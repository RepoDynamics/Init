"""Push event handler."""

import shutil

import conventional_commits
from github_contexts import github as _gh_context
from loggerman import logger
import controlman
from versionman.pep440_semver import PEP440SemVer
import fileex as _fileex

from proman.datatype import InitCheckAction
from proman.main import EventHandler


class PushEventHandler(EventHandler):
    """Push event handler.

    This handler is responsible for the setup process of new and existing repositories.
    It also runs Continuous pipelines on forked repositories.
    """

    @logger.sectioner("Initialize Event Handler")
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: _gh_context.payload.PushPayload = self._context.event
        return

    @logger.sectioner("Execute Push Handler", group=False)
    def _run_event(self):
        if self._context.ref_type is not _gh_context.enum.RefType.BRANCH:
            self._reporter.event(
                f"Push to tag `{self._context.ref_name}`"
            )
            self._reporter.add(
                name="main",
                status="skip",
                summary="Push to tags does not trigger the workflow."
            )
            return
        action = self._payload.action
        if action not in (_gh_context.enum.ActionType.CREATED, _gh_context.enum.ActionType.EDITED):
            self._reporter.event(
                f"Deletion of branch `{self._context.ref_name}`"
            )
            self._reporter.add(
                name="main",
                status="skip",
                summary="Branch deletion does not trigger the workflow.",
            )
            return
        is_main = self._context.ref_is_main
        has_tags = bool(self._git_head.get_tags())
        if action is _gh_context.enum.ActionType.CREATED:
            if not is_main:
                self._reporter.event(f"Creation of branch `{self._context.ref_name}`")
                self._reporter.add(
                    name="main",
                    status="skip",
                    summary="Branch creation does not trigger the workflow.",
                )
                return
            if not has_tags:
                return self._run_repository_created()
            self._reporter.event(f"Creation of default branch `{self._context.ref_name}`")
            self._reporter.add(
                name="main",
                status="skip",
                summary="Default branch created while a version tag is present. "
                        "This is likely a result of renaming the default branch.",
            )
            return
        # Branch edited
        if self._context.event.repository.fork:
            return self._run_branch_edited_fork()
        if not is_main:
            self._reporter.event(f"Modification of branch `{self._context.ref_name}`")
            self._reporter.add(
                name="main",
                status="skip",
                summary="Modification of non-default branches does not trigger the workflow.",
            )
            return
        # Main branch edited
        if not has_tags:
            # The repository is in the initialization phase
            if self._context.event.head_commit.message.startswith("init:"):
                # User is signaling the end of initialization phase
                return self._run_first_release()
            # User is still setting up the repository (still in initialization phase)
            return self._run_init_phase()
        return self._run_branch_edited_main_normal()

    def _run_repository_created(self):
        self._reporter.event("Repository creation")
        _fileex.directory.delete_contents(
            path=self._path_head,
            exclude=[".github", "template"],
        )
        _fileex.directory.delete_contents(
            path=self._path_head / ".github",
            exclude=["workflows"],
        )
        template_dir = self._path_head / "template"
        for item in template_dir.iterdir():
            shutil.move(item, self._path_head)
        shutil.rmtree(template_dir)
        self._git_head.commit(message="temp", amend=True, stage="all")
        cc_manager = controlman.manager(
            repo=self._git_head,
            github_token=self._context.token,
            future_versions={self._context.event.repository.default_branch: "0.0.0"},
            control_center_path=".control"
        )
        self._sync(
            action=InitCheckAction.AMEND,
            cc_manager=cc_manager,
            base=False,
            commit_msg=f"init: Create repository from RepoDynamics template.",
        )
        data = cc_manager.generate_data()
        self._git_head.push()
        self._repo_config.reset_labels(data=data)
        self._reporter.add(
            name="main",
            status="pass",
            summary=f"Repository created from RepoDynamics template.",
        )
        return

    def _run_init_phase(self):
        self._reporter.event("Repository initialization phase")
        new_data, job_runs, latest_hash = self.run_sync_fix(
            action=InitCheckAction.COMMIT,
            future_versions={self._context.ref_name: "0.0.0"},
        )
        self._repo_config.update_all(data_new=new_data, data_old=self._data_main, rulesets="ignore")
        self._output.set(
            data_branch=new_data,
            ref=latest_hash,
            website_deploy=True,
            package_lint=True,
            package_test=True,
            package_build=True,
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
                    f"init: Initialize project from RepoDynamics template."
                )
                head_commit_msg_final = "\n".join(head_commit_msg_lines)
            return conventional_commits.parser.create(types=["init"]).parse(head_commit_msg_final)

        def parse_version() -> str:
            if commit_msg.footer.get("version"):
                version_input = commit_msg.footer["version"]
                try:
                    return str(PEP440SemVer(version_input))
                except ValueError:
                    logger.critical(f"Invalid version string in commit footer: {version_input}")
            return "0.0.0"

        self._reporter.event("Project initialization")
        commit_msg = parse_commit_msg()
        version = parse_version()
        new_data, job_runs, latest_hash = self.run_sync_fix(
            action=InitCheckAction.COMMIT,
            future_versions={self._context.ref_name: version},
        )
        if commit_msg.footer.get("squash", True):
            # Squash all commits into a single commit
            # Ref: https://blog.avneesh.tech/how-to-delete-all-commit-history-in-github
            #      https://stackoverflow.com/questions/55325930/git-how-to-squash-all-commits-on-master-branch
            self._git_head.checkout("temp", orphan=True)
            self._git_head.commit(message=commit_msg.footerless)
            self._git_head.branch_delete(self._context.ref_name, force=True)
            self._git_head.branch_rename(self._context.ref_name, force=True)
            latest_hash = self._git_head.push(
                target="origin", ref=self._context.ref_name, force_with_lease=True
            )
        data_main_before = self._data_main
        self._data_main = new_data
        self._tag_version(ver=version, msg=f"Release Version {version}", base=False)
        self._repo_config.update_all(data_new=new_data, data_old=data_main_before, rulesets="create")
        self._output.set(
            data_branch=new_data,
            ref=latest_hash,
            version=version,
            website_deploy=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
        )
        return

    def _run_branch_edited_fork(self):
        self._reporter.event("CI on fork")
        new_data, job_runs, latest_hash = self.run_sync_fix(action=InitCheckAction.COMMIT)
        website_deploy = False
        if self._has_admin_token:
            self._repo_config.activate_gh_pages()
            if job_runs["website_build"]:
                website_deploy = True
            if self._context.ref_is_main:
                self._repo_config.update_all(
                    data_new=new_data,
                    data_old=self._data_main,
                    rulesets="ignore"
                )
        self._output.set(
            data_branch=new_data,
            ref=latest_hash,
            ref_before=self._context.hash_before,
            website_deploy=website_deploy,
            **job_runs,
        )
        return

    def _run_branch_edited_main_normal(self):
        self._reporter.event("Repository configuration synchronization")
        self._repo_config.update_all(
            data_new=self._data_main,
            data_old=controlman.from_json_file_at_commit(
                git_manager=self._git_head,
                commit_hash=self._context.hash_before,
            )
        )
        return
