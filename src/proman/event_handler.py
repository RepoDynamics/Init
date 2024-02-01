from pathlib import Path
import json
from typing import Literal
import re
import datetime

from actionman.logger import logger
from markitup import html, md
import pylinks
from pylinks.exceptions import WebAPIError
from github_contexts import GitHubContext
import conventional_commits

import repodynamics
import repodynamics.control.content
import repodynamics.control.content.manager
from repodynamics.git import Git
from repodynamics.control.content import ControlCenterContentManager, ControlCenterContent
from repodynamics import hook
from repodynamics.version import PEP440SemVer
from repodynamics.control.manager import ControlCenterManager
from repodynamics.path import RelativePath
from repodynamics.control.content.dev.branch import (
    BranchProtectionRuleset,
    RulesetEnforcementLevel,
    RulesetBypassActorType,
    RulesetBypassMode,
)
from repodynamics.control.content.dev.label import LabelType, FullLabel
from repodynamics.datatype import (
    Branch,
    BranchType,
    Commit,
    Label,
    RepoFileType,
    PrimaryActionCommitType,
    NonConventionalCommit,
    FileChangeType,
    Emoji,
    InitCheckAction,
    TemplateType,
)


class EventHandler:

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
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str,
    ):
        self._template_type = template_type
        self._context = context_manager
        self._path_root_base = Path(path_repo_base)
        self._path_root_head = Path(path_repo_head)

        self._ccm_main: ControlCenterContentManager | None = repodynamics.control.content.from_json_file(
            path_repo=self._path_root_base,
            logger=logger,
            log_section_title="Read Main Control Center Contents"
        )

        repo_user = self._context.repository_owner
        repo_name = self._context.repository_name
        self._gh_api_admin = pylinks.api.github(token=admin_token).user(repo_user).repo(repo_name)
        self._gh_api = pylinks.api.github(token=self._context.token).user(repo_user).repo(repo_name)
        self._gh_link = pylinks.site.github.user(repo_user).repo(repo_name)
        self._git_base: Git = Git(
            path_repo=self._path_root_base,
            user=(self._context.event.sender.login, self._context.event.sender.github_email),
            logger=logger,
        )
        self._git_head: Git = Git(
            path_repo=self._path_root_head,
            user=(self._context.event.sender.login, self._context.event.sender.github_email),
            logger=logger,
        )
        self._template_name_ver = f"{self._template_type.value} v{repodynamics.__version__}"
        self._is_pypackit = self._template_type is TemplateType.PYPACKIT

        self._failed = False
        self._branch_name_memory_autoupdate: str | None = None
        self._output_website: dict = {}
        self._output_lint: dict = {}
        self._output_test: list[dict] = []
        self._output_build: dict = {}
        self._output_publish_testpypi: dict = {}
        self._output_test_testpypi: list[dict] = []
        self._output_publish_pypi: dict = {}
        self._output_test_pypi: list[dict] = []
        self._output_finalize: dict = {}
        self._summary_oneliners: list[str] = []
        self._summary_sections: list[str | html.ElementCollection | html.Element] = []
        return

    @logger.sectioner("Execute Event Handler", group=False)
    def run(self):
        self._run_event()
        output, summary = self._generate_output()
        return output, None, summary

    def _run_event(self) -> None:
        ...

    @logger.sectioner("Generate Outputs and Summary")
    def _generate_output(self):
        if self._failed:
            # Just to be safe, disable publish/deploy/release jobs if fail is True
            if self._output_website:
                self._output_website["deploy"] = False
            self._output_publish_testpypi = {}
            self._output_test_testpypi = []
            self._output_publish_pypi = {}
            self._output_test_pypi = []
            if self._output_finalize.get("release"):
                self._output_finalize["release"] = False
        output = {
            "fail": self._failed,
            "run": {
                "website": bool(self._output_website),
                "lint": bool(self._output_lint),
                "test": bool(self._output_test),
                "build": bool(self._output_build),
                "publish-testpypi": bool(self._output_publish_testpypi),
                "test-testpypi": bool(self._output_test_testpypi),
                "publish-pypi": bool(self._output_publish_pypi),
                "test-pypi": bool(self._output_test_pypi),
                "finalize": bool(self._output_finalize),
            },
            "website": self._output_website,
            "lint": self._output_lint,
            "test": self._output_test,
            "build": self._output_build,
            "publish-testpypi": self._output_publish_testpypi,
            "test-testpypi": self._output_test_testpypi,
            "publish-pypi": self._output_publish_pypi,
            "test-pypi": self._output_test_pypi,
            "finalize": self._output_finalize,
        }
        summary = self.assemble_summary()
        return output, summary

    def _action_meta(self, action: InitCheckAction, meta: ControlCenterManager, base: bool, branch: Branch) -> str | None:
        name = "Meta Sync"
        logger.h1(name)
        # if not action:
        #     action = InitCheckAction(
        #         self._ccm_main["workflow"]["init"]["meta_check_action"][self._event_type.value]
        #     )
        logger.input(f"Action: {action.value}")
        if action == InitCheckAction.NONE:
            self.add_summary(
                name=name,
                status="skip",
                oneliner="Meta synchronization is disabled for this event type❗",
            )
            logger.skip("Meta synchronization is disabled for this event type; skip❗")
            return
        git = self._git_base if base else self._git_head
        if action == InitCheckAction.PULL:
            pr_branch_name = self.switch_to_autoupdate_branch(typ="meta", git=git)
        meta_results, meta_changes, meta_summary = meta.compare_files()
        # logger.success("Meta synchronization completed.", {"result": meta_results})
        # logger.success("Meta synchronization summary:", meta_summary)
        # logger.success("Meta synchronization changes:", meta_changes)
        meta_changes_any = any(any(change.values()) for change in meta_changes.values())
        # Push/amend/pull if changes are made and action is not 'fail' or 'report'
        commit_hash = None
        if action not in [InitCheckAction.FAIL, InitCheckAction.REPORT] and meta_changes_any:
            meta.apply_changes()
            commit_msg = conventional_commits.message.create(
                typ=self._ccm_main["commit"]["secondary_action"]["auto-update"]["type"],
                description="Sync dynamic files",
            )
            commit_hash_before = git.commit_hash_normal()
            commit_hash_after = git.commit(message=str(commit_msg), stage="all")
            commit_hash = self._action_hooks(
                action=InitCheckAction.AMEND,
                branch=branch,
                base=base,
                ref_range=(commit_hash_before, commit_hash_after),
                internal=True,
            ) or commit_hash_after
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
        if not meta_changes_any:
            oneliner = "All dynamic files are in sync with meta content."
            logger.success(oneliner)
        else:
            oneliner = "Some dynamic files were out of sync with meta content."
            if action in [InitCheckAction.PULL, InitCheckAction.COMMIT, InitCheckAction.AMEND]:
                oneliner += " These were resynchronized and applied to "
                if action == InitCheckAction.PULL:
                    link = html.a(href=pull_data["url"], content=pull_data["number"])
                    oneliner += f"branch '{pr_branch_name}' and a pull request ({link}) was created."
                else:
                    link = html.a(
                        href=str(self._gh_link.commit(commit_hash)), content=commit_hash[:7]
                    )
                    oneliner += "the current branch " + (
                        f"in a new commit (hash: {link})"
                        if action == InitCheckAction.COMMIT
                        else f"by amending the latest commit (new hash: {link})"
                    )
        self.add_summary(
            name=name,
            status="fail"
            if meta_changes_any
            and action in [InitCheckAction.FAIL, InitCheckAction.REPORT, InitCheckAction.PULL]
            else "pass",
            oneliner=oneliner,
            details=meta_summary,
        )
        return commit_hash

    def _action_hooks(
        self,
        action: InitCheckAction,
        branch: Branch,
        base: bool,
        ref_range: tuple[str, str] | None = None,
        internal: bool = False,
    ) -> str | None:
        name = "Workflow Hooks"
        logger.h1(name)
        # if not action:
        #     action = InitCheckAction(
        #         self._ccm_main["workflow"]["init"]["hooks_check_action"][self._event_type.value]
        #     )
        logger.input(f"Action: {action.value}")
        if action == InitCheckAction.NONE:
            self.add_summary(
                name=name,
                status="skip",
                oneliner="Hooks are disabled for this event type❗",
            )
            logger.skip("Hooks are disabled for this event type; skip❗")
            return
        config = self._ccm_main["workflow"]["pre_commit"].get(branch.type.value)
        if not config:
            if not internal:
                oneliner = "Hooks are enabled but no pre-commit config set in 'meta.workflow.pre_commit'❗"
                logger.error(oneliner, raise_error=False)
                self.add_summary(
                    name=name,
                    status="fail",
                    oneliner=oneliner,
                )
            return
        input_action = (
            action
            if action in [InitCheckAction.REPORT, InitCheckAction.AMEND, InitCheckAction.COMMIT]
            else (InitCheckAction.REPORT if action == InitCheckAction.FAIL else InitCheckAction.COMMIT)
        )
        commit_msg = (
            conventional_commits.message.create(
                typ=self._ccm_main["commit"]["secondary_action"]["auto-update"]["type"],
                description="Apply automatic fixes made by workflow hooks",
            )
            if action in [InitCheckAction.COMMIT, InitCheckAction.PULL]
            else ""
        )
        git = self._git_base if base else self._git_head
        if action == InitCheckAction.PULL:
            pr_branch = self.switch_to_autoupdate_branch(typ="hooks", git=git)
        hooks_output = hook.run(
            ref_range=ref_range,
            action=input_action.value,
            commit_message=str(commit_msg),
            path_root=self._path_root_base if base else self._path_root_head,
            config=config,
            git=git,
            logger=logger,
        )
        passed = hooks_output["passed"]
        modified = hooks_output["modified"]
        # Push/amend/pull if changes are made and action is not 'fail' or 'report'
        if action not in [InitCheckAction.FAIL, InitCheckAction.REPORT] and modified:
            # self.push(amend=action == InitCheckAction.AMEND, set_upstream=action == InitCheckAction.PULL)
            if action == InitCheckAction.PULL:
                git.push(target="origin", set_upstream=True)
                pull_data = self._gh_api_admin.pull_create(
                    head=pr_branch,
                    base=branch.name,
                    title=commit_msg.summary,
                    body=commit_msg.body,
                )
                self.switch_back_from_autoupdate_branch(git=git)
        commit_hash = None
        if action == InitCheckAction.PULL and modified:
            link = html.a(href=pull_data["url"], content=pull_data["number"])
            target = f"branch '{pr_branch}' and a pull request ({link}) was created"
        if action in [InitCheckAction.COMMIT, InitCheckAction.AMEND] and modified:
            commit_hash = hooks_output["commit_hash"]
            link = html.a(href=str(self._gh_link.commit(commit_hash)), content=commit_hash[:7])
            target = "the current branch " + (
                f"in a new commit (hash: {link})"
                if action == InitCheckAction.COMMIT
                else f"by amending the latest commit (new hash: {link})"
            )
        if passed:
            oneliner = (
                "All hooks passed without making any modifications."
                if not modified
                else (
                    "All hooks passed in the second run. "
                    f"The modifications made during the first run were applied to {target}."
                )
            )
        elif action in [InitCheckAction.FAIL, InitCheckAction.REPORT]:
            mode = "some failures were auto-fixable" if modified else "failures were not auto-fixable"
            oneliner = f"Some hooks failed ({mode})."
        elif modified:
            oneliner = (
                "Some hooks failed even after the second run. "
                f"The modifications made during the first run were still applied to {target}."
            )
        else:
            oneliner = "Some hooks failed (failures were not auto-fixable)."
        if not internal:
            self.add_summary(
                name=name,
                status="fail" if not passed or (action == InitCheckAction.PULL and modified) else "pass",
                oneliner=oneliner,
                details=hooks_output["summary"],
            )
        return commit_hash

    def _get_latest_version(
        self,
        branch: str | None = None,
        dev_only: bool = False,
        base: bool = True,
    ) -> tuple[PEP440SemVer | None, int | None]:
        git = self._git_base if base else self._git_head
        ver_tag_prefix = self._ccm_main["tag"]["group"]["version"]["prefix"]
        if branch:
            git.stash()
            curr_branch = git.current_branch_name()
            git.checkout(branch=branch)
        latest_version = git.get_latest_version(tag_prefix=ver_tag_prefix, dev_only=dev_only)
        distance = git.get_distance(
            ref_start=f"refs/tags/{ver_tag_prefix}{latest_version.input}"
        ) if latest_version else None
        if branch:
            git.checkout(branch=curr_branch)
            git.stash_pop()
        if not latest_version and not dev_only:
            logger.error(f"No matching version tags found with prefix '{ver_tag_prefix}'.")
        return latest_version, distance

    def _tag_version(self, ver: str | PEP440SemVer, base: bool, msg: str = "") -> str:
        tag_prefix = self._ccm_main["tag"]["group"]["version"]["prefix"]
        tag = f"{tag_prefix}{ver}"
        if not msg:
            msg = f"Release version {ver}"
        git = self._git_base if base else self._git_head
        git.create_tag(tag=tag, message=msg)
        return tag

    def switch_to_autoupdate_branch(self, typ: Literal["hooks", "meta"], git: Git) -> str:
        current_branch = git.current_branch_name()
        new_branch_prefix = self._ccm_main.settings.dev.branch.auto_update.prefix
        new_branch_name = f"{new_branch_prefix}{current_branch}/{typ}"
        git.stash()
        git.checkout(branch=new_branch_name, reset=True)
        logger.success(f"Switch to CI branch '{new_branch_name}' and reset it to '{current_branch}'.")
        self._branch_name_memory_autoupdate = current_branch
        return new_branch_name

    def switch_back_from_autoupdate_branch(self, git: Git) -> None:
        if self._branch_name_memory_autoupdate:
            git.checkout(branch=self._branch_name_memory_autoupdate)
            git.stash_pop()
            self._branch_name_memory_autoupdate = None
        return

    def add_summary(
        self,
        name: str,
        status: Literal["pass", "fail", "skip", "warning"],
        oneliner: str,
        details: str | html.Element | html.ElementCollection | None = None,
    ):
        if status == "fail":
            self._failed = True
        self._summary_oneliners.append(f"{Emoji[status]}&nbsp;<b>{name}</b>: {oneliner}")
        if details:
            self._summary_sections.append(f"<h2>{name}</h2>\n\n{details}\n\n")
        return

    def _set_output(
        self,
        ccm_branch: ControlCenterContentManager,
        repository: str = "",
        ref: str = "",
        ref_before: str = "",
        version: str = "",
        release_name: str = "",
        release_tag: str = "",
        release_body: str = "",
        release_prerelease: bool = False,
        release_make_latest: Literal["legacy", "latest", "none"] = "legacy",
        release_discussion_category_name: str = "",
        website_build: bool = False,
        website_deploy: bool = False,
        package_lint: bool = False,
        package_test: bool = False,
        package_build: bool = False,
        package_publish_testpypi: bool = False,
        package_publish_pypi: bool = False,
        package_release: bool = False,
    ):
        package_artifact_name = f"Package ({version})" if version else "Package"
        if website_build or website_deploy:
            self._set_output_website(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                deploy=website_deploy,
            )
        if package_lint and not (package_publish_testpypi or package_publish_pypi):
            self._set_output_lint(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                ref_before=ref_before,
            )
        if package_test and not (package_publish_testpypi or package_publish_pypi):
            self._set_output_package_test(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                version=version,
            )
        if package_build or package_publish_testpypi or package_publish_pypi:
            self._set_output_package_build_and_publish(
                ccm_branch=ccm_branch,
                version=version,
                repository=repository,
                ref=ref,
                publish_testpypi=package_publish_testpypi,
                publish_pypi=package_publish_pypi,
                artifact_name=package_artifact_name,
            )
        if package_release:
            self._set_output_release(
                name=release_name,
                tag=release_tag,
                body=release_body,
                prerelease=release_prerelease,
                make_latest=release_make_latest,
                discussion_category_name=release_discussion_category_name,
                package_artifact_name=package_artifact_name,
            )
        return

    def _set_output_website(
        self,
        ccm_branch: ControlCenterContentManager,
        repository: str = "",
        ref: str = "",
        deploy: bool = False,
    ):
        self._output_website = {
            "url": self._ccm_main["url"]["website"]["base"],
            "repository": repository or self._context.target_repo_fullname,
            "ref": ref or self._context.ref_name,
            "deploy": deploy,
            "path-website": ccm_branch["path"]["dir"]["website"],
            "path-package": ".",
            "artifact-name": "Documentation",
        }
        return

    def _set_output_lint(
        self,
        ccm_branch: ControlCenterContentManager,
        repository: str = "",
        ref: str = "",
        ref_before: str = "",
    ):
        self._output_lint = {
            "repository": repository or self._context.target_repo_fullname,
            "ref": ref or self._context.ref_name,
            "ref-before": ref_before or self._context.hash_before,
            "os": [
                {"name": name, "runner": runner} for name, runner in zip(
                    ccm_branch["package"]["os_titles"],
                    ccm_branch["package"]["github_runners"]
                )
            ],
            "package-name": ccm_branch["package"]["name"],
            "python-versions": ccm_branch["package"]["python_versions"],
            "python-max-ver": ccm_branch["package"]["python_version_max"],
            "path-source": ccm_branch["path"]["dir"]["source"],
        }
        return

    def _set_output_package_test(
        self,
        ccm_branch: ControlCenterContentManager,
        repository: str = "",
        ref: str = "",
        source: Literal["GitHub", "PyPI", "TestPyPI"] = "GitHub",
        version: str = "",
        retry_sleep_seconds: str = "30",
        retry_sleep_seconds_total: str = "900",
    ):
        self._output_test.extend(
            self._create_output_package_test(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                source=source,
                version=version,
                retry_sleep_seconds=retry_sleep_seconds,
                retry_sleep_seconds_total=retry_sleep_seconds_total,
            )
        )
        return

    def _set_output_package_build_and_publish(
        self,
        ccm_branch: ControlCenterContentManager,
        version: str,
        repository: str = "",
        ref: str = "",
        ref_before: str = "",
        publish_testpypi: bool = False,
        publish_pypi: bool = False,
        artifact_name: str = "Package"
    ):
        self._output_build = {
            "repository": repository or self._context.target_repo_fullname,
            "ref": ref or self._context.ref_name,
            "artifact-name": artifact_name,
            "pure-python": ccm_branch["package"]["pure_python"],
            "cibw-matrix-platform": ccm_branch["package"]["cibw_matrix_platform"],
            "cibw-matrix-python": ccm_branch["package"]["cibw_matrix_python"],
            "path-readme": ccm_branch["path"]["file"]["readme_pypi"],
        }
        if publish_testpypi or publish_pypi:
            self._set_output_lint(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                ref_before=ref_before,
            )
            self._output_test.extend(
                self._create_output_package_test(
                    ccm_branch=ccm_branch,
                    repository=repository,
                    ref=ref,
                    source="GitHub",
                )
            )
            self._output_publish_testpypi = {
                "platform": "TestPyPI",
                "upload-url": "https://test.pypi.org/legacy/",
                "download-url": f'https://test.pypi.org/project/{ccm_branch["package"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_testpypi = self._create_output_package_test(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                source="TestPyPI",
                version=version,
            )
        if publish_pypi:
            self._output_publish_pypi = {
                "platform": "PyPI",
                "upload-url": "https://upload.pypi.org/legacy/",
                "download-url": f'https://pypi.org/project/{ccm_branch["package"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_pypi = self._create_output_package_test(
                ccm_branch=ccm_branch,
                repository=repository,
                ref=ref,
                source="PyPI",
                version=version,
            )
        return

    def _set_output_release(
        self,
        name: str,
        tag: str,
        body: str | None = None,
        prerelease: bool | None = None,
        make_latest: Literal["legacy", "latest", "none"] | None = None,
        discussion_category_name: str | None = None,
        website_artifact_name: str = "Documentation",
        package_artifact_name: str = "Package",
    ):
        self._output_finalize["release"] = {
            "name": name,
            "tag-name": tag,
            "body": body,
            "prerelease": prerelease,
            "make-latest": make_latest,
            "discussion_category_name": discussion_category_name,
            "website-artifact-name": website_artifact_name,
            "package-artifact-name": package_artifact_name
        }
        return

    def _create_output_package_test(
        self,
        ccm_branch: ControlCenterContentManager,
        repository: str = "",
        ref: str = "",
        source: Literal["GitHub", "PyPI", "TestPyPI"] = "GitHub",
        version: str = "",
        retry_sleep_seconds_total: str = "900",
        retry_sleep_seconds: str = "30",
    ) -> list[dict]:
        common = {
            "repository": repository or self._context.target_repo_fullname,
            "ref": ref or self._context.ref_name,
            "path-setup-testsuite": f'./{ccm_branch["path"]["dir"]["tests"]}',
            "path-setup-package": ".",
            "testsuite-import-name": ccm_branch["package"]["testsuite_import_name"],
            "package-source": source,
            "package-name": ccm_branch["package"]["name"],
            "package-version": version,
            "path-requirements-package": "requirements.txt",
            "path-report-pytest": ccm_branch["path"]["dir"]["local"]["report"]["pytest"],
            "path-report-coverage": ccm_branch["path"]["dir"]["local"]["report"]["coverage"],
            "path-cache-pytest": ccm_branch["path"]["dir"]["local"]["cache"]["pytest"],
            "path-cache-coverage": ccm_branch["path"]["dir"]["local"]["cache"]["coverage"],
            "retry-sleep-seconds": retry_sleep_seconds,
            "retry-sleep-seconds-total": retry_sleep_seconds_total,
        }
        out = []
        for github_runner, os in zip(
            ccm_branch["package"]["github_runners"],
            ccm_branch["package"]["os_titles"]
        ):
            for python_version in ccm_branch["package"]["python_versions"]:
                out.append(
                    {
                        **common,
                        "runner": github_runner,
                        "os": os,
                        "python-version": python_version,
                    }
                )
        return out

    def assemble_summary(self) -> str:
        github_context, event_payload = (
            html.details(content=md.code_block(str(data), lang="yaml"), summary=summary)
            for data, summary in (
                (self._context, "🎬 GitHub Context"),
                (self._context.event, "📥 Event Payload"),
            )
        )
        intro = [
            f"{Emoji.PLAY} The workflow was triggered by a <code>{self._context.event_name}</code> event."
        ]
        if self._failed:
            intro.append(f"{Emoji.FAIL} The workflow failed.")
        else:
            intro.append(f"{Emoji.PASS} The workflow passed.")
        intro = html.ul(intro)
        summary = html.ElementCollection(
            [
                html.h(1, "Workflow Report"),
                intro,
                html.ul([github_context, event_payload]),
                html.h(2, "🏁 Summary"),
                html.ul(self._summary_oneliners),
            ]
        )
        logs = html.ElementCollection(
            [
                html.h(2, "🪵 Logs"),
                html.details(logger.file_log, "Log"),
            ]
        )
        summaries = html.ElementCollection(self._summary_sections)
        path = Path("./repodynamics")
        path.mkdir(exist_ok=True)
        with open(path / "log.html", "w") as f:
            f.write(str(logs))
        with open(path / "report.html", "w") as f:
            f.write(str(summaries))
        return str(summary)

    def resolve_branch(self, branch_name: str | None = None) -> Branch:
        if not branch_name:
            branch_name = self._context.ref_name
        if branch_name == self._context.event.repository.default_branch:
            return Branch(type=BranchType.MAIN, name=branch_name)
        return self._ccm_main.get_branch_info_from_name(branch_name=branch_name)

    def error_unsupported_triggering_action(self):
        event_name = self._context.event_name.value
        action_name = self._context.event.action.value
        action_err_msg = f"Unsupported triggering action for '{event_name}' event."
        action_err_details_sub = f"but the triggering action '{action_name}' is not supported."
        action_err_details = (
            f"The workflow was triggered by an event of type '{event_name}', {action_err_details_sub}"
        )
        logger.error(action_err_msg, action_err_details, raise_error=False)
        self.add_summary(
            name="Event Handler",
            status="fail",
            oneliner=action_err_msg,
            details=action_err_details,
        )
        return

    def _action_file_change_detector(self, meta: ControlCenterManager) -> dict[RepoFileType, list[str]]:
        name = "File Change Detector"
        logger.h1(name)
        change_type_map = {
            "added": FileChangeType.CREATED,
            "deleted": FileChangeType.REMOVED,
            "modified": FileChangeType.MODIFIED,
            "unmerged": FileChangeType.UNMERGED,
            "unknown": FileChangeType.UNKNOWN,
            "broken": FileChangeType.BROKEN,
            "copied_to": FileChangeType.CREATED,
            "renamed_from": FileChangeType.REMOVED,
            "renamed_to": FileChangeType.CREATED,
            "copied_modified_to": FileChangeType.CREATED,
            "renamed_modified_from": FileChangeType.REMOVED,
            "renamed_modified_to": FileChangeType.CREATED,
        }
        summary_detail = {file_type: [] for file_type in RepoFileType}
        change_group = {file_type: [] for file_type in RepoFileType}
        changes = self._git_head.changed_files(
            ref_start=self._context.hash_before, ref_end=self._context.hash_after
        )
        logger.success("Detected changed files", json.dumps(changes, indent=3))
        fixed_paths = [outfile.rel_path for outfile in meta.path_manager.fixed_files]
        for change_type, changed_paths in changes.items():
            # if change_type in ["unknown", "broken"]:
            #     self.logger.warning(
            #         f"Found {change_type} files",
            #         f"Running 'git diff' revealed {change_type} changes at: {changed_paths}. "
            #         "These files will be ignored."
            #     )
            #     continue
            if change_type.startswith("copied") and change_type.endswith("from"):
                continue
            for path in changed_paths:
                if path in fixed_paths:
                    typ = RepoFileType.DYNAMIC
                elif path == ".github/_README.md" or path.endswith("/README.md"):
                    typ = RepoFileType.README
                elif path.startswith(meta.path_manager.dir_source_rel):
                    typ = RepoFileType.PACKAGE
                elif path.startswith(meta.path_manager.dir_website_rel):
                    typ = RepoFileType.WEBSITE
                elif path.startswith(meta.path_manager.dir_tests_rel):
                    typ = RepoFileType.TEST
                elif path.startswith(RelativePath.dir_github_workflows):
                    typ = RepoFileType.WORKFLOW
                elif (
                    path.startswith(RelativePath.dir_github_discussion_template)
                    or path.startswith(RelativePath.dir_github_issue_template)
                    or path.startswith(RelativePath.dir_github_pull_request_template)
                    or path.startswith(RelativePath.dir_github_workflow_requirements)
                ):
                    typ = RepoFileType.DYNAMIC
                elif path == RelativePath.file_path_meta:
                    typ = RepoFileType.SUPERMETA
                elif path == f"{meta.path_manager.dir_meta_rel}path.yaml":
                    typ = RepoFileType.SUPERMETA
                elif path.startswith(meta.path_manager.dir_meta_rel):
                    typ = RepoFileType.META
                else:
                    typ = RepoFileType.OTHER
                summary_detail[typ].append(f"{change_type_map[change_type].value.emoji} {path}")
                change_group[typ].append(path)
        summary_details = []
        changed_groups_str = ""
        for file_type, summaries in summary_detail.items():
            if summaries:
                summary_details.append(html.h(3, file_type.value.title))
                summary_details.append(html.ul(summaries))
                changed_groups_str += f", {file_type.value}"
        if changed_groups_str:
            oneliner = f"Found changes in following groups: {changed_groups_str[2:]}."
            if summary_detail[RepoFileType.SUPERMETA]:
                oneliner = (
                    f"This event modified SuperMeta files; "
                    f"make sure to double-check that everything is correct❗ {oneliner}"
                )
        else:
            oneliner = "No changes were found."
        legend = [f"{status.value.emoji}  {status.value.title}" for status in FileChangeType]
        color_legend = html.details(content=html.ul(legend), summary="Color Legend")
        summary_details.insert(0, html.ul([oneliner, color_legend]))
        self.add_summary(
            name=name,
            status="warning"
            if summary_detail[RepoFileType.SUPERMETA]
            else ("pass" if changed_groups_str else "skip"),
            oneliner=oneliner,
            details=html.ElementCollection(summary_details),
        )
        return change_group

    def _config_repo(self):
        data = self._ccm_main.repo__config | {
            "has_issues": True,
            "allow_squash_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
        }
        topics = data.pop("topics")
        self._gh_api_admin.repo_update(**data)
        self._gh_api_admin.repo_topics_replace(topics=topics)
        if not self._gh_api_admin.actions_permissions_workflow_default()["can_approve_pull_request_reviews"]:
            self._gh_api_admin.actions_permissions_workflow_default_set(can_approve_pull_requests=True)
        return

    def _config_repo_pages(self) -> None:
        """Activate GitHub Pages (source: workflow) if not activated, update custom domain."""
        if not self._gh_api.info["has_pages"]:
            self._gh_api_admin.pages_create(build_type="workflow")
        cname = self._ccm_main.web__base_url
        try:
            self._gh_api_admin.pages_update(
                cname=cname.removeprefix("https://").removeprefix("http://") if cname else "",
                build_type="workflow",
            )
        except WebAPIError as e:
            logger.warning(f"Failed to update custom domain for GitHub Pages", str(e))
        if cname:
            try:
                self._gh_api_admin.pages_update(https_enforced=cname.startswith("https://"))
            except WebAPIError as e:
                logger.warning(f"Failed to update HTTPS enforcement for GitHub Pages", str(e))
        return

    def _config_repo_labels_reset(self, ccs: ControlCenterContent | None = None):
        ccs = ccs or self._ccm_main.settings
        for label in self._gh_api.labels:
            self._gh_api.label_delete(label["name"])
        for label in ccs.dev.label.full_labels:
            self._gh_api.label_create(name=label.name, description=label.description, color=label.color)
        return

    def _config_repo_labels_update(self, ccs_new: ControlCenterContent, ccs_old: ControlCenterContent):

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

        name = "Repository Labels Synchronizer"
        logger.h1(name)

        labels_old, labels_old_ver, labels_old_branch, labels_old_rest = format_labels(
            ccs_old.dev.label.full_labels
        )
        labels_new, labels_new_ver, labels_new_branch, labels_new_rest = format_labels(
            ccs_new.dev.label.full_labels
        )

        ids_old = set(labels_old.keys())
        ids_new = set(labels_new.keys())

        # Update labels that are in both old and new settings,
        #   when their label data has changed in new settings.
        ids_shared = ids_old & ids_new
        for id_shared in ids_shared:
            old_label = labels_old[id_shared]
            new_label = labels_new[id_shared]
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

    def _config_repo_branch_names(self, ccs_new: ControlCenterContent, ccs_old: ControlCenterContent) -> dict:
        old = ccs_old.dev.branch
        new = ccs_new.dev.branch
        old_to_new_map = {}
        if old.main.name != new.main.name:
            self._gh_api_admin.branch_rename(old_name=old.main.name, new_name=new.main.name)
            old_to_new_map[old.main.name] = new.main.name
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
                        self._gh_api_admin.branch_rename(old_name=branch_name, new_name=new_name)
                        old_to_new_map[branch_name] = new_name
        return old_to_new_map

    def _config_rulesets(
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
                self._gh_api_admin.ruleset_create(**args)
                return
            for existing_ruleset in existing_rulesets:
                if existing_ruleset['name'] == name:
                    args["ruleset_id"] = existing_ruleset["id"]
                    args["require_status_checks"] = ruleset.rule.require_status_checks
                    self._gh_api_admin.ruleset_update(**args)
                    return
            self._gh_api_admin.ruleset_create(**args)
            return

        if ccs_old:
            existing_rulesets = self._gh_api_admin.rulesets(include_parents=False)

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

    def _add_readthedocs_reference_to_pr(
        self,
        pull_nr: int,
        update: bool = True,
        pull_body: str = ""
    ) -> str | None:
        if not self._ccm_main["web"].get("readthedocs"):
            return
        url = self._create_readthedocs_preview_url(pull_nr=pull_nr)
        reference = f"[Website Preview on ReadTheDocs]({url})"
        if not pull_body:
            pull_body = self._gh_api.pull(number=pull_nr)["body"]
        new_body = self._add_reference_to_dev_protocol(protocol=pull_body, reference=reference)
        if update:
            self._gh_api.pull_update(number=pull_nr, body=new_body)
        return new_body

    def _add_reference_to_dev_protocol(self, protocol: str, reference: str) -> str:
        entry = f"- {reference}"
        pattern = rf"({self._MARKER_REFERENCES_START})(.*?)({self._MARKER_REFERENCES_END})"
        replacement = r"\1\2" + entry + "\n" + r"\3"
        return re.sub(pattern, replacement, protocol, flags=re.DOTALL)

    def _create_readthedocs_preview_url(self, pull_nr: int):
        # Ref: https://github.com/readthedocs/actions/blob/v1/preview/scripts/edit-description.js
        # Build the ReadTheDocs website for pull-requests and add a link to the pull request's description.
        # Note: Enable "Preview Documentation from Pull Requests" in ReadtheDocs project at https://docs.readthedocs.io/en/latest/pull-requests.html
        config = self._ccm_main["web"]["readthedocs"]
        domain = "org.readthedocs.build" if config["platform"] == "community" else "com.readthedocs.build"
        slug = config["name"]
        url = f"https://{slug}--{pull_nr}.{domain}/"
        if config["versioning_scheme"]["translation"]:
            language = config["language"]
            url += f"{language}/{pull_nr}/"
        return url

    def _get_commits(self, base: bool = False) -> list[Commit]:
        git = self._git_base if base else self._git_head
        commits = git.get_commits(f"{self._context.hash_before}..{self._context.hash_after}")
        logger.success("Read commits from git history", json.dumps(commits, indent=4))
        parser = conventional_commits.parser.create(
            types=self._ccm_main.get_all_conventional_commit_types(secondary_custom_only=False),
        )
        parsed_commits = []
        for commit in commits:
            conv_msg = parser.parse(message=commit["msg"])
            if not conv_msg:
                parsed_commits.append(Commit(**commit, group_data=NonConventionalCommit()))
            else:
                group = self._ccm_main.get_commit_type_from_conventional_type(conv_type=conv_msg.type)
                commit["msg"] = conv_msg
                parsed_commits.append(Commit(**commit, group_data=group))
        return parsed_commits

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
            pattern = rf'{" " * level * 2}- \[(X| )\] (.+?)(?=\n{" " * level * 2}- \[|\Z)'
            # Find all matches
            matches = re.findall(pattern, tasklist_string, flags=re.DOTALL)
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

    def _update_issue_status_labels(self, issue_nr: int, labels: list[Label], current_label: Label) -> None:
        for label in labels:
            if label.name != current_label.name:
                self._gh_api.issue_labels_remove(number=issue_nr, label=label.name)
        return

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

    def create_branch_name_release(self, major_version: int) -> str:
        """Generate the name of the release branch for a given major version."""
        release_branch_prefix = self._ccm_main.settings.dev.branch.release.prefix
        return f"{release_branch_prefix}{major_version}"

    def create_branch_name_prerelease(self, version: PEP440SemVer) -> str:
        """Generate the name of the pre-release branch for a given version."""
        pre_release_branch_prefix = self._ccm_main.settings.dev.branch.pre_release.prefix
        return f"{pre_release_branch_prefix}{version}"

    def create_branch_name_implementation(self, issue_nr: int, base_branch_name: str) -> str:
        """Generate the name of the implementation branch for a given issue number and base branch."""
        impl_branch_prefix = self._ccm_main.settings.dev.branch.implementation.prefix
        return f"{impl_branch_prefix}{issue_nr}/{base_branch_name}"

    def create_branch_name_development(self, issue_nr: int, base_branch_name: str, task_nr: int) -> str:
        """Generate the name of the development branch for a given issue number and base branch."""
        dev_branch_prefix = self._ccm_main.settings.dev.branch.development.prefix
        return f"{dev_branch_prefix}{issue_nr}/{base_branch_name}/{task_nr}"

    def _read_web_announcement_file(self, base: bool, ccm: ControlCenterContentManager) -> str | None:
        path_root = self._path_root_base if base else self._path_root_head
        path = path_root / ccm["path"]["file"]["website_announcement"]
        return path.read_text() if path.is_file() else None

    def _write_web_announcement_file(self, announcement: str, base: bool, ccm: ControlCenterContentManager) -> None:
        if announcement:
            announcement = f"{announcement.strip()}\n"
        path_root = self._path_root_base if base else self._path_root_head
        with open(path_root / ccm["path"]["file"]["website_announcement"], "w") as f:
            f.write(announcement)
        return

    @staticmethod
    def _get_next_version(version: PEP440SemVer, action: PrimaryActionCommitType):
        if action == PrimaryActionCommitType.RELEASE_MAJOR:
            if version.major == 0:
                return version.next_minor
            return version.next_major
        if action == PrimaryActionCommitType.RELEASE_MINOR:
            return version.next_minor
        if action == PrimaryActionCommitType.RELEASE_PATCH:
            return version.next_patch
        if action == PrimaryActionCommitType.RELEASE_POST:
            return version.next_post
        return version

    @staticmethod
    def _write_tasklist(entries: list[dict[str, bool | str | list]]) -> str:
        """
        Write an implementation tasklist as Markdown string.

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

    # @property
    # def hash_latest(self) -> str:
    #     """The SHA hash of the most recent commit on the branch,
    #     including commits made during the workflow run.
    #     """
    #     return self._hash_latest if self._hash_latest else self._context.hash_after

    # def commit(
    #     self,
    #     message: str = "",
    #     stage: Literal["all", "staged", "unstaged"] = "all",
    #     amend: bool = False,
    #     push: bool = False,
    #     set_upstream: bool = False,
    # ):
    #     commit_hash = self._git_head.commit(message=message, stage=stage, amend=amend)
    #     if amend:
    #         self._amended = True
    #     if push:
    #         commit_hash = self.push(set_upstream=set_upstream)
    #     return commit_hash
    #
    # def push(self, amend: bool = False, set_upstream: bool = False):
    #     new_hash = self._git_head.push(
    #         target="origin", set_upstream=set_upstream, force_with_lease=self._amended or amend
    #     )
    #     self._amended = False
    #     if new_hash and self._git_head.current_branch_name() == self._context.ref_name:
    #         self._hash_latest = new_hash
    #     return new_hash

    # def _set_job_run(
    #     self,
    #     package_build: bool | None = None,
    #     package_lint: bool | None = None,
    #     package_test_local: bool | None = None,
    #     website_build: bool | None = None,
    #     website_deploy: bool | None = None,
    #     website_rtd_preview: bool | None = None,
    #     package_publish_testpypi: bool | None = None,
    #     package_publish_pypi: bool | None = None,
    #     package_test_testpypi: bool | None = None,
    #     package_test_pypi: bool | None = None,
    #     github_release: bool | None = None,
    # ) -> None:
    #     data = locals()
    #     data.pop("self")
    #     for key, val in data.items():
    #         if val is not None:
    #             self._job_run_flag[key] = val
    #     return
