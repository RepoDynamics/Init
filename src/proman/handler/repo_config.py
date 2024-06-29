from typing import Literal

from pylinks.exceptions import WebAPIError
from pylinks.api.github import Repo as GitHubRepoAPI
from loggerman import logger
from controlman import ControlCenterContentManager
from controlman.content import ControlCenterContent
from controlman.content.dev.label import LabelType, FullLabel
from controlman.content.dev.branch import (
    BranchProtectionRuleset,
    RulesetEnforcementLevel,
    RulesetBypassActorType,
    RulesetBypassMode,
)


class RepoConfig:

    def __init__(self, gh_api: GitHubRepoAPI, default_branch_name: str):
        self._gh_api = gh_api
        self._default_branch_name = default_branch_name
        return

    @logger.sectioner("Update Repository Configurations")
    def update_all(
        self,
        ccm_new: ControlCenterContentManager,
        ccm_old: ControlCenterContentManager | None = None,
        rulesets: Literal["create", "update", "ignore"] = "update",
    ):
        self.update_settings(ccm=ccm_new)
        self.update_gh_pages(ccm=ccm_new)
        self.update_branch_names(ccs_new=ccm_new.content, ccs_old=ccm_old.content)
        self.update_labels(ccs_new=ccm_new.content, ccs_old=ccm_old.content)
        if rulesets != "ignore":
            self.update_rulesets(
                ccs_new=ccm_new.content, ccs_old=ccm_old.content if rulesets == "update" else None
            )
        return

    @logger.sectioner("Update Repository Settings")
    def update_settings(self, ccm: ControlCenterContentManager):
        """Update repository settings.

        Notes
        -----
        - The GitHub API Token must have write access to 'Administration' scope.
        """
        self._gh_api.actions_permissions_workflow_default_set(can_approve_pull_requests=True)
        data = ccm.repo__config | {
            "has_issues": True,
            "allow_squash_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
        }
        topics = data.pop("topics")
        self._gh_api.repo_update(**data)
        self._gh_api.repo_topics_replace(topics=topics)
        return

    @logger.sectioner("Activate GitHub Pages")
    def activate_gh_pages(self):
        """Activate GitHub Pages for the repository if not activated.

        Notes
        -----
        - The GitHub API Token must have write access to 'Pages' scope.
        """
        if not self._gh_api.info["has_pages"]:
            self._gh_api.pages_create(build_type="workflow")
        return

    @logger.sectioner("Update GitHub Pages Settings")
    def update_gh_pages(self, ccm: ControlCenterContentManager) -> None:
        """Activate GitHub Pages if not activated, and update custom domain.

        Notes
        -----
        - The GitHub API Token must have write access to 'Pages' scope.
        """
        self.activate_gh_pages()
        cname = ccm.web__base_url
        try:
            self._gh_api.pages_update(
                cname=cname.removeprefix("https://").removeprefix("http://") if cname else "",
                build_type="workflow",
            )
        except WebAPIError as e:
            logger.notice(f"Failed to update custom domain for GitHub Pages", str(e))
        if cname:
            try:
                self._gh_api.pages_update(https_enforced=cname.startswith("https://"))
            except WebAPIError as e:
                logger.notice(f"Failed to update HTTPS enforcement for GitHub Pages", str(e))
        return

    @logger.sectioner("Reset Repository Labels")
    def reset_labels(self, ccs: ControlCenterContent | None = None):
        for label in self._gh_api.labels:
            self._gh_api.label_delete(label["name"])
        for label in ccs.dev.label.full_labels:
            self._gh_api.label_create(name=label.name, description=label.description, color=label.color)
        return

    @logger.sectioner("Update Repository Labels")
    def update_labels(
        self,
        ccs_new: ControlCenterContent,
        ccs_old: ControlCenterContent,
    ):

        def format_labels(
            labels: tuple[FullLabel]
        ) -> tuple[
            dict[tuple[LabelType, str, str], FullLabel],
            dict[tuple[LabelType, str, str], FullLabel],
            dict[tuple[LabelType, str, str], FullLabel],
            dict[tuple[LabelType, str, str], FullLabel],
        ]:
            full = {}
            version = {}
            branch = {}
            rest = {}
            for label in labels:
                key = (label.type, label.group_name, label.id)
                full[key] = label
                if label.type is LabelType.AUTO_GROUP:
                    if label.group_name == "version":
                        version[key] = label
                    else:
                        branch[key] = label
                else:
                    rest[key] = label
            return full, version, branch, rest

        labels_old, labels_old_ver, labels_old_branch, labels_old_rest = format_labels(
            ccs_old.dev.label.full_labels
        )
        labels_new, labels_new_ver, labels_new_branch, labels_new_rest = format_labels(
            ccs_new.dev.label.full_labels
        )

        ids_old = set(labels_old.keys())
        ids_new = set(labels_new.keys())

        current_label_names = [label['name'] for label in self._gh_api.labels]

        # Update labels that are in both old and new settings,
        #   when their label data has changed in new settings.
        ids_shared = ids_old & ids_new
        for id_shared in ids_shared:
            old_label = labels_old[id_shared]
            new_label = labels_new[id_shared]
            if old_label.name not in current_label_names:
                self._gh_api.label_create(
                    name=new_label.name, color=new_label.color, description=new_label.description
                )
                continue
            if old_label != new_label:
                self._gh_api.label_update(
                    name=old_label.name,
                    new_name=new_label.name,
                    description=new_label.description,
                    color=new_label.color,
                )
        # Add new labels
        ids_added = ids_new - ids_old
        for id_added in ids_added:
            label = labels_new[id_added]
            self._gh_api.label_create(name=label.name, color=label.color, description=label.description)
        # Delete old non-auto-group (i.e., not version or branch) labels
        ids_old_rest = set(labels_old_rest.keys())
        ids_new_rest = set(labels_new_rest.keys())
        ids_deleted_rest = ids_old_rest - ids_new_rest
        for id_deleted in ids_deleted_rest:
            self._gh_api.label_delete(labels_old_rest[id_deleted].name)
        # Update old branch and version labels
        for label_data_new, label_data_old, labels_old in (
            (ccs_new.dev.label.branch, ccs_old.dev.label.branch, labels_old_branch),
            (ccs_new.dev.label.version, ccs_old.dev.label.version, labels_old_ver),
        ):
            if label_data_new != label_data_old:
                for label_old in labels_old.values():
                    label_old_suffix = label_old.name.removeprefix(label_data_old.prefix)
                    self._gh_api.label_update(
                        name=label_old.name,
                        new_name=f"{label_data_new.prefix}{label_old_suffix}",
                        color=label_data_new.color,
                        description=label_data_new.description,
                    )
        return

    @logger.sectioner("Update Repository Branch Names")
    def update_branch_names(
        self,
        ccs_new: ControlCenterContent,
        ccs_old: ControlCenterContent,
    ) -> dict:
        """Update all branch names.

        Notes
        -----
        - The GitHub API Token must have write access to 'Administration' scope.
        """
        old = ccs_old.dev.branch
        new = ccs_new.dev.branch
        old_to_new_map = {}
        if new.main.name != self._default_branch_name:
            self._gh_api.branch_rename(old_name=self._default_branch_name, new_name=new.main.name)
            old_to_new_map[self._default_branch_name] = new.main.name
        branches = self._gh_api.branches
        branch_names = [branch["name"] for branch in branches]
        old_groups = old.groups
        new_groups = new.groups
        for group_type, group_data in new_groups.items():
            prefix_new = group_data.prefix
            prefix_old = old_groups[group_type].prefix
            if prefix_old != prefix_new:
                for branch_name in branch_names:
                    if branch_name.startswith(prefix_old):
                        new_name = f"{prefix_new}{branch_name.removeprefix(prefix_old)}"
                        self._gh_api.branch_rename(old_name=branch_name, new_name=new_name)
                        old_to_new_map[branch_name] = new_name
        return old_to_new_map

    @logger.sectioner("Update Repository Rulesets")
    def update_rulesets(
        self,
        ccs_new: ControlCenterContent,
        ccs_old: ControlCenterContent | None = None
    ) -> None:
        """Update branch and tag protection rulesets."""
        enforcement = {
            RulesetEnforcementLevel.ENABLED: 'active',
            RulesetEnforcementLevel.DISABLED: 'disabled',
            RulesetEnforcementLevel.EVALUATE: 'evaluate',
        }
        bypass_actor_type = {
            RulesetBypassActorType.ORG_ADMIN: 'OrganizationAdmin',
            RulesetBypassActorType.REPO_ROLE: 'RepositoryRole',
            RulesetBypassActorType.TEAM: 'Team',
            RulesetBypassActorType.INTEGRATION: 'Integration',
        }
        bypass_actor_mode = {
            RulesetBypassMode.ALWAYS: True,
            RulesetBypassMode.PULL: False,
        }

        def apply(
            name: str,
            target: Literal['branch', 'tag'],
            pattern: list[str],
            ruleset: BranchProtectionRuleset,
        ) -> None:
            args = {
                'name': name,
                'target': target,
                'enforcement': enforcement[ruleset.enforcement],
                'bypass_actors': [
                    (actor.id, bypass_actor_type[actor.type], bypass_actor_mode[actor.mode])
                    for actor in ruleset.bypass_actors
                ],
                'ref_name_include': pattern,
                'creation': ruleset.rule.protect_creation,
                'update': ruleset.rule.protect_modification,
                'update_allows_fetch_and_merge': ruleset.rule.modification_allows_fetch_and_merge,
                'deletion': ruleset.rule.protect_deletion,
                'required_linear_history': ruleset.rule.require_linear_history,
                'required_deployment_environments': ruleset.rule.required_deployment_environments,
                'required_signatures': ruleset.rule.require_signatures,
                'required_pull_request': ruleset.rule.require_pull_request,
                'dismiss_stale_reviews_on_push': ruleset.rule.dismiss_stale_reviews_on_push,
                'require_code_owner_review': ruleset.rule.require_code_owner_review,
                'require_last_push_approval': ruleset.rule.require_last_push_approval,
                'required_approving_review_count': ruleset.rule.required_approving_review_count,
                'required_review_thread_resolution': ruleset.rule.require_review_thread_resolution,
                'required_status_checks': [
                    (
                        (context.name, context.integration_id) if context.integration_id is not None
                        else context.name
                    )
                    for context in ruleset.rule.status_check_contexts
                ],
                'strict_required_status_checks_policy': ruleset.rule.status_check_strict_policy,
                'non_fast_forward': ruleset.rule.protect_force_push,
            }
            if not ccs_old:
                self._gh_api.ruleset_create(**args)
                return
            for existing_ruleset in existing_rulesets:
                if existing_ruleset['name'] == name:
                    args["ruleset_id"] = existing_ruleset["id"]
                    args["require_status_checks"] = ruleset.rule.require_status_checks
                    self._gh_api.ruleset_update(**args)
                    return
            self._gh_api.ruleset_create(**args)
            return

        if ccs_old:
            existing_rulesets = self._gh_api.rulesets(include_parents=False)

        if not ccs_old or ccs_old.dev.branch.main != ccs_new.dev.branch.main:
            apply(
                name='Branch: main',
                target='branch',
                pattern=["~DEFAULT_BRANCH"],
                ruleset=ccs_new.dev.branch.main.ruleset,
            )
        groups_new = ccs_new.dev.branch.groups
        groups_old = ccs_old.dev.branch.groups if ccs_old else {}
        for group_type, group_data in groups_new.items():
            if not ccs_old or group_data != groups_old[group_type]:
                apply(
                    name=f"Branch Group: {group_type.value}",
                    target='branch',
                    pattern=[f"refs/heads/{group_data.prefix}**/**/*"],
                    ruleset=group_data.ruleset,
                )
        return
