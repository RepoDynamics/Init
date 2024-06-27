from enum import Enum

import github_contexts
from loggerman import logger
import controlman
# from repodynamics.datatype import InitCheckAction
# from repodynamics.datatype import (
#     Branch,
#     BranchType,
# )
# from repodynamics.control.manager import ControlCenterManager

from proman.datatype import TemplateType
from proman.handler.main import EventHandler
from proman.changelog_manager import ChangelogManager


class ConfigLintAction(Enum):
    DISABLE = "disable"
    REPORT = "report"
    PULL = "pull"
    MERGE = "merge"


class WorkflowDispatchInput(Enum):
    CONFIG = "config"
    LINT = "lint"
    BUILD = "build"
    TEST = "test"
    WEBSITE = "website"
    RELEASE = "release"

_WORKFLOW_DISPATCH_INPUT_TYPE = {
    WorkflowDispatchInput.CONFIG: ConfigLintAction,
    WorkflowDispatchInput.LINT: ConfigLintAction,
    WorkflowDispatchInput.BUILD: bool,
    WorkflowDispatchInput.TEST: bool,
    WorkflowDispatchInput.WEBSITE: bool,
    WorkflowDispatchInput.RELEASE: bool,
}


class WorkflowDispatchEventHandler(EventHandler):

    @logger.sectioner("Initialize Event Handler")
    def __init__(
        self,
        template_type: TemplateType,
        context_manager: github_contexts.GitHubContext,
        admin_token: str,
        # package_build: bool,
        # package_lint: bool,
        # package_test: bool,
        # website_build: bool,
        # meta_sync: str,
        # hooks: str,
        # website_announcement: str,
        # website_announcement_msg: str,
        # first_major_release: bool,
        path_repo_base: str,
        path_repo_head: str | None = None,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
        )
        # for arg_name, arg in (("meta_sync", meta_sync), ("hooks", hooks)):
        #     if arg not in ["report", "amend", "commit", "pull", "none", ""]:
        #         raise ValueError(
        #             f"Invalid input argument for '{arg_name}': "
        #             f"Expected one of 'report', 'amend', 'commit', 'pull', or 'none', but got '{arg}'."
        #         )
        # self._input_first_major_release = first_major_release
        # self._input_package_build = package_build
        # self._input_package_lint = package_lint
        # self._input_package_test = package_test
        # self._input_website_build = website_build
        # self._input_meta_sync = InitCheckAction(meta_sync or "none")
        # self._input_hooks = InitCheckAction(hooks or "none")
        # self._input_website_announcement = website_announcement
        # self._input_website_announcement_msg = website_announcement_msg

        self._payload: github_contexts.github.payloads.WorkflowDispatchPayload = self._context.event
        self._inputs = {
            WorkflowDispatchInput(k): _WORKFLOW_DISPATCH_INPUT_TYPE[WorkflowDispatchInput(k)](v)
            for k, v in self._payload.inputs.items()
        }
        return

    @logger.sectioner("Execute Event Handler", group=False)
    def _run_event(self):
        if WorkflowDispatchInput.RELEASE in self._inputs:
            return self._release_first_major_version()
        return self._action_default()

    def _release_first_major_version(self):
        if not self._context.ref_is_main:
            logger.critical("Cannot create first major release: not on main branch")
            return
        latest_ver, _ = self._get_latest_version(base=True)
        if latest_ver is None:
            logger.critical("Cannot create first major release: no previous version found")
            return
        if latest_ver.major != 0:
            logger.critical("Cannot create first major release: latest version's major is not 0")
            return
        control_center_manager = controlman.initialize_manager(
            git_manager=self._git_head,
            github_token=self._context.token,
            future_versions={self._context.ref_name: "1.0.0"},
        )
        ccm_main = control_center_manager.generate_data()
        hash_base = self._git_base.commit_hash_normal()
        self._action_meta(
            action=controlman.datatype.InitCheckAction.COMMIT,
            meta=control_center_manager,
            base=True,
            branch=controlman.datatype.Branch(type=controlman.datatype.BranchType.MAIN, name=self._context.ref_name)
        )
        changelog_manager = ChangelogManager(
            changelog_metadata=self._ccm_main["changelog"],
            ver_dist="1.0.0",
            commit_type=self._ccm_main["commit"]["primary_action"]["release_major"]["type"],
            commit_title="Release public API",
            parent_commit_hash=hash_base,
            parent_commit_url=self._gh_link.commit(hash_base),
            path_root=self._path_repo_base,
        )
        release_body = (
            "This is the first major release of the project, defining the stable public API. "
            "There has been no changes to the public API since the last release, i.e., "
            f"version {latest_ver}."
        )
        changelog_manager.add_entry(changelog_id="package_public", sections=release_body)
        changelog_manager.write_all_changelogs()
        hash_latest = self._git_base.push()
        tag = self._tag_version(ver="1.0.0", base=True)

        self._set_output(
            ccm_branch=ccm_main,
            ref=hash_latest,
            ref_before=self._context.hash_before,
            version="1.0.0",
            release_name=f"{ccm_main['name']} v1.0.0",
            release_tag=tag,
            release_body=release_body,
            website_deploy=True,
            package_lint=True,
            package_test=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
            package_release=True,
        )
        return

