"""Main event handler."""

from __future__ import annotations

from pathlib import Path
import json
from typing import TYPE_CHECKING
import re

import jinja2
from loggerman import logger
import mdit
import htmp
import pylinks
from pylinks.exception.api import WebAPIError as _WebAPIError
import pyserials as _ps
from github_contexts.github import enum as _ghc_enum
import conventional_commits
import pkgdata
import controlman
from proman.datatype import (
    InitCheckAction, Branch,
    Commit, NonConventionalCommit, Label, PrimaryActionCommitType
)
import gittidy
from versionman.pep440_semver import PEP440SemVer

from proman.datatype import FileChangeType, RepoFileType, BranchType
from proman.repo_config import RepoConfig
from proman import hook_runner
from proman.data_manager import DataManager
from proman import change_detector
from proman.exception import ProManException

if TYPE_CHECKING:
    from typing import Literal
    from github_contexts import GitHubContext
    from proman.reporter import Reporter
    from proman.output_writer import OutputWriter
    from github_contexts.github.payload.object.user import User as GitHubUser


class EventHandler:

    _REPODYNAMICS_BOT_USER = ("RepoDynamicsBot", "146771514+RepoDynamicsBot@users.noreply.github.com")

    _MARKER_COMMIT_START = "<!-- Begin primary commit summary -->"
    _MARKER_COMMIT_END = "<!-- End primary commit summary -->"
    _MARKER_TASKLIST_START = "<!-- Begin secondary commits tasklist -->"
    _MARKER_TASKLIST_END = "<!-- End secondary commits tasklist -->"
    _MARKER_REFERENCES_START = "<!-- Begin references -->"
    _MARKER_REFERENCES_END = "<!-- End references -->"
    _MARKER_TIMELINE_START = "<!-- Begin timeline -->"
    _MARKER_TIMELINE_END = "<!-- End timeline -->"
    _MARKER_ISSUE_NR_START = "<!-- Begin issue number -->"
    _MARKER_ISSUE_NR_END = "<!-- End issue number -->"

    def __init__(
        self,
        github_context: GitHubContext,
        reporter: Reporter,
        output_writer: OutputWriter,
        admin_token: str | None,
        path_repo_base: str,
        path_repo_head: str,
    ):

        @logger.sectioner("GitHub API Initialization")
        def init_github_api():
            repo_user = self._context.repository_owner
            repo_name = self._context.repository_name
            link_gen = pylinks.site.github.user(repo_user).repo(repo_name)
            api_admin, api_actions = (
                pylinks.api.github(token=token).user(repo_user).repo(repo_name)
                for token in (admin_token, self._context.token)
            )
            log_title = "Admin Token Verification"
            if not admin_token:
                has_admin_token = False
                if in_repo_creation_event():
                    logger.info(
                        log_title,
                        "Repository creation event detected; no admin token required.",
                    )
                elif self._context.event.repository.fork:
                    logger.info(
                        log_title,
                        "Forked repository detected; no admin token required.",
                    )
                else:
                    logger.critical(
                        log_title,
                        "No admin token provided.",
                    )
                    reporter.add(
                        name="main",
                        status="fail",
                        summary="No admin token provided.",
                    )
                    raise ProManException()
            else:
                try:
                    api_admin.info
                except _WebAPIError as e:
                    details = e.report.body
                    details.extend(*e.report.section["details"].content.body.elements())
                    logger.critical(
                        log_title,
                        "Failed to verify the provided admin token.",
                        details
                    )
                    reporter.add(
                        name="main",
                        status="fail",
                        summary="Failed to verify the provided admin token.",
                        section=mdit.document(
                            heading="Admin Token Verification",
                            body=details.elements(),
                        )
                    )
                    raise ProManException()
                has_admin_token = True
                logger.success(
                    log_title,
                    "Admin token verified successfully.",
                )
            return api_admin, api_actions, link_gen, has_admin_token

        @logger.sectioner("Git API Initialization")
        def init_git_api() -> tuple[gittidy.Git, gittidy.Git]:
            apis = []
            for path, title in ((path_repo_base, "Base Repo"), (path_repo_head, "Head Repo")):
                with logger.sectioning(title):
                    git_api = gittidy.Git(
                        path=path,
                        user=(self._context.event.sender.login, self._context.event.sender.github_email),
                        user_scope="global",
                        committer=self._REPODYNAMICS_BOT_USER,
                        committer_scope="local",
                        committer_persistent=True,
                        logger=logger,
                    )
                    apis.append(git_api)
            return tuple(apis)

        @logger.sectioner("Metadata Load")
        def load_metadata() -> tuple[DataManager | None, DataManager | None]:

            def load(path, name: str):
                log_title = f"{name.capitalize()} Repo Metadata Load"
                err_msg = f"Failed to load metadata from the {name} repository."
                try:
                    data = DataManager(controlman.from_json_file(repo_path=path))
                except controlman.exception.load.ControlManInvalidMetadataError as e:
                    logger.critical(
                        log_title,
                        err_msg,
                        e.report.body["problem"].content,
                    )
                    reporter.add(
                        name="main",
                        status="fail",
                        summary="Failed to load metadata from the base repository.",
                        section="",
                    )
                    raise ProManException()
                except controlman.exception.load.ControlManSchemaValidationError as e:
                    logger.critical(
                        log_title,
                        err_msg,
                        e.report.body["problem"].content,
                    )
                    reporter.add(
                        name="main",
                        status="fail",
                        summary="Failed to load metadata from the base repository.",
                        section="",
                    )
                    raise ProManException()
                else:
                    logger.success(
                        log_title,
                        "Metadata loaded successfully.",
                    )
                    return data

            if in_repo_creation_event():
                logger.info(
                    "Metadata Load",
                    "Repository creation event detected; no metadata to load.",
                )
                return None, None

            data_main = load(path=self._path_base, name="base")
            data_branch_before = data_main if self._context.ref_is_main else load(path=self._path_head, name="head")
            return data_main, data_branch_before

        def in_repo_creation_event():
            return (
                self._context.event_name is _ghc_enum.EventType.PUSH
                and self._context.ref_type is _ghc_enum.RefType.BRANCH
                and self._context.event.action is _ghc_enum.ActionType.CREATED
                and self._context.ref_is_main
            )

        self._context = github_context
        self._reporter = reporter
        self._output = output_writer
        self._gh_api_admin, self._gh_api, self._gh_link, self._has_admin_token = init_github_api()
        self._git_base, self._git_head = init_git_api()
        self._path_base = self._git_base.repo_path
        self._path_head = self._git_head.repo_path
        self._data_main, self._data_branch_before = load_metadata()
        self._repo_config = RepoConfig(
            gh_api=self._gh_api,
            gh_api_admin=self._gh_api_admin,
            default_branch_name=self._context.event.repository.default_branch
        )
        self._ver = pkgdata.get_version_from_caller()
        self._data_branch: DataManager | None = None
        self._failed = False
        self._branch_name_memory_autoupdate: str | None = None
        return

    def run(self) -> None:
        ...

    def run_sync_fix(
        self,
        action: InitCheckAction,
        future_versions: dict | None = None,
        testpypi_publishable: bool = False,
    ) -> tuple[DataManager, dict[str, bool], str]:

        def decide_jobs():

            def decide(filetypes: list[RepoFileType]):
                return any(filetype in changed_filetypes for filetype in filetypes)
            package_changed = decide([RepoFileType.PKG_SOURCE, RepoFileType.PKG_CONFIG])
            test_changed = decide([RepoFileType.TEST_SOURCE, RepoFileType.TEST_CONFIG])
            website_changed = decide(
                [
                    RepoFileType.CC, RepoFileType.WEB_CONFIG, RepoFileType.WEB_SOURCE,
                    RepoFileType.THEME, RepoFileType.PKG_SOURCE,
                ]
            )
            return {
                "website_build": website_changed,
                "package_test": package_changed or test_changed,
                "package_build": package_changed,
                "package_lint": package_changed,
                "package_publish_testpypi": package_changed and testpypi_publishable,
            }

        changed_filetypes = self._detect_changes()
        if any(filetype in changed_filetypes for filetype in (RepoFileType.CC, RepoFileType.DYNAMIC)):
            cc_manager = self.get_cc_manager(future_versions=future_versions)
            hash_sync = self._sync(action=action, cc_manager=cc_manager, base=False)
            data = DataManager(cc_manager.generate_data())
        else:
            hash_sync = None
            data = self._data_branch_before
        hash_hooks = self._action_hooks(
            action=action,
            data=data,
            base=False,
            ref_range=(self._context.hash_before, self._context.hash_after),
        ) if data["tool.pre-commit.config.file.content"] else None
        if hash_hooks or hash_sync:
            with logger.sectioning("Repository Update"):
                self._git_head.push()
        latest_hash = hash_hooks or hash_sync or self._context.hash_after
        job_runs = decide_jobs()
        return data, job_runs, latest_hash

    @logger.sectioner("File Change Detector")
    def _detect_changes(self) -> tuple[RepoFileType, ...]:
        changes = self._git_head.changed_files(
            ref_start=self._context.hash_before, ref_end=self._context.hash_after
        )
        full_info = change_detector.detect(data=self._data_branch_before, changes=changes)
        changed_filetypes = {}
        rows = [["Type", "Subtype", "Change", "Dynamic", "Path"]]
        for typ, subtype, change_type, is_dynamic, path in sorted(full_info, key=lambda x: (x[0].value, x[1])):
            changed_filetypes.setdefault(typ, []).append(change_type)
            if is_dynamic:
                changed_filetypes.setdefault(RepoFileType.DYNAMIC, []).append(change_type)
            dynamic = htmp.element.span('✅' if is_dynamic else '❌', title='Dynamic' if is_dynamic else 'Static')
            change_sig = change_type.value
            change = htmp.element.span(change_sig.emoji, title=change_sig.title)
            subtype = subtype or Path(path).stem
            rows.append([typ.value, subtype, change, dynamic, mdit.element.code_span(path)])
        if not changed_filetypes:
            oneliner = "No files were changed in this event."
            body = None
        else:
            changed_types = ", ".join(sorted([typ.value for typ in changed_filetypes]))
            oneliner = f"Following filetypes were changed: {changed_types}"
            body = mdit.element.unordered_list()
            intro_table_rows = [["Type", "Changes"]]
            has_broken_changes = False
            if RepoFileType.DYNAMIC in changed_filetypes:
                warning = "⚠️ Dynamic files were changed; make sure to double-check that everything is correct."
                body.append(warning)
            for file_type, change_list in changed_filetypes.items():
                change_list = sorted(set(change_list), key=lambda x: x.value.title)
                changes = []
                for change_type in change_list:
                    if change_type in (FileChangeType.BROKEN, FileChangeType.UNKNOWN):
                        has_broken_changes = True
                    changes.append(
                        f'<span title="{change_type.value.title}">{change_type.value.emoji}</span>'
                    )
                changes_cell = "&nbsp;".join(changes)
                intro_table_rows.append([file_type.value, changes_cell])
            if has_broken_changes:
                warning = "⚠️ Some changes were marked as 'broken' or 'unknown'; please investigate."
                body.append(warning)
            intro_table = mdit.element.table(intro_table_rows, num_rows_header=1)
            body.append(["Following filetypes were changed:", intro_table])
            body.append(["Following files were changed:", mdit.element.table(rows, num_rows_header=1)])
        self._reporter.add(
            name="file_change",
            status="pass",
            summary=oneliner,
            body=body,
        )
        return tuple(changed_filetypes.keys())

    @logger.sectioner("CCA")
    def _sync(
        self,
        action: InitCheckAction,
        cc_manager: controlman.CenterManager,
        base: bool,
        commit_msg: str | None = None,
    ) -> str | None:
        if action == InitCheckAction.NONE:
            self._reporter.add(
                name="cca",
                status="skip",
                summary="CCA is disabled for this event.",
            )
            logger.info("CCA Disabled", "CCA is disabled for this event.")
            return
        git = self._git_base if base else self._git_head
        if action == InitCheckAction.PULL:
            pr_branch_name = self.switch_to_autoupdate_branch(typ="meta", git=git)
        try:
            reporter = cc_manager.report()
        except controlman.exception.ControlManException as e:
            self._reporter.add(
                name="cca",
                status="fail",
                summary=e.report.body["intro"].content,
                body=e.report.body,
                section=e.report.section,
                section_is_container=True,
            )
            raise ProManException()
        # Push/pull if changes are made and action is not 'fail' or 'report'
        commit_hash = None
        report = reporter.report()
        summary = report.body["summary"].content
        if reporter.has_changes and action not in [InitCheckAction.FAIL, InitCheckAction.REPORT]:
            with logger.sectioning("Synchronization"):
                cc_manager.apply_changes()
                commit_msg = commit_msg or conventional_commits.message.create(
                    typ=self._data_main["commit.auto.sync.type"],
                    description="Sync dynamic files with control center configurations.",
                )
                commit_hash_before = git.commit_hash_normal()
                commit_hash_after = git.commit(
                    message=str(commit_msg),
                    stage="all",
                    amend=(action == InitCheckAction.AMEND),
                )
                commit_hash = self._action_hooks(
                    action=InitCheckAction.AMEND,
                    data=cc_manager.generate_data(),
                    base=base,
                    ref_range=(commit_hash_before, commit_hash_after),
                    internal=True,
                ) or commit_hash_after
                description = "These were synced and changes were applied to "
                if action == InitCheckAction.PULL:
                    git.push(target="origin", set_upstream=True)
                    pull_data = self._gh_api_admin.pull_create(
                        head=pr_branch_name,
                        base=self._branch_name_memory_autoupdate,
                        title=commit_msg.summary,
                        body=commit_msg.body,
                    )
                    self.switch_back_from_autoupdate_branch(git=git)
                    commit_hash = None
                    link = f'[#{pull_data["number"]}]({pull_data["url"]})'
                    description += f"branch {htmp.element.code(pr_branch_name)} in PR {link}."
                else:
                    link = f"[`{commit_hash[:7]}`]({self._gh_link.commit(commit_hash)})"
                    description += "the current branch " + (
                        f"in commit {link}."
                        if action == InitCheckAction.COMMIT
                        else f"by amending the latest commit (new hash: {link})."
                    )
                summary += f" {description}"
        self._reporter.add(
            name="cca",
            status="fail" if reporter.has_changes and action in [
               InitCheckAction.FAIL,
               InitCheckAction.REPORT,
               InitCheckAction.PULL
            ] else "pass",
            summary=summary,
            section=report.section,
            section_is_container=True,
        )
        return commit_hash

    @logger.sectioner("Hooks")
    def _action_hooks(
        self,
        action: InitCheckAction,
        data: _ps.NestedDict,
        base: bool,
        ref_range: tuple[str, str] | None = None,
        internal: bool = False,
    ) -> str | None:
        if action == InitCheckAction.NONE:
            self._reporter.add(
                name="hooks",
                status="skip",
                summary="Hooks are disabled for this event type.",
            )
            return
        config = data["tool.pre-commit.config.file.content"]
        if not config:
            if not internal:
                oneliner = "Hooks are enabled but no pre-commit config set in <code>$.tool.pre-commit.config.file.content</code>."
                logger.error(oneliner)
                self._reporter.add(
                    name="hooks",
                    status="fail",
                    summary=oneliner,
                )
            return
        input_action = (
            action
            if action in [InitCheckAction.REPORT, InitCheckAction.AMEND, InitCheckAction.COMMIT]
            else (InitCheckAction.REPORT if action == InitCheckAction.FAIL else InitCheckAction.COMMIT)
        )
        commit_msg = (
            conventional_commits.message.create(
                typ=self._data_main["commit.auto.maintain.type"],
                description="Apply automatic fixes made by workflow hooks.",
            )
            if action in [InitCheckAction.COMMIT, InitCheckAction.PULL]
            else ""
        )
        git = self._git_base if base else self._git_head
        if action == InitCheckAction.PULL:
            pr_branch = self.switch_to_autoupdate_branch(typ="hooks", git=git)
        try:
            hooks_output = hook_runner.run(
                git=git,
                ref_range=ref_range,
                action=input_action.value,
                commit_message=str(commit_msg),
                config=config,
            )
        except ProManException as e:
            pass
        passed = hooks_output["passed"]
        modified = hooks_output["modified"]
        commit_hash = None
        # Push/amend/pull if changes are made and action is not 'fail' or 'report'
        summary_addon_template = " The modifications made during the first run were applied to {target}."
        if action == InitCheckAction.PULL and modified:
            git.push(target="origin", set_upstream=True)
            pull_data = self._gh_api_admin.pull_create(
                head=pr_branch,
                base=self._branch_name_memory_autoupdate,
                title=commit_msg.summary,
                body=commit_msg.body,
            )
            self.switch_back_from_autoupdate_branch(git=git)
            link = htmp.element.a(pull_data["number"], href=pull_data["url"])
            target = f"branch <code>{pr_branch}</code> and a pull request ({link}) was created"
            hooks_output["summary"] += summary_addon_template.format(target=target)
        if action in [InitCheckAction.COMMIT, InitCheckAction.AMEND] and modified:
            commit_hash = hooks_output["commit_hash"]
            link = htmp.element.a(commit_hash[:7], href=str(self._gh_link.commit(commit_hash)))
            target = "the current branch " + (
                f"in a new commit (hash: {link})"
                if action == InitCheckAction.COMMIT
                else f"by amending the latest commit (new hash: {link})"
            )
            hooks_output["summary"] += summary_addon_template.format(target=target)
        if not internal:
            self._reporter.add(
                name="hooks",
                status="fail" if not passed or (action == InitCheckAction.PULL and modified) else "pass",
                summary=hooks_output["summary"],
                body=hooks_output["body"],
                section=hooks_output["section"],
            )
        return commit_hash

    def get_cc_manager(
        self,
        base: bool = False,
        data_before: _ps.NestedDict | None = None,
        data_main: _ps.NestedDict | None = None,
        future_versions: dict[str, str | PEP440SemVer] | None = None,
        control_center_path: str | None = None,
        log_title: str = "Control Center Manager Initialization",
    ) -> controlman.CenterManager:
        with logger.sectioning(log_title):
            return controlman.manager(
                repo=self._git_base if base else self._git_head,
                data_before=data_before or self._data_branch_before,
                data_main=data_main or self._data_main,
                github_token=self._context.token,
                future_versions=future_versions,
                control_center_path=control_center_path,
            )

    def _get_latest_version(
        self,
        branch: str | None = None,
        dev_only: bool = False,
        base: bool = True,
    ) -> tuple[PEP440SemVer | None, int | None]:

        def get_latest_version() -> PEP440SemVer | None:
            tags_lists = git.get_tags()
            if not tags_lists:
                return
            for tags_list in tags_lists:
                ver_tags = []
                for tag in tags_list:
                    if tag.startswith(ver_tag_prefix):
                        ver_tags.append(PEP440SemVer(tag.removeprefix(ver_tag_prefix)))
                if ver_tags:
                    if dev_only:
                        ver_tags = sorted(ver_tags, reverse=True)
                        for ver_tag in ver_tags:
                            if ver_tag.release_type == "dev":
                                return ver_tag
                    else:
                        return max(ver_tags)
            return

        git = self._git_base if base else self._git_head
        ver_tag_prefix = self._data_main["tag.version.prefix"]
        if branch:
            git.stash()
            curr_branch = git.current_branch_name()
            git.checkout(branch=branch)
        latest_version = get_latest_version()
        distance = git.get_distance(
            ref_start=f"refs/tags/{ver_tag_prefix}{latest_version.input}"
        ) if latest_version else None
        if branch:
            git.checkout(branch=curr_branch)
            git.stash_pop()
        if not latest_version and not dev_only:
            logger.error(f"No matching version tags found with prefix '{ver_tag_prefix}'.")
        return latest_version, distance

    def _get_commits(self, base: bool = False) -> list[Commit]:
        git = self._git_base if base else self._git_head
        commits = git.get_commits(f"{self._context.hash_before}..{self._context.hash_after}")
        logger.info("Read commits from git history", json.dumps(commits, indent=4))
        parser = conventional_commits.parser.create(
            types=self._data_main.get_all_conventional_commit_types(secondary_only=False),
        )
        parsed_commits = []
        for commit in commits:
            conv_msg = parser.parse(message=commit["msg"])
            if not conv_msg:
                parsed_commits.append(
                    Commit(
                        **commit, group_data=NonConventionalCommit()
                    )
                )
            else:
                group = self._data_main.get_commit_type_from_conventional_type(conv_type=conv_msg.type)
                commit["msg"] = conv_msg
                parsed_commits.append(Commit(**commit, group_data=group))
        return parsed_commits

    def _tag_version(self, ver: str | PEP440SemVer, base: bool, msg: str = "") -> str:
        tag_prefix = self._data_main["tag.version.prefix"]
        tag = f"{tag_prefix}{ver}"
        if not msg:
            msg = f"Release version {ver}"
        git = self._git_base if base else self._git_head
        git.create_tag(tag=tag, message=msg)
        return tag

    def _update_issue_status_labels(
        self, issue_nr: int, labels: list[Label], current_label: Label
    ) -> None:
        for label in labels:
            if label.name != current_label.name:
                self._gh_api.issue_labels_remove(number=issue_nr, label=label.name)
        return

    def resolve_branch(self, branch_name: str | None = None) -> Branch:
        if not branch_name:
            branch_name = self._context.ref_name
        if branch_name == self._context.event.repository.default_branch:
            return Branch(type=BranchType.MAIN, name=branch_name)
        for branch_type, branch_data in self._data_main["branch"].items():
            if branch_name.startswith(branch_data["name"]):
                branch_type = BranchType(branch_type)
                suffix_raw = branch_name.removeprefix(branch_data["name"])
                if branch_type is BranchType.RELEASE:
                    suffix = int(suffix_raw)
                elif branch_type is BranchType.PRE:
                    suffix = PEP440SemVer(suffix_raw)
                elif branch_type is BranchType.DEV:
                    issue_num, target_branch = suffix_raw.split("/", 1)
                    suffix = (int(issue_num), target_branch)
                else:
                    suffix = suffix_raw
                return Branch(type=branch_type, name=branch_name, prefix=branch_data["name"], suffix=suffix)
        return Branch(type=BranchType.OTHER, name=branch_name)

    def _extract_tasklist(self, body: str) -> list[dict[str, bool | str | list]]:
        """
        Extract the implementation tasklist from the pull request body.

        Returns
        -------
        A list of dictionaries, each representing a tasklist entry.
        Each dictionary has the following keys:
        - complete : bool
            Whether the task is complete.
        - summary : str
            The summary of the task.
        - description : str
            The description of the task.
        - sublist : list[dict[str, bool | str | list]]
            A list of dictionaries, each representing a subtask entry, if any.
            Each dictionary has the same keys as the parent dictionary.
        """

        def extract(tasklist_string: str, level: int = 0) -> list[dict[str, bool | str | list]]:
            # Regular expression pattern to match each task item
            task_pattern = rf'{" " * level * 2}- \[(X| )\] (.+?)(?=\n{" " * level * 2}- \[|\Z)'
            # Find all matches
            matches = re.findall(task_pattern, tasklist_string, flags=re.DOTALL)
            # Process each match into the required dictionary format
            tasklist_entries = []
            for match in matches:
                complete, summary_and_desc = match
                summary_and_desc_split = summary_and_desc.split('\n', 1)
                summary = summary_and_desc_split[0]
                description = summary_and_desc_split[1] if len(summary_and_desc_split) > 1 else ''
                if description:
                    sublist_pattern = r'^( *- \[(?:X| )\])'
                    parts = re.split(sublist_pattern, description, maxsplit=1, flags=re.MULTILINE)
                    description = parts[0]
                    if len(parts) > 1:
                        sublist_str = ''.join(parts[1:])
                        sublist = extract(sublist_str, level + 1)
                    else:
                        sublist = []
                else:
                    sublist = []
                tasklist_entries.append({
                    'complete': complete == 'X',
                    'summary': summary.strip(),
                    'description': description.rstrip(),
                    'sublist': sublist
                })
            return tasklist_entries

        pattern = rf"{self._MARKER_TASKLIST_START}(.*?){self._MARKER_TASKLIST_END}"
        match = re.search(pattern, body, flags=re.DOTALL)
        return extract(match.group(1).strip()) if match else []

    def _add_to_timeline(
        self,
        entry: str,
        body: str,
        issue_nr: int | None = None,
        comment_id: int | None = None,
    ):
        new_body = self.add_data_to_marked_document(
            data=entry, document=body, data_id="timeline", replace=False
        )
        if issue_nr:
            self._gh_api.issue_update(number=issue_nr, body=new_body)
        elif comment_id:
            self._gh_api.issue_comment_update(comment_id=comment_id, body=new_body)
        else:
            logger.error(
                "Failed to add to timeline", "Neither issue nor comment ID was provided."
            )
        return new_body

    def switch_to_autoupdate_branch(self, typ: Literal["hooks", "meta"], git: gittidy.Git) -> str:
        current_branch = git.current_branch_name()
        new_branch_prefix = self._data_main["branch.auto.name"]
        new_branch_name = f"{new_branch_prefix}{current_branch}/{typ}"
        git.stash()
        git.checkout(branch=new_branch_name, reset=True)
        logger.info(f"Switch to CI branch '{new_branch_name}' and reset it to '{current_branch}'.")
        self._branch_name_memory_autoupdate = current_branch
        return new_branch_name

    def switch_back_from_autoupdate_branch(self, git: gittidy.Git) -> None:
        if self._branch_name_memory_autoupdate:
            git.checkout(branch=self._branch_name_memory_autoupdate)
            git.stash_pop()
            self._branch_name_memory_autoupdate = None
        return

    def error_unsupported_triggering_action(self):
        event_name = self._context.event_name.value
        action_name = self._context.event.action.value
        action_err_msg = f"Unsupported triggering action for '{event_name}' event"
        action_err_details = (
            f"The workflow was triggered by an event of type '{event_name}', "
            f"but the triggering action '{action_name}' is not supported."
        )
        self._reporter.add(
            name="main",
            status="fail",
            summary=action_err_msg,
            body=action_err_details,
        )
        logger.critical(action_err_msg, action_err_details)
        raise ProManException()

    def _add_reference_to_dev_protocol(self, protocol: str, ref_url: str, ref_title: str) -> str:
        template = self._data_main["doc.dev_protocol.reference_template"]
        env_vars = {
            "ref": {
                "url": ref_url,
                "title": ref_title,
            }
        }
        entry = self.fill_jinja_template(template, env_vars)
        return self.add_data_to_marked_document(data=entry, document=protocol, data_id="references", replace=False)

    def add_data_to_marked_document(self, data: str, document: str, data_id: str, replace: bool = False):
        marker_start, marker_end = self.make_text_marker(data_id)
        pattern = rf"({re.escape(marker_start)})(.*?)({re.escape(marker_end)})"
        replacement = r"\1" + data + r"\3" if replace else r"\1\2" + data + r"\3"
        return re.sub(pattern, replacement, document, flags=re.DOTALL)

    def _add_readthedocs_reference_to_pr(
        self,
        pull_nr: int,
        update: bool = True,
        pull_body: str = ""
    ) -> str | None:

        def create_readthedocs_preview_url():
            # Ref: https://github.com/readthedocs/actions/blob/v1/preview/scripts/edit-description.js
            # Build the ReadTheDocs website for pull-requests and add a link to the pull request's description.
            # Note: Enable "Preview Documentation from Pull Requests" in ReadtheDocs project at https://docs.readthedocs.io/en/latest/pull-requests.html
            # https://docs.readthedocs.io/en/latest/guides/pull-requests.html

            config = self._data_main["tool.readthedocs.config.workflow"]
            domain = "org.readthedocs.build" if config["platform"] == "community" else "com.readthedocs.build"
            slug = config["name"]
            url = f"https://{slug}--{pull_nr}.{domain}/"
            if config["version_scheme"]["translation"]:
                language = config["language"]
                url += f"{language}/{pull_nr}/"
            return url

        if not self._data_main["tool.readthedocs"]:
            return
        if not pull_body:
            pull_body = self._gh_api.pull(number=pull_nr)["body"]
        new_body = self._add_reference_to_dev_protocol(
            protocol=pull_body,
            ref_url=create_readthedocs_preview_url(),
            ref_title="Website Preview on ReadTheDocs"
        )
        if update:
            self._gh_api.pull_update(number=pull_nr, body=new_body)
        return new_body

    def create_branch_name_release(self, major_version: int) -> str:
        """Generate the name of the release branch for a given major version."""
        release_branch_prefix = self._data_main["branch.release.name"]
        return f"{release_branch_prefix}{major_version}"

    def create_branch_name_prerelease(self, version: PEP440SemVer) -> str:
        """Generate the name of the pre-release branch for a given version."""
        pre_release_branch_prefix = self._data_main["branch.pre.name"]
        return f"{pre_release_branch_prefix}{version}"

    def create_branch_name_implementation(self, issue_nr: int, base_branch_name: str) -> str:
        """Generate the name of the development branch for a given issue number and base branch."""
        dev_branch_prefix = self._data_main["branch.dev.name"]
        return f"{dev_branch_prefix}{issue_nr}/{base_branch_name}"

    def read_announcement_file(self, base: bool, data: _ps.NestedDict) -> str | None:
        filepath = data["announcement.path"]
        if not filepath:
            return
        path_root = self._path_base if base else self._path_head
        fullpath = path_root / filepath
        return fullpath.read_text() if fullpath.is_file() else None

    def write_announcement_file(self, announcement: str, base: bool, data: _ps.NestedDict) -> None:
        announcement_data = data["announcement"]
        if not announcement_data:
            return
        if announcement:
            announcement = f"{announcement.strip()}\n"
        path_root = self._path_base if base else self._path_head
        with open(path_root / announcement_data["path"], "w") as f:
            f.write(announcement)
        return

    def make_text_marker(self, id: str):
        marker = self._data_main["doc.dev_protocol.marker"]
        template = "<!-- {pos}{id} -->"
        return tuple(template.format(pos=marker[pos], id=id) for pos in ("start", "end"))

    @staticmethod
    def get_next_version(
        version: PEP440SemVer,
        action: PrimaryActionCommitType
    ) -> PEP440SemVer:
        if action is PrimaryActionCommitType.RELEASE_MAJOR:
            if version.major == 0:
                return version.next_minor
            return version.next_major
        if action == PrimaryActionCommitType.RELEASE_MINOR:
            if version.major == 0:
                return version.next_patch
            return version.next_minor
        if action == PrimaryActionCommitType.RELEASE_PATCH:
            return version.next_patch
        if action == PrimaryActionCommitType.RELEASE_POST:
            return version.next_post
        return version

    @staticmethod
    def write_tasklist(entries: list[dict[str, bool | str | list]]) -> str:
        """Write an implementation tasklist as Markdown string.

        Parameters
        ----------
        entries : list[dict[str, bool | str | list]]
            A list of dictionaries, each representing a tasklist entry.
            The format of each dictionary is the same as that returned by
            `_extract_tasklist_entries`.
        """
        string = []

        def write(entry_list, level=0):
            for entry in entry_list:
                description = f"{entry['description']}\n" if entry['description'] else ''
                check = 'X' if entry['complete'] else ' '
                string.append(f"{' ' * level * 2}- [{check}] {entry['summary']}\n{description}")
                write(entry['sublist'], level + 1)

        write(entries)
        return "".join(string).rstrip()

    @staticmethod
    def make_user_mention(user: GitHubUser) -> str:
        linked_username = f"[{user.login}]({user.html_url})"
        if not user.name:
            return linked_username
        return f"{user.name} ({linked_username})"

    @staticmethod
    def fill_jinja_template(template: str, env_vars: dict) -> str:
        return jinja2.Template(template).render(env_vars)
