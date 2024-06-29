"""Push event handler."""


import shutil

import conventional_commits
from github_contexts import GitHubContext
from github_contexts.github.payloads.push import PushPayload
from github_contexts.github.enums import RefType, ActionType
from loggerman import logger
import controlman
import versionman

from proman.datatype import TemplateType
from proman.handler.main import EventHandler


class PushEventHandler(EventHandler):
    """Push event handler.

    This handler is responsible for the setup process of new and existing repositories.
    It also runs Continuous pipelines on forked repositories.
    """

    @logger.sectioner("Initialize Event Handler")
    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
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
        self._payload: PushPayload = self._context.event

        self._ccm_main_before: controlman.ControlCenterContentManager | None = None
        return

    @logger.sectioner("Execute Event Handler", group=False)
    def _run_event(self):
        if self._context.ref_type is not RefType.BRANCH:
            logger.notice(
                f"Non-branch reference type for 'push' event.",
                "The workflow was triggered by a 'push' event, "
                f"but the reference type was '{self._context.ref_type}'.",
            )
            return
        action = self._payload.action
        if action not in (ActionType.CREATED, ActionType.EDITED):
            logger.notice(f"Unsupported action '{action.value}' for 'push' event to branch.")
            return
        is_main = self._context.ref_is_main
        has_tags = bool(self._git_head.get_tags())
        if action is ActionType.CREATED:
            if not is_main:
                logger.notice("Creation of non-default branch detected; skipping.")
                return
            if not has_tags:
                return self._run_repository_created()
            logger.notice(
                "Creation of default branch detected while a version tag is present; skipping.",
                "This is likely a result of renaming the default branch.",
            )
            return
        # Branch edited
        if self._context.event.repository.fork:
            return self._run_branch_edited_fork()
        if not is_main:
            if self._ccm_main:
                logger.notice("Canceled Workflow", "Push to non-main branch.")
                return
            return self._run_init_existing_nonmain()
        # Main branch edited
        self._ccm_main_before = controlman.read_from_json_file_at_commit(
            commit_hash=self._context.hash_before,
            git_manager=self._git_head,
        )
        if not self._ccm_main_before:
            return self._run_init_existing_main()
        if not has_tags:
            # The repository is in the initialization phase
            if self._context.event.head_commit.message.startswith("init:"):
                # User is signaling the end of initialization phase
                return self._run_first_release()
            # User is still setting up the repository (still in initialization phase)
            return self._run_init_phase()
        return self._run_branch_edited_main_normal()

    def _run_repository_created(self):
        logger.info("Detected event", "repository creation")
        cc_manager = self.get_cc_manager(
            future_versions={self._context.event.repository.default_branch: "0.0.0"}
        )
        shutil.rmtree(cc_manager.path_manager.dir_meta)
        shutil.rmtree(cc_manager.path_manager.dir_website)
        (cc_manager.path_manager.dir_docs / "website_template").rename(cc_manager.path_manager.dir_website)
        (cc_manager.path_manager.root / ".control_template").rename(cc_manager.path_manager.dir_meta)
        shutil.rmtree(cc_manager.path_manager.dir_local)
        cc_manager.path_manager.file_path_meta.unlink(missing_ok=True)
        for path_dynamic_file in cc_manager.path_manager.all_files:
            path_dynamic_file.unlink(missing_ok=True)
        for changelog_data in self._ccm_main.changelog.values():
            path_changelog_file = cc_manager.path_manager.root / changelog_data["path"]
            path_changelog_file.unlink(missing_ok=True)
        if self._is_pypackit:
            shutil.rmtree(cc_manager.path_manager.dir_source)
            shutil.rmtree(cc_manager.path_manager.dir_tests)
        self._git_head.commit(
            message=f"init: Create repository from RepoDynamics {self._template_name_ver} template",
            stage="all"
        )
        self._git_head.push()
        self._action_meta(
            action=controlman.datatype.InitCheckAction.COMMIT,
            cc_manager=cc_manager,
            base=False,
        )
        self._git_head.push()
        ccm = cc_manager.generate_data()
        self._repo_config.reset_labels(ccs=ccm.content)
        self.add_summary(
            name="Init",
            status="pass",
            oneliner=f"Repository created from RepoDynamics {self._template_name_ver} template.",
        )
        return

    def _run_init_phase(self):
        job_runs, ccm_branch, latest_hash = self.run_sync_fix(
            action=controlman.datatype.InitCheckAction.COMMIT,
            branch=controlman.datatype.Branch(
                type=controlman.datatype.BranchType.MAIN, name=self._context.ref_name
            ),
            version="0.0.0"
        )
        self._ccm_main = ccm_branch
        self._repo_config.update_all(ccm_new=self._ccm_main, ccm_old=self._ccm_main_before, rulesets="ignore")
        self._output.set(
            ccm_branch=self._ccm_main,
            ref=latest_hash,
            website_deploy=True,
            package_lint=self._is_pypackit,
            package_test=self._is_pypackit,
            package_build=self._is_pypackit,
            website_url=self._ccm_main["url"]["website"]["base"],
        )
        return

    def _run_init_existing_nonmain(self):
        job_runs, ccm_branch, latest_hash = self.run_sync_fix(action=controlman.datatype.InitCheckAction.COMMIT)
        self._ccm_main = ccm_branch
        self._output.set(
            ccm_branch=ccm_branch,
            ref=latest_hash,
            ref_before=self._context.hash_before,
            website_url=self._ccm_main["url"]["website"]["base"],
            **job_runs,
        )
        return

    def _run_init_existing_main(self):
        job_runs, ccm_branch, latest_hash = self.run_sync_fix(action=controlman.datatype.InitCheckAction.COMMIT)
        self._ccm_main = ccm_branch
        self._repo_config.update_all(ccm_new=self._ccm_main, ccm_old=self._ccm_main, rulesets="create")
        self._output.set(
            ccm_branch=ccm_branch,
            ref=latest_hash,
            ref_before=self._context.hash_before,
            website_deploy=True,
            website_url=self._ccm_main["url"]["website"]["base"],
            **job_runs,
        )
        return

    def _run_first_release(self):

        def parse_commit_msg() -> conventional_commits.message.ConventionalCommitMessage:
            head_commit_msg = self._context.event.head_commit.message
            head_commit_msg_lines = head_commit_msg.splitlines()
            head_commit_summary = head_commit_msg_lines[0]
            if head_commit_summary.removeprefix("init:").strip():
                head_commit_msg_final = head_commit_msg
            else:
                head_commit_msg_lines[0] = (
                    f"init: Initialize project from RepoDynamics {self._template_name_ver} template"
                )
                head_commit_msg_final = "\n".join(head_commit_msg_lines)
            return conventional_commits.parser.create(types=["init"]).parse(head_commit_msg_final)

        def parse_version() -> str:
            if commit_msg.footer.get("version"):
                version_input = commit_msg.footer["version"]
                try:
                    return str(versionman.PEP440SemVer(version_input))
                except ValueError:
                    logger.critical(f"Invalid version string in commit footer: {version_input}")
            return "0.0.0"

        commit_msg = parse_commit_msg()
        version = parse_version()
        job_runs, ccm_branch, latest_hash = self.run_sync_fix(
            action=controlman.datatype.InitCheckAction.COMMIT,
            branch=controlman.datatype.Branch(
                type=controlman.datatype.BranchType.MAIN, name=self._context.ref_name
            ),
            version=version
        )
        self._ccm_main = ccm_branch
        if commit_msg.footer.get("squash", True):
            # Squash all commits into a single commit
            # Ref: https://blog.avneesh.tech/how-to-delete-all-commit-history-in-github
            #      https://stackoverflow.com/questions/55325930/git-how-to-squash-all-commits-on-master-branch
            self._git_head.checkout("temp", orphan=True)
            self._git_head.commit(
                message=f"init: Initialize project from RepoDynamics {self._template_name_ver} template",
            )
            self._git_head.branch_delete(self._context.ref_name, force=True)
            self._git_head.branch_rename(self._context.ref_name, force=True)
            latest_hash = self._git_head.push(
                target="origin", ref=self._context.ref_name, force_with_lease=True
            )
        self._tag_version(ver=version, msg=f"Release version {version}", base=False)
        self._repo_config.update_all(ccm_new=self._ccm_main, ccm_old=self._ccm_main_before, rulesets="create")
        self._output.set(
            ccm_branch=self._ccm_main,
            ref=latest_hash,
            version=version,
            website_deploy=True,
            package_publish_testpypi=self._is_pypackit,
            package_publish_pypi=self._is_pypackit,
            website_url=self._ccm_main["url"]["website"]["base"],
        )
        return

    def _run_branch_edited_fork(self):
        # if (self._path_repo_base / "FORK_TEST_MODE").is_file():
        #     return
        job_runs, ccm_branch, latest_hash = self.run_sync_fix(action=controlman.datatype.InitCheckAction.COMMIT)
        website_deploy = False
        if self._has_admin_token:
            self._repo_config.activate_gh_pages()
            if job_runs["website_build"]:
                website_deploy = True
            if self._context.ref_is_main:
                ccm_main_before = self._ccm_main
                self._ccm_main = ccm_branch
                self._repo_config.update_all(
                    ccm_new=self._ccm_main,
                    ccm_old=ccm_main_before,
                    rulesets="ignore"
                )
        self._output.set(
            ccm_branch=ccm_branch,
            ref=latest_hash,
            ref_before=self._context.hash_before,
            website_deploy=website_deploy,
            **job_runs,
        )
        return

    def _run_branch_edited_main_normal(self):
        self._repo_config.update_all(ccm_new=self._ccm_main, ccm_old=self._ccm_main_before)
        return
