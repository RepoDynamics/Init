import time
import re

from pylinks.exceptions import WebAPIError
from github_contexts import GitHubContext
from github_contexts.github.payloads.pull_request import PullRequestPayload
from github_contexts.github.enums import ActionType
import conventional_commits
from actionman.logger import Logger, LogLevel

import repodynamics.control.content
import repodynamics.control.content
import repodynamics.control.content.manager
from repodynamics import control
from repodynamics.control.manager import ControlCenterManager
from repodynamics.action.events._base import EventHandler
from repodynamics.path import RelativePath
from repodynamics.version import PEP440SemVer
from repodynamics.datatype import (
    EventType,
    Label,
    PrimaryActionCommit,
    PrimaryCustomCommit,
    PrimaryActionCommitType,
    CommitGroup,
    BranchType,
    IssueStatus,
    TemplateType,
    RepoFileType,
    InitCheckAction,
    LabelType,
)
from repodynamics.control.content import from_json_file
from repodynamics.control import ControlCenterContentManager
from repodynamics.action._changelog import ChangelogManager


class PullRequestEventHandler(EventHandler):

    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str,
        logger: Logger,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
            logger=logger
        )
        self._payload: PullRequestPayload = self._context.event
        self._pull = self._payload.pull_request
        self._branch_base = self.resolve_branch(self._context.base_ref)
        self._logger.entry(
            LogStatus.PASS,
            "Resolve base branch",
            f"Resolved base branch as a branch of type '{self._branch_base.type.value}'.",
            str(self._branch_base),
        )
        self._branch_head = self.resolve_branch(self._context.head_ref)
        self._logger.entry(
            LogStatus.PASS,
            "Resolve head branch",
            f"Resolved head branch as a branch of type '{self._branch_head.type.value}'.",
            str(self._branch_head),
        )
        self._git_base.fetch_remote_branches_by_name(branch_names=self._context.base_ref)
        self._git_base.checkout(branch=self._context.base_ref)
        self._primary_commit_type: PrimaryActionCommit | PrimaryCustomCommit | None = None
        return

    def _run_event(self):
        if not self._head_to_base_allowed():
            return
        action = self._payload.action
        if action is ActionType.OPENED:
            self._run_action_opened()
        elif action is ActionType.REOPENED:
            self._run_action_reopened()
        elif action is ActionType.SYNCHRONIZE:
            self._run_action_synchronize()
        elif action is ActionType.LABELED:
            self._run_action_labeled()
        elif action is ActionType.READY_FOR_REVIEW:
            self._run_action_ready_for_review()
        else:
            self.error_unsupported_triggering_action()
        return

    def _run_action_opened(self):
        if self._branch_head.type is BranchType.PRERELEASE and self._branch_base.type in (
            BranchType.MAIN,
            BranchType.RELEASE,
        ):
            return self._run_open_prerelease_to_release()
        return

    def _run_action_reopened(self):
        return

    def _run_action_synchronize(self):
        new_body = self._add_to_pr_timeline(
            entry=(
                "New commits were pushed to the head branch "
                f"(workflow run: [{self._context.run_id}]({self._gh_link.workflow_run(run_id=self._context.run_id)}), "
                f"actor: @{self._payload.sender.login})."
            )
        )
        meta_and_hooks_action_type = InitCheckAction.COMMIT if self._payload.internal else InitCheckAction.FAIL
        meta = ControlCenterManager(
            path_repo=self._path_root_head,
            github_token=self._context.token,
            ccm_before=self._ccm_main,
            logger=self._logger,
        )
        changed_file_groups = self._action_file_change_detector(meta=meta)
        hash_hooks = self._action_hooks(
            action=meta_and_hooks_action_type,
            branch=self._branch_head,
            base=False,
            ref_range=(self._context.hash_before, self._context.hash_after),
        )
        for file_type in (RepoFileType.SUPERMETA, RepoFileType.META, RepoFileType.DYNAMIC):
            if changed_file_groups[file_type]:
                hash_meta = self._action_meta(
                    action=meta_and_hooks_action_type, meta=meta, base=False, branch=self._branch_head
                )
                ccm_branch = meta.generate_data()
                break
        else:
            hash_meta = None
            ccm_branch = from_json_file(
                path_repo=self._path_root_base, git=self._git_head, logger=self._logger
            )
        latest_hash = self._git_head.push() if hash_hooks or hash_meta else self._context.hash_after

        tasks_complete = self._update_implementation_tasklist(body=new_body)
        if tasks_complete and not self._failed:
            self._gh_api.pull_update(
                number=self._pull.number,
                draft=False,
            )
        final_commit_type = self._ccm_main.get_issue_data_from_labels(self._pull.label_names).group_data
        job_runs = self._determine_job_runs(
            changed_file_groups=changed_file_groups, final_commit_type=final_commit_type
        )
        if job_runs["package_publish_testpypi"]:
            next_ver = self._calculate_next_dev_version(final_commit_type=final_commit_type)
            job_runs["version"] = str(next_ver)
            self._tag_version(
                ver=next_ver,
                base=False,
                msg=f"Developmental release (issue: #{self._branch_head.suffix[0]}, target: {self._branch_base.name})",
            )
        self._set_output(
            ccm_branch=ccm_branch,
            ref=latest_hash,
            ref_before=self._context.hash_before,
            **job_runs,
        )
        return

    def _run_action_labeled(self):
        label = self._ccm_main.resolve_label(self._payload.label.name)
        if label.category is LabelType.STATUS:
            self._primary_commit_type = self._ccm_main.get_issue_data_from_labels(self._pull.label_names).group_data
            if not self._status_label_allowed(label=label):
                return
            self._update_issue_status_labels(
                issue_nr=self._pull.number,
                labels=self._ccm_main.resolve_labels(self._pull.label_names)[LabelType.STATUS],
                current_label=label,
            )
            status = label.type
            if status in (IssueStatus.DEPLOY_ALPHA, IssueStatus.DEPLOY_BETA, IssueStatus.DEPLOY_RC):
                if self._branch_base.type in (BranchType.RELEASE, BranchType.MAIN):
                    return self._run_create_prerelease_from_implementation(status=status)
                if self._branch_base.type is BranchType.PRERELEASE:
                    return self._run_merge_implementation_to_prerelease(status=status)
            elif status is IssueStatus.DEPLOY_FINAL:
                self._run_action_labeled_status_final()
        return

    def _run_action_ready_for_review(self):
        return

    def _run_action_labeled_status_final(self):
        if self._branch_head.type is BranchType.AUTOUPDATE:
            return self._run_merge_autoupdate()
        elif self._branch_head.type is BranchType.DEV:
            return self._run_merge_development_to_implementation()
        elif self._branch_head.type is BranchType.IMPLEMENT:
            if self._payload.internal:
                if self._branch_base.type in (BranchType.RELEASE, BranchType.MAIN):
                    return self._run_merge_implementation_to_release()
                elif self._branch_base.type is BranchType.PRERELEASE:
                    return self._run_merge_implementation_to_prerelease(status=IssueStatus.DEPLOY_FINAL)
                else:
                    self._logger.error(
                        "Merge not allowed",
                        f"Merge from a head branch of type '{self._branch_head.type.value}' "
                        f"to a branch of type '{self._branch_base.type.value}' is not allowed.",
                    )
            else:
                if self._branch_base.type is BranchType.IMPLEMENT:
                    return self._run_merge_fork_to_implementation()
                else:
                    self._logger.error(
                        "Merge not allowed",
                        f"Merge from a head branch of type '{self._branch_head.type.value}' "
                        f"to a branch of type '{self._branch_base.type.value}' is not allowed.",
                    )
        elif self._branch_head.type is BranchType.PRERELEASE:
            if self._branch_base.type in (BranchType.RELEASE, BranchType.MAIN):
                return self._run_merge_pre_to_release()
            else:
                self._logger.error(
                    "Merge not allowed",
                    f"Merge from a head branch of type '{self._branch_head.type.value}' "
                    f"to a branch of type '{self._branch_base.type.value}' is not allowed.",
                )
        else:
            self._logger.error(
                "Merge not allowed",
                f"Merge from a head branch of type '{self._branch_head.type.value}' "
                f"to a branch of type '{self._branch_base.type.value}' is not allowed.",
            )

    def _run_open_prerelease_to_release(self):
        main_dev_protocol, sub_dev_protocols = self._read_prerelease_dev_protocols()
        self._gh_api.issue_comment_create(number=self._pull.number, body=sub_dev_protocols)
        self._gh_api.pull_update(
            number=self._pull.number,
            body=main_dev_protocol,
        )
        original_issue_nr = self._get_originating_issue_nr(body=main_dev_protocol)
        issue_labels = self._ccm_main.resolve_labels(
            names=[label["name"] for label in self._gh_api.issue_labels(number=original_issue_nr)]
        )
        label_names_to_add = [
            label.name for label in issue_labels[LabelType.TYPE] + issue_labels[LabelType.SUBTYPE]
        ]
        self._gh_api.issue_labels_add(number=self._pull.number, labels=label_names_to_add)
        return

    def _run_merge_prerelease_to_release(self):
        self._run_merge_implementation_to_release()
        return

    def _run_upgrade_prerelease(self):
        return

    def _run_merge_implementation_to_release(self):
        primary_commit_type, ver_base, next_ver, ver_dist = self._get_next_ver_dist()
        hash_base = self._git_base.commit_hash_normal()
        changelog_manager = self._update_changelogs(
            ver_dist=ver_dist,
            commit_type=primary_commit_type.conv_type,
            commit_title=self._pull.title,
            hash_base=hash_base,
            prerelease=False,
        )

        self._git_head.commit(
            message=f'{self._ccm_main["commit"]["secondary_action"]["auto-update"]["type"]}: Update changelogs',
            stage="all"
        )

        if (
            self._branch_base.type is BranchType.MAIN
            and ver_base.major > 0
            and primary_commit_type.group is CommitGroup.PRIMARY_ACTION
            and primary_commit_type.action is PrimaryActionCommitType.RELEASE_MAJOR
        ):
            self._git_base.checkout(
                branch=self.create_branch_name_release(major_version=ver_base.major), create=True
            )
            self._git_base.push(target="origin", set_upstream=True)
            self._git_base.checkout(branch=self._branch_base.name)

        # Update the metadata in main branch to reflect the new release
        if next_ver:
            if self._branch_base.type is BranchType.MAIN:
                meta_gen = ControlCenterManager(
                    path_repo=self._path_root_head,
                    github_token=self._context.token,
                    ccm_before=self._ccm_main,
                    future_versions={self._branch_base.name: next_ver},
                    logger=self._logger,
                )
                self._action_meta(
                    action=InitCheckAction.COMMIT, meta=meta_gen, base=False, branch=self._branch_head
                )
            else:
                self._git_base.checkout(branch=self._payload.repository.default_branch)
                meta_gen = ControlCenterManager(
                    path_repo=self._path_root_base,
                    github_token=self._context.token,
                    future_versions={self._branch_base.name: next_ver},
                    logger=self._logger,
                )
                self._action_meta(
                    action=InitCheckAction.COMMIT, meta=meta_gen, base=True, branch=self._branch_base
                )
                self._git_base.push()
                self._git_base.checkout(branch=self._branch_base.name)

        latest_hash = self._git_head.push()
        # Wait 30 s to make sure the push to head is registered
        time.sleep(30)

        merge_response = self._merge_pull(conv_type=primary_commit_type.conv_type, sha=latest_hash)
        if not merge_response:
            return

        ccm_branch = repodynamics.control.content.from_json_file(
            path_repo=self._path_root_head, logger=self._logger
        )
        hash_latest = merge_response["sha"]
        if not next_ver:
            self._set_output(
                ccm_branch=ccm_branch,
                ref=hash_latest,
                ref_before=hash_base,
                website_deploy=True,
                package_lint=True,
                package_test=True,
                package_build=True,
            )
            return

        for i in range(10):
            self._git_base.pull()
            if self._git_base.commit_hash_normal() == hash_latest:
                break
            time.sleep(5)
        else:
            self._logger.error("Failed to pull changes from GitHub. Please pull manually.")
            self._failed = True
            return

        tag = self._tag_version(ver=next_ver, base=True)
        self._set_output(
            ccm_branch=ccm_branch,
            ref=hash_latest,
            ref_before=hash_base,
            version=str(next_ver),
            release_name=f"{ccm_branch['name']} v{next_ver}",
            release_tag=tag,
            release_body=changelog_manager.get_entry(changelog_id="package_public")[0],
            website_deploy=True,
            package_lint=True,
            package_test=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
            package_release=True,
        )
        return

    def _run_create_prerelease_from_implementation(self, status: IssueStatus):
        ver_base, dist_base = self._get_latest_version(base=True)
        next_ver_final = self._get_next_version(ver_base, self._primary_commit_type.action)
        pre_segment = {
            IssueStatus.DEPLOY_ALPHA: "a",
            IssueStatus.DEPLOY_BETA: "b",
            IssueStatus.DEPLOY_RC: "rc",
        }[status]
        next_ver_pre = PEP440SemVer(f"{next_ver_final}.{pre_segment}{self._branch_head.suffix[0]}")
        pre_release_branch_name = self.create_branch_name_prerelease(version=next_ver_pre)
        self._git_base.checkout(branch=pre_release_branch_name, create=True)
        self._git_base.commit(
            message=(
                f"init: Create pre-release branch '{pre_release_branch_name}' "
                f"from base branch '{self._branch_base.name}'."
            ),
            allow_empty=True,
        )
        self._git_base.push(target="origin", set_upstream=True)
        # Wait 30 s to make sure the push of the new base branch is registered
        time.sleep(30)
        self._gh_api.pull_update(number=self._pull.number, base=pre_release_branch_name)
        hash_base = self._git_base.commit_hash_normal()
        changelog_manager = self._update_changelogs(
            ver_dist=str(next_ver_pre),
            commit_type=self._primary_commit_type.conv_type,
            commit_title=self._pull.title,
            hash_base=hash_base,
            prerelease=True,
        )
        self._write_prerelease_dev_protocol(ver=str(next_ver_pre))
        self._git_head.commit(
            message="auto: Update changelogs",
            stage="all"
        )
        latest_hash = self._git_head.push()
        # Wait 30 s to make sure the push to head is registered
        time.sleep(30)
        merge_response = self._merge_pull(conv_type=self._primary_commit_type.conv_type, sha=latest_hash)
        if not merge_response:
            return
        hash_latest = merge_response["sha"]
        for i in range(10):
            self._git_base.pull()
            if self._git_base.commit_hash_normal() == hash_latest:
                break
            time.sleep(5)
        else:
            self._logger.error("Failed to pull changes from GitHub. Please pull manually.")
            self._failed = True
            return
        tag = self._tag_version(ver=next_ver_pre, base=True)
        ccm_branch = repodynamics.control.content.from_json_file(
            path_repo=self._path_root_head, logger=self._logger
        )
        self._set_output(
            ccm_branch=ccm_branch,
            ref=hash_latest,
            ref_before=hash_base,
            version=str(next_ver_pre),
            release_name=f"{ccm_branch['name']} v{next_ver_pre}",
            release_tag=tag,
            release_body=changelog_manager.get_entry(changelog_id="package_public_prerelease")[0],
            release_prerelease=True,
            website_deploy=True,
            package_lint=True,
            package_test=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
            package_release=True,
        )
        return

    def _run_merge_implementation_to_prerelease(self, status: IssueStatus):
        primary_commit_type, ver_base, next_ver, ver_dist = self._get_next_ver_dist(prerelease_status=status)
        return

    def _run_merge_development_to_implementation(self):
        tasklist_head = self._extract_tasklist(body=self._pull.body)
        if not tasklist_head or len(tasklist_head) != 1:
            self._logger.error(
                "Failed to find tasklist",
                "Failed to find tasklist in pull request body.",
            )
            self._failed = True
            return
        task = tasklist_head[0]

        matching_pulls = self._gh_api.pull_list(
            state="open",
            head=f"{self._context.repository_owner}:{self._context.base_ref}",
        )
        if not matching_pulls or len(matching_pulls) != 1:
            self._logger.error(
                "Failed to find matching pull request",
                "Failed to find matching pull request for the development branch.",
            )
            self._failed = True
            return
        parent_pr = self._gh_api.pull(number=matching_pulls[0]["number"])

        tasklist_base = self._extract_tasklist(body=parent_pr["body"])
        task_nr = self._branch_head.suffix[2]
        tasklist_base[task_nr - 1] = task
        self._update_tasklist(entries=tasklist_base, body=parent_pr["body"], number=parent_pr["number"])
        response = self._gh_api_admin.pull_merge(
            number=self._pull.number,
            commit_title=task["summary"],
            commit_message=self._pull.body,
            sha=self._pull.head.sha,
            merge_method="squash",
        )
        return

    def _run_merge_autoupdate(self):
        return

    def _run_merge_fork_to_implementation(self):
        return

    def _run_merge_fork_to_development(self):
        return

    def _merge_pull(self, conv_type: str,  sha: str | None = None) -> dict | None:
        bare_title = self._pull.title.removeprefix(f'{conv_type}: ')
        commit_title = f"{conv_type}: {bare_title}"
        try:
            response = self._gh_api_admin.pull_merge(
                number=self._pull.number,
                commit_title=commit_title,
                commit_message=self._pull.body,
                sha=sha,
                merge_method="squash",
            )
        except WebAPIError as e:
            self._gh_api.pull_update(
                number=self._pull.number,
                title=commit_title,
            )
            self._logger.error(
                "Failed to merge pull request using GitHub API. Please merge manually.", e, raise_error=False
            )
            self._failed = True
            return
        return response

    def _update_changelogs(
        self, ver_dist: str, commit_type: str, commit_title: str, hash_base: str, prerelease: bool = False
    ):
        parser = conventional_commits.parser.create(
            types=self._ccm_main.get_all_conventional_commit_types(secondary_custom_only=True),
        )
        changelog_manager = ChangelogManager(
            changelog_metadata=self._ccm_main["changelog"],
            ver_dist=ver_dist,
            commit_type=commit_type,
            commit_title=commit_title,
            parent_commit_hash=hash_base,
            parent_commit_url=self._gh_link.commit(hash_base),
            path_root=self._path_root_head,
            logger=self._logger,
        )
        tasklist = self._extract_tasklist(body=self._pull.body)
        for task in tasklist:
            conv_msg = parser.parse(message=task["summary"])
            if conv_msg:
                group_data = self._ccm_main.get_commit_type_from_conventional_type(conv_type=conv_msg.type)
                if prerelease:
                    if group_data.changelog_id != "package_public":
                        continue
                    changelog_id = "package_public_prerelease"
                else:
                    changelog_id = group_data.changelog_id
                changelog_manager.add_change(
                    changelog_id=changelog_id,
                    section_id=group_data.changelog_section_id,
                    change_title=conv_msg.description,
                    change_details=task["description"],
                )
        changelog_manager.write_all_changelogs()
        return changelog_manager

    def _get_next_ver_dist(self, prerelease_status: IssueStatus | None = None):
        ver_base, dist_base = self._get_latest_version(base=True)
        primary_commit_type = self._ccm_main.get_issue_data_from_labels(self._pull.label_names).group_data
        if self._primary_type_is_package_publish(commit_type=primary_commit_type):
            if prerelease_status:
                next_ver = "?"
            else:
                next_ver = self._get_next_version(ver_base, primary_commit_type.action)
                ver_dist = str(next_ver)
        else:
            ver_dist = f"{ver_base}+{dist_base + 1}"
            next_ver = None
        return primary_commit_type, ver_base, next_ver, ver_dist

    def _determine_job_runs(self, changed_file_groups, final_commit_type):
        package_setup_files_changed = any(
            filepath in changed_file_groups[RepoFileType.DYNAMIC]
            for filepath in (
                RelativePath.file_python_pyproject,
                RelativePath.file_python_manifest,
            )
        )
        out = {
            "website_build": (
                bool(changed_file_groups[RepoFileType.WEBSITE])
                or bool(changed_file_groups[RepoFileType.PACKAGE])
            ),
            "package_test": (
                bool(changed_file_groups[RepoFileType.TEST])
                or bool(changed_file_groups[RepoFileType.PACKAGE])
                or package_setup_files_changed
            ),
            "package_build": bool(changed_file_groups[RepoFileType.PACKAGE]) or package_setup_files_changed,
            "package_lint": bool(changed_file_groups[RepoFileType.PACKAGE]) or package_setup_files_changed,
            "package_publish_testpypi": (
                self._branch_head.type is BranchType.IMPLEMENT
                and self._payload.internal
                and (bool(changed_file_groups[RepoFileType.PACKAGE]) or package_setup_files_changed)
                and self._primary_type_is_package_publish(commit_type=final_commit_type)
            ),
        }
        return out

    def _calculate_next_dev_version(self, final_commit_type):
        ver_last_base, _ = self._get_latest_version(dev_only=False, base=True)
        ver_last_head, _ = self._get_latest_version(dev_only=True, base=False)
        if ver_last_base.pre:
            # The base branch is a pre-release branch
            next_ver = ver_last_base.next_post
            if not ver_last_head or (
                ver_last_head.release != next_ver.release or ver_last_head.pre != next_ver.pre
            ):
                dev = 0
            else:
                dev = (ver_last_head.dev or -1) + 1
            next_ver_str = f"{next_ver}.dev{dev}"
        else:
            next_ver = self._get_next_version(ver_last_base, final_commit_type.action)
            next_ver_str = str(next_ver)
            if final_commit_type.action != PrimaryActionCommitType.RELEASE_POST:
                next_ver_str += f".a{self._branch_head.suffix[0]}"
            if not ver_last_head:
                dev = 0
            elif final_commit_type.action == PrimaryActionCommitType.RELEASE_POST:
                if ver_last_head.post is not None and ver_last_head.post == next_ver.post:
                    dev = ver_last_head.dev + 1
                else:
                    dev = 0
            elif ver_last_head.pre is not None and ver_last_head.pre == ("a", self._branch_head.suffix[0]):
                dev = ver_last_head.dev + 1
            else:
                dev = 0
            next_ver_str += f".dev{dev}"
        return PEP440SemVer(next_ver_str)

    def _update_implementation_tasklist(self, body: str | None = None) -> bool:

        def apply(commit_details, tasklist_entries):
            for entry in tasklist_entries:
                if entry['complete'] or entry['summary'].casefold() != commit_details[0].casefold():
                    continue
                if (
                    not entry['sublist']
                    or len(commit_details) == 1
                    or commit_details[1].casefold() not in [subentry['summary'].casefold() for subentry in entry['sublist']]
                ):
                    entry['complete'] = True
                    return
                apply(commit_details[1:], entry['sublist'])
            return

        def update_complete(tasklist_entries):
            for entry in tasklist_entries:
                if entry['sublist']:
                    entry['complete'] = update_complete(entry['sublist'])
            return all([entry['complete'] for entry in tasklist_entries])

        commits = self._get_commits()
        tasklist = self._extract_tasklist(body=self._pull.body)
        if not tasklist:
            return False
        for commit in commits:
            commit_details = (
                commit.msg.splitlines() if commit.group_data.group == CommitGroup.NON_CONV
                else [commit.msg.summary, *commit.msg.body.strip().splitlines()]
            )
            apply(commit_details, tasklist)
        complete = update_complete(tasklist)
        self._update_tasklist(tasklist, body=body)
        return complete

    def _update_tasklist(
        self,
        entries: list[dict[str, bool | str | list]],
        body: str | None = None,
        number: int | None = None,
    ) -> None:
        """
        Update the implementation tasklist in the pull request body.

        Parameters
        ----------
        entries : list[dict[str, bool | str | list]]
            A list of dictionaries, each representing a tasklist entry.
            The format of each dictionary is the same as that returned by
            `_extract_tasklist_entries`.
        """
        tasklist_string = self._write_tasklist(entries)
        pattern = rf"({self._MARKER_TASKLIST_START}).*?({self._MARKER_TASKLIST_END})"
        replacement = rf"\1\n{tasklist_string}\n\2"
        new_body = re.sub(pattern, replacement, body or self._pull.body, flags=re.DOTALL)
        self._gh_api.pull_update(
            number=number or self._pull.number,
            body=new_body,
        )
        return

    def _add_to_pr_timeline(self, entry: str) -> str:
        return self._add_to_timeline(entry=entry, body=self._pull.body, issue_nr=self._pull.number)

    def _write_prerelease_dev_protocol(self, ver: str):
        filepath = self._path_root_head / self._ccm_main["issue"]["dev_protocol"]["prerelease_temp_path"]
        filepath.parent.mkdir(parents=True, exist_ok=True)
        old_title = f'# {self._ccm_main["issue"]["dev_protocol"]["template"]["title"]}'
        new_title = f"{old_title} (v{ver})"
        entry = self._pull.body.strip().replace(old_title, new_title, 1)
        with open(filepath, "a") as f:
            f.write(f"\n\n{entry}\n")
        return

    def _read_prerelease_dev_protocols(self) -> tuple[str, str]:
        filepath = self._path_root_head / self._ccm_main["issue"]["dev_protocol"]["prerelease_temp_path"]
        protocols = filepath.read_text().strip()
        main_protocol, sub_protocols = protocols.split("\n# ", 1)
        return main_protocol.strip(), f"# {sub_protocols.strip()}"

    def _get_originating_issue_nr(self, body: str | None = None) -> int:
        pattern = rf"{self._MARKER_ISSUE_NR_START}(.*?){self._MARKER_ISSUE_NR_END}"
        match = re.search(pattern, body or self._pull.body, flags=re.DOTALL)
        issue_nr = match.group(1).strip().removeprefix("#")
        return int(issue_nr)

    def _head_to_base_allowed(self) -> bool:
        internal_head_to_base_map = {
            BranchType.PRERELEASE: (BranchType.MAIN, BranchType.RELEASE),
            BranchType.IMPLEMENT: (BranchType.MAIN, BranchType.RELEASE, BranchType.PRERELEASE),
            BranchType.DEV: (BranchType.IMPLEMENT,),
            BranchType.AUTOUPDATE: (BranchType.MAIN, BranchType.RELEASE, BranchType.PRERELEASE),
        }
        external_head_to_base_map = {
            BranchType.IMPLEMENT: (BranchType.IMPLEMENT,),
            BranchType.DEV: (BranchType.DEV,),
        }
        mapping = internal_head_to_base_map if self._payload.internal else external_head_to_base_map
        allowed_base_types = mapping.get(self._branch_head.type)
        if not allowed_base_types:
            self._error_unsupported_head()
            return False
        if self._branch_base.type not in allowed_base_types:
            self._error_unsupported_head_to_base()
            return False
        return True

    def _status_label_allowed(self, label: Label):
        if label.type not in (
            IssueStatus.DEPLOY_ALPHA,
            IssueStatus.DEPLOY_BETA,
            IssueStatus.DEPLOY_RC,
            IssueStatus.DEPLOY_FINAL
        ):
            self._error_unsupported_status_label()
            return False
        if label.type is not IssueStatus.DEPLOY_FINAL and (
            self._branch_head.type, self._branch_base.type
        ) not in (
            (BranchType.PRERELEASE, BranchType.MAIN),
            (BranchType.PRERELEASE, BranchType.RELEASE),
            (BranchType.IMPLEMENT, BranchType.MAIN),
            (BranchType.IMPLEMENT, BranchType.RELEASE),
        ):
            self._error_unsupported_prerelease_status_label()
            return False
        if label.type is not IssueStatus.DEPLOY_FINAL and not self._primary_type_is_package_publish(
            commit_type=self._primary_commit_type, include_post_release=False
        ):
            self._error_unsupported_prerelease_status_label_for_primary_type()
            return False
        if self._branch_head.type is BranchType.PRERELEASE and label.type is not IssueStatus.DEPLOY_FINAL:
            head_prerelease_segment = self._branch_head.suffix.pre[0]
            label_prerelease_segment = {
                IssueStatus.DEPLOY_ALPHA: "a",
                IssueStatus.DEPLOY_BETA: "b",
                IssueStatus.DEPLOY_RC: "rc",
            }[label.type]
            if label_prerelease_segment < head_prerelease_segment:
                self._error_unsupported_prerelease_status_label_for_prerelease_branch()
                return False
        return True

    def _error_unsupported_head(self):
        err_msg = "Unsupported pull request head branch."
        err_details = (
            f"Pull requests from a head branch of type '{self._branch_head.type.value}' "
            f"are not allowed for {'internal' if self._payload.internal else 'external'} pull requests."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    def _error_unsupported_head_to_base(self):
        err_msg = "Unsupported pull request base branch."
        err_details = (
            f"Pull requests from a head branch of type '{self._branch_head.type.value}' "
            f"to a base branch of type '{self._branch_base.type.value}' "
            f"are not allowed for {'internal' if self._payload.internal else 'external'} pull requests."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    def _error_unsupported_status_label(self):
        err_msg = "Unsupported pull request status label."
        err_details = (
            f"Status label '{self._payload.label.name}' is not supported for pull requests."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    def _error_unsupported_prerelease_status_label(self):
        err_msg = "Unsupported pull request status label."
        err_details = (
            f"Status label '{self._payload.label.name}' is not supported for pull requests "
            f"from a head branch of type '{self._branch_head.type.value}' "
            f"to a base branch of type '{self._branch_base.type.value}'."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    def _error_unsupported_prerelease_status_label_for_primary_type(self):
        err_msg = "Unsupported pull request status label."
        err_details = (
            f"Status label '{self._payload.label.name}' is not supported for pull requests "
            f"with primary types other than major, minor, or patch releases."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    def _error_unsupported_prerelease_status_label_for_prerelease_branch(self):
        err_msg = "Unsupported pull request status label."
        err_details = (
            f"Status label '{self._payload.label.name}' is not supported for pull requests "
            f"from a head branch of type '{self._branch_head.type.value}' "
            f"with a lower pre-release segment than the label."
        )
        self._logger.error(f"Pull Request Event Handler: {err_msg}", err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=err_msg,
            details=err_details,
        )
        return

    @staticmethod
    def _primary_type_is_package_publish(
        commit_type: PrimaryActionCommit | PrimaryCustomCommit, include_post_release: bool = True
    ):
        actions = [
            PrimaryActionCommitType.RELEASE_MAJOR,
            PrimaryActionCommitType.RELEASE_MINOR,
            PrimaryActionCommitType.RELEASE_PATCH,
        ]
        if include_post_release:
            actions.append(PrimaryActionCommitType.RELEASE_POST)
        return commit_type.group is CommitGroup.PRIMARY_ACTION and commit_type.action in actions
