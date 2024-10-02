from typing import Literal

import pyserials as _ps
from pyserials import NestedDict as _NestedDict
from pylinks.exception.api import WebAPIError
from pylinks.api.github import Repo as GitHubRepoAPI
from loggerman import logger
import mdit
import pycolorit as pcit


class RepoConfig:

    def __init__(
        self,
        gh_api: GitHubRepoAPI,
        gh_api_admin: GitHubRepoAPI,
        default_branch_name: str
    ):
        self._gh_api = gh_api
        self._gh_api_admin = gh_api_admin
        self._default_branch_name = default_branch_name
        return

    @logger.sectioner("GitHub Repository Configuration")
    def update_all(
        self,
        data_new: _NestedDict,
        data_old: _NestedDict | None = None,
        rulesets: Literal["create", "update", "ignore"] = "update",
    ):
        self.update_settings(data=data_new)
        self.update_gh_pages(data=data_new)
        self.update_branch_names(data_new=data_new, data_old=data_old)
        self.update_labels(data_new=data_new, data_old=data_old)
        if rulesets != "ignore":
            self.update_rulesets(
                data_new=data_new, data_old=data_old if rulesets == "update" else None
            )
        return

    def update_settings(self, data: _NestedDict):
        """Update repository settings.

        Notes
        -----
        - The GitHub API Token must have write access to 'Administration' scope.
        """
        self._gh_api_admin.actions_permissions_workflow_default_set(can_approve_pull_requests=True)
        repo_config = {
            k: v for k, v in data.get("repo", {}).items() if k not in (
                "topics", "gitattributes", "gitignore",
                "id", "node_id", "name", "full_name",
                "created_at", "default_branch", "url",
            )
        }
        if repo_config:
            results = self._gh_api_admin.repo_update(**repo_config)
            logger.success(
                "Repository Settings",
                "Updated repository settings.",
                mdit.element.code_block(
                    content=_ps.write.to_yaml_string(results),
                    language="yaml",
                    caption="Settings",
                )
            )
        topics = data["repo.topics"]
        if topics:
            self._gh_api_admin.repo_topics_replace(topics=topics)
            logger.success(
                "Repository Topics",
                "Updated repository topics.",
                mdit.element.code_block(
                    content=_ps.write.to_yaml_string(topics),
                    language="yaml",
                    caption="Topics",
                )
            )
        return

    def activate_gh_pages(self):
        """Activate GitHub Pages for the repository if not activated.

        Notes
        -----
        - The GitHub API Token must have write access to 'Pages' scope.
        """
        if not self._gh_api_admin.info["has_pages"]:
            results = self._gh_api_admin.pages_create(build_type="workflow")
            logger.success(
                "GitHub Pages Activation",
                "GitHub Pages has been activated for the repository.",
                mdit.element.code_block(
                    content=_ps.write.to_yaml_string(results),
                    language="yaml",
                    caption="GitHub Pages Details",
                )
            )
        return

    def update_gh_pages(self, data: _NestedDict) -> None:
        """Activate GitHub Pages if not activated, and update custom domain.

        Notes
        -----
        - The GitHub API Token must have write access to 'Pages' scope.
        """
        self.activate_gh_pages()
        cname = data.get("web.url.custom.name", "").removeprefix("https://").removeprefix("http://")
        try:
            self._gh_api_admin.pages_update(
                cname=cname,
                build_type="workflow",
            )
            logger.success(
                "GitHub Pages Custom Domain",
                mdit.inline_container(
                    "Updated custom domain for GitHub Pages to ",
                    mdit.element.code_span(cname),
                )
            )
        except WebAPIError as e:
            if cname:
                logger.error(
                    "GitHub Pages Custom Domain",
                    f"Failed to update custom domain for GitHub Pages",
                    logger.traceback(e)
                )
        if cname:
            try:
                self._gh_api_admin.pages_update(https_enforced=data["web.url.custom.enforce_https"])
                logger.success(
                    "GitHub Pages HTTPS Enforcement",
                    mdit.inline_container(
                        "Updated HTTPS enforcement for GitHub Pages to ",
                        mdit.element.code_span(data["web.url.custom.enforce_https"]),
                    )
                )
            except WebAPIError as e:
                logger.error(
                    "GitHub Pages HTTPS Enforcement",
                    f"Failed to update HTTPS enforcement for GitHub Pages",
                    logger.traceback(e)
                )
        return

    @logger.sectioner("Repository Labels Reset")
    def reset_labels(self, data: _NestedDict | None = None):
        current_labels = self._gh_api.labels
        for current_label in current_labels:
            self._gh_api.label_delete(current_label["name"])
        logger.success(
            "Deleted Labels",
            "All current repository labels have been deleted:",
            self._make_labels_table(current_labels, "Deleted Labels"),
        )
        for label in data["label.all"]:
            label_color = pcit.color.css(label["color"]).css_hex().removeprefix("#")
            self._gh_api.label_create(name=label["name"], description=label["description"], color=label_color)
        logger.success(
            "Created Labels",
            "Following labels have been created:",
            self._make_labels_table(self._gh_api.labels, "Created Labels"),
        )
        return

    @staticmethod
    def _make_labels_table(labels: list[dict], caption: str):
        rows = [["Name", "ID", "Node ID", "URL", "Default", "Description", "Color"]]
        for label in labels:
            rows.append(
                [
                    label["name"],
                    label["id"],
                    label["node_id"],
                    label["url"],
                    label["default"],
                    label["description"],
                    label["color"],
                ]
            )
        return mdit.element.table(rows=rows, caption=caption, num_rows_header=1)

    @logger.sectioner("Labels")
    def update_labels(self, data_new: _NestedDict, data_old: _NestedDict):

        def format_labels(labels: list[dict]) -> tuple[
            dict[tuple[str, str, str], dict],
            dict[tuple[str, str, str], dict],
            dict[tuple[str, str, str], dict],
            dict[tuple[str, str, str], dict],
        ]:
            full = {}
            version = {}
            branch = {}
            rest = {}
            for label in labels:
                key = (label["type"], label["group_name"], label["id"])
                full[key] = label
                if label["type"] == "auto":
                    if label["group_name"] == "version":
                        version[key] = label
                    else:
                        branch[key] = label
                else:
                    rest[key] = label
            return full, version, branch, rest

        labels_old, labels_old_ver, labels_old_branch, labels_old_rest = format_labels(data_old.get("label.full", []))
        labels_new, labels_new_ver, labels_new_branch, labels_new_rest = format_labels(data_new.get("label.full", []))

        ids_old = set(labels_old.keys())
        ids_new = set(labels_new.keys())

        current_label_names = [label['name'] for label in self._gh_api.labels]

        # Update labels that are in both old and new settings,
        # when their label data has changed in new settings.
        ids_shared = ids_old & ids_new
        for id_shared in ids_shared:
            old_label = labels_old[id_shared]
            new_label = labels_new[id_shared]
            if old_label["name"] not in current_label_names:
                self._gh_api.label_create(
                    name=new_label["name"], color=new_label["color"], description=new_label["description"]
                )
                continue
            if old_label != new_label:
                self._gh_api.label_update(
                    name=old_label["name"],
                    new_name=new_label["name"],
                    description=new_label["description"],
                    color=new_label["color"],
                )
        # Add new labels
        ids_added = ids_new - ids_old
        for id_added in ids_added:
            label = labels_new[id_added]
            self._gh_api.label_create(name=label["name"], color=label["color"], description=label["description"])
        # Delete old non-auto-group (i.e., not version or branch) labels
        ids_old_rest = set(labels_old_rest.keys())
        ids_new_rest = set(labels_new_rest.keys())
        ids_deleted_rest = ids_old_rest - ids_new_rest
        for id_deleted in ids_deleted_rest:
            self._gh_api.label_delete(labels_old_rest[id_deleted]["name"])
        # Update old branch and version labels
        for label_data_new, label_data_old, labels_old in (
            (data_new["label.branch"], data_old["label.branch"], labels_old_branch),
            (data_new["label.version"], data_old["label.version"], labels_old_ver),
        ):
            if label_data_new != label_data_old:
                for label_old in labels_old.values():
                    label_old_suffix = label_old["name"].removeprefix(label_data_old["prefix"])
                    self._gh_api.label_update(
                        name=label_old["name"],
                        new_name=f"{label_data_new['prefix']}{label_old_suffix}",
                        color=label_data_new["color"],
                        description=label_data_new["description"],
                    )
        return

    @logger.sectioner("Branch Names")
    def update_branch_names(
        self,
        data_new: _NestedDict,
        data_old: _NestedDict,
    ) -> dict:
        """Update all branch names.

        Notes
        -----
        - The GitHub API Token must have write access to 'Administration' scope.
        """
        old_to_new_map = {}
        new_default_branch_name = data_new["branch.main.name"]
        if new_default_branch_name != self._default_branch_name:
            self._gh_api.branch_rename(old_name=self._default_branch_name, new_name=new_default_branch_name)
            old_to_new_map[self._default_branch_name] = new_default_branch_name
        branches = self._gh_api.branches
        branch_names = [branch["name"] for branch in branches]
        for branch_key in ("release", "pre", "dev", "auto"):
            old_prefix = data_old[f"branch.{branch_key}.name"]
            new_prefix = data_new[f"branch.{branch_key}.name"]
            if old_prefix == new_prefix:
                continue
            for branch_name in branch_names:
                if branch_name.startswith(old_prefix):
                    new_branch_name = f"{new_prefix}{branch_name.removeprefix(old_prefix)}"
                    self._gh_api.branch_rename(old_name=branch_name, new_name=new_branch_name)
                    old_to_new_map[branch_name] = new_branch_name
        return old_to_new_map

    @logger.sectioner("Rulesets")
    def update_rulesets(
        self,
        data_new: _NestedDict,
        data_old: _NestedDict | None = None
    ) -> None:
        """Update branch and tag protection rulesets."""
        bypass_actor_map = {
            "organization_admin": (1, "OrganizationAdmin"),
            "repository_admin": (5, "RepositoryRole"),
            "repository_maintainer": (2, "RepositoryRole"),
            "repository_writer": (4, "RepositoryRole"),
        }
        bypass_actor_type = {
            "organization_admin": 'OrganizationAdmin',
            "repository_role": 'RepositoryRole',
            "team": 'Team',
            "integration": 'Integration',
        }
        bypass_actor_mode = {"always": True, "pull_request": False}

        def apply(
            name: str,
            target: Literal['branch', 'tag'],
            pattern: list[str],
            ruleset: dict,
        ) -> None:
            bypass_actors = []
            for actor in ruleset["bypass_actors"]:
                if actor.get("role"):
                    actor_id, actor_type = bypass_actor_map[actor["role"]]
                else:
                    actor_id, actor_type = actor["id"], bypass_actor_type[actor["type"]]
                bypass_actors.append((actor_id, actor_type, bypass_actor_mode[actor["mode"]]))
            pr = ruleset.get("require_pull_request", {})
            status_check = ruleset.get("require_status_checks", {})
            required_status_checks = []
            for context in status_check.get("contexts", []):
                to_append = [context["name"]]
                if context.get("integration_id"):
                    to_append.append(context["integration_id"])
                required_status_checks.append(tuple(to_append))
            args = {
                'name': name,
                'target': target,
                'enforcement': ruleset["enforcement"],
                'bypass_actors': bypass_actors,
                'ref_name_include': pattern,
                'creation': ruleset["protect_creation"],
                'update': "protect_modification" in ruleset,
                'update_allows_fetch_and_merge': ruleset.get("protect_modification", {}).get("allow_fetch_and_merge"),
                'deletion': ruleset["protect_deletion"],
                'required_linear_history': ruleset["require_linear_history"],
                'required_deployment_environments': ruleset.get("required_deployment_environments", []),
                'required_signatures': ruleset["require_signatures"],
                'required_pull_request': bool(pr),
                'dismiss_stale_reviews_on_push': pr.get("dismiss_stale_reviews_on_push"),
                'require_code_owner_review': pr.get("require_code_owner_review"),
                'require_last_push_approval': pr.get("require_last_push_approval"),
                'required_approving_review_count': pr.get("required_approving_review_count"),
                'required_review_thread_resolution': pr.get("require_review_thread_resolution"),
                'required_status_checks': required_status_checks,
                'strict_required_status_checks_policy': status_check.get("strict"),
                'non_fast_forward': ruleset["protect_force_push"],
            }
            for existing_ruleset in existing_rulesets:
                if existing_ruleset['name'] == name:
                    args["ruleset_id"] = existing_ruleset["id"]
                    args["require_status_checks"] = bool(status_check)
                    new_ruleset = self._gh_api_admin.ruleset_update(**args)
                    logger.success(
                        "Ruleset Update",
                        f"Updated ruleset: {name}",
                        mdit.element.code_block(
                            content=_ps.write.to_yaml_string(new_ruleset),
                            language="yaml",
                            caption="New Ruleset",
                        )
                    )
                    return
            new_ruleset = self._gh_api_admin.ruleset_create(**args)
            logger.success(
                "Ruleset Creation",
                f"Created ruleset: {name}",
                mdit.element.code_block(
                    content=_ps.write.to_yaml_string(new_ruleset),
                    language="yaml",
                    caption="New Ruleset",
                )
            )
            return

        existing_rulesets = self._gh_api_admin.rulesets(include_parents=False)

        for branch_key in ("main", "release", "pre", "dev", "auto"):
            branch_name = data_new[f"branch.{branch_key}.name"]
            branch_ruleset = data_new[f"branch.{branch_key}.ruleset"]
            ruleset_name = "Branch: main" if branch_key == "main" else f"Branch Group: {branch_key}"
            if not branch_ruleset:
                for existing_ruleset in existing_rulesets:
                    if existing_ruleset['name'] == ruleset_name:
                        self._gh_api_admin.ruleset_delete(ruleset_id=existing_ruleset["id"])
                        logger.success(
                            "Ruleset Deletion",
                            f"Deleted branch ruleset: {ruleset_name}",
                        )
                continue
            apply(
                name=ruleset_name,
                target='branch',
                pattern=["~DEFAULT_BRANCH" if branch_key == "main" else f"refs/heads/{branch_name}**/**/*"],
                ruleset=branch_ruleset,
            )
        return
