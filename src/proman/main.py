"""Main event handler."""


from pathlib import Path
import json
from typing import Literal
import re
import datetime

from loggerman import logger
from markitup import html, md
import pylinks
import pyserials as _ps

from github_contexts import GitHubContext
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
from proman.output_writer import OutputWriter
from proman.repo_config import RepoConfig
from proman import hook_runner
from proman.data_manager import DataManager
from proman import change_detector
from proman.reporter import Reporter


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
        admin_token: str | None,
        path_repo_base: str,
        path_repo_head: str,
    ):
        self._context = github_context
        self._path_base = Path(path_repo_base)
        self._path_head = Path(path_repo_head)
        self._reporter = Reporter(github_context=self._context)
        self._output = OutputWriter(context=self._context)

        repo_user = self._context.repository_owner
        repo_name = self._context.repository_name
        self._gh_api_admin = pylinks.api.github(token=admin_token).user(repo_user).repo(repo_name)
        self._gh_api = pylinks.api.github(token=self._context.token).user(repo_user).repo(repo_name)
        self._gh_link = pylinks.site.github.user(repo_user).repo(repo_name)
        self._has_admin_token = bool(admin_token)
        self._repo_config = RepoConfig(
            gh_api=self._gh_api_admin if self._has_admin_token else self._gh_api,
            default_branch_name=self._context.event.repository.default_branch
        )

        git_user = (self._context.event.sender.login, self._context.event.sender.github_email)
        # TODO: Check again when gittidy is finalized; add section titles
        self._git_base = gittidy.Git(
            path=self._path_base,
            user=git_user,
            user_scope="global",
            committer=self._REPODYNAMICS_BOT_USER,
            committer_scope="local",
            committer_persistent=True,
        )
        self._git_head = gittidy.Git(
            path=self._path_head,
            user=git_user,
            user_scope="global",
            committer=self._REPODYNAMICS_BOT_USER,
            committer_scope="local",
            committer_persistent=True,
        )

        self._ver = pkgdata.get_version_from_caller()

        self._data_main: DataManager | None = None
        self._data_branch_before: DataManager | None = None
        self._data_branch: DataManager | None = None
        self._failed = False
        self._branch_name_memory_autoupdate: str | None = None
        return

    def run(self) -> tuple[dict, str]:
        self._data_main = DataManager(controlman.from_json_file(repo_path=self._path_base))
        self._data_branch_before = self._data_main if self._context.ref_is_main else DataManager(
            controlman.from_json_file(repo_path=self._path_head)
        )
        self._run_event()
        output = self._output.generate(failed=self._failed)
        summary_gha, summary_full = self._reporter.generate()
        filename = (
            f"{self._context.repository_name}-workflow-run"
            f"-{self._context.run_id}-{self._context.run_attempt}.{{}}.html"
        )
        dir_path = Path("./proman_artifacts")
        dir_path.mkdir(exist_ok=True)
        with open(dir_path / filename.format("report"), "w") as f:
            f.write(summary_full)
        return output, summary_gha

    def _run_event(self) -> None:
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
        latest_hash = self._git_head.push() if hash_hooks or hash_sync else self._context.hash_after
        job_runs = decide_jobs()
        return data, job_runs, latest_hash

    @logger.sectioner("File Change Detector")
    def _detect_changes(self) -> tuple[RepoFileType, ...]:
        changes = self._git_head.changed_files(
            ref_start=self._context.hash_before, ref_end=self._context.hash_after
        )
        logger.debug("Detected changed files", json.dumps(changes, indent=3))
        full_info = change_detector.detect(data=self._data_branch_before, changes=changes)
        changed_filetypes = {}
        headers = "".join(
            [f"<th>{header}</th>" for header in ("Type", "Subtype", "Change", "Dynamic", "Path")])
        rows = [f"<tr>{headers}</tr>"]
        for typ, subtype, change_type, is_dynamic, path in sorted(full_info, key=lambda x: (x[0].value, x[1])):
            changed_filetypes.setdefault(typ, []).append(change_type)
            if is_dynamic:
                changed_filetypes.setdefault(RepoFileType.DYNAMIC, []).append(change_type)
            dynamic = f'<td title="{'Dynamic' if is_dynamic else 'Static'}">{'✅' if is_dynamic else '❌'}</td>'
            change_sig = change_type.value
            change = f'<td title="{change_sig.title}">{change_sig.emoji}</td>'
            subtype = subtype or Path(path).stem
            rows.append(
                f"<tr><td>{typ.value}</td><td>{subtype}</td>{change}{dynamic}<td><code>{path}</code></td></tr>"
            )
        if not changed_filetypes:
            oneliner = "No files were changed in this event."
            section = None
        else:
            changed_types = ", ".join(sorted([typ.value for typ in changed_filetypes]))
            oneliner = f"Following filetypes were changed: {changed_types}"
            section_intro = []
            intro_table_rows = ["<tr><th>Type</th><th>Changes</th></tr>"]
            has_broken_changes = False
            if RepoFileType.DYNAMIC in changed_filetypes:
                warning = "⚠️ Dynamic files were changed; make sure to double-check that everything is correct."
                section_intro.append(warning)
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
                intro_table_rows.append(
                    f"<tr><td>{file_type.value}</td><td>{changes_cell}</td></tr>"
                )
            if has_broken_changes:
                warning = "⚠️ Some changes were marked as 'broken' or 'unknown'; please investigate."
                section_intro.append(warning)
            intro_table = html.elem.table(intro_table_rows)
            section_intro.append(f"Following filetypes were changed: {intro_table}")
            section_intro.append(f"Following files were changed during this event: {html.elem.table(rows)}")
            legend = [html.elem.li(f"{status.value.emoji}  {status.value.title}") for status in FileChangeType]
            color_legend = html.elem.details(content=[html.elem.ul(legend)], summary="Color Legend")
            section_intro.append(color_legend)
            section = html.elem.ul([html.elem.li(entry) for entry in section_intro])
        self._reporter.add(
            name="file_change",
            status="pass",
            summary=oneliner,
            details_full=section,
            details_short=section,
        )
        return tuple(changed_filetypes.keys())

    @logger.sectioner("Configuration Management")
    def _sync(
        self,
        action: InitCheckAction,
        cc_manager: controlman.CenterManager,
        base: bool,
        commit_msg: str | None = None,
    ) -> str | None:
        logger.info(f"Action: {action.value}")
        if action == InitCheckAction.NONE:
            self._reporter.add(
                name="cca",
                status="skip",
                summary="CCA is disabled for this event.",
            )
            return
        git = self._git_base if base else self._git_head
        if action == InitCheckAction.PULL:
            pr_branch_name = self.switch_to_autoupdate_branch(typ="meta", git=git)
        try:
            reporter = cc_manager.report()
        except controlman.exception.ControlManException as e:
            report_full = e.report(mode="full", md=False)
            report_short = e.report(mode="short", md=True)
            self._reporter.add(
                name="cca",
                status="fail",
                summary=e.summary_html,
                details_full=list(report_full.section["details"].content.values()) if report_full.section else None,
                details_short=list(report_short.section["details"].content.values()) if report_short.section else None,
            )
            return
        # Push/pull if changes are made and action is not 'fail' or 'report'
        report = reporter.report()
        commit_hash = None
        if reporter.has_changes and action not in [InitCheckAction.FAIL, InitCheckAction.REPORT]:
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
                link = html.elem.a(href=pull_data["url"], content=f'#{pull_data["number"]}')
                description += f"branch {html.elem.code(pr_branch_name)} in PR {link}."
            else:
                link = html.elem.a(
                    href=str(self._gh_link.commit(commit_hash)), content=f"<code>{commit_hash[:7]}</code>"
                )
                description += "the current branch " + (
                    f"in commit {link}."
                    if action == InitCheckAction.COMMIT
                    else f"by amending the latest commit (new hash: {link})."
                )
            report.content["summary"] += f" {description}"
        self._reporter.add(
            name="cca",
            status="fail" if reporter.has_changes and action in [
               InitCheckAction.FAIL,
               InitCheckAction.REPORT,
               InitCheckAction.PULL
            ] else "pass",
            summary=report.content["summary"],
            details_full=report.section["changes"].content["details"],
        )
        return commit_hash

    @logger.sectioner("Workflow Hooks")
    def _action_hooks(
        self,
        action: InitCheckAction,
        data: _ps.NestedDict,
        base: bool,
        ref_range: tuple[str, str] | None = None,
        internal: bool = False,
    ) -> str | None:
        logger.info(f"Action: {action.value}")
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
        hooks_output = hook_runner.run(
            git=git,
            ref_range=ref_range,
            action=input_action.value,
            commit_message=str(commit_msg),
            config=config,
        )
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
            link = html.elem.a(href=pull_data["url"], content=pull_data["number"])
            target = f"branch <code>{pr_branch}</code> and a pull request ({link}) was created"
            hooks_output["summary"] += summary_addon_template.format(target=target)
        if action in [InitCheckAction.COMMIT, InitCheckAction.AMEND] and modified:
            commit_hash = hooks_output["commit_hash"]
            link = html.elem.a(href=str(self._gh_link.commit(commit_hash)), content=commit_hash[:7])
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
                details_full=hooks_output["details_full"],
                details_short=hooks_output["details_short"],
            )
        return commit_hash

    def get_cc_manager(
        self,
        base: bool = False,
        data_before: _ps.NestedDict | None = None,
        data_main: _ps.NestedDict | None = None,
        future_versions: dict[str, str | PEP440SemVer] | None = None,
    ) -> controlman.CenterManager:
        return controlman.manager(
            repo=self._git_base if base else self._git_head,
            data_before=data_before or self._data_branch_before,
            data_main=data_main or self._data_main,
            github_token=self._context.token,
            future_versions=future_versions,
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
            types=self._data_main.get_all_conventional_commit_types(secondary_custom_only=False),
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
        now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y.%m.%d %H:%M:%S")
        timeline_entry = (
            f"- **{now}**: {entry}"
        )
        pattern = rf"({self._MARKER_TIMELINE_START})(.*?)({self._MARKER_TIMELINE_END})"
        replacement = r"\1\2" + timeline_entry + "\n" + r"\3"
        new_body = re.sub(pattern, replacement, body, flags=re.DOTALL)
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
            details_short=action_err_details,
            details_full=action_err_details,
        )
        logger.critical(action_err_msg, action_err_details)
        return

    def _add_reference_to_dev_protocol(self, protocol: str, reference: str) -> str:
        entry = f"- {reference}"
        pattern = rf"({self._MARKER_REFERENCES_START})(.*?)({self._MARKER_REFERENCES_END})"
        replacement = r"\1\2" + entry + "\n" + r"\3"
        return re.sub(pattern, replacement, protocol, flags=re.DOTALL)

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
        url = create_readthedocs_preview_url()
        reference = f"[Website Preview on ReadTheDocs]({url})"
        if not pull_body:
            pull_body = self._gh_api.pull(number=pull_nr)["body"]
        new_body = self._add_reference_to_dev_protocol(protocol=pull_body, reference=reference)
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
