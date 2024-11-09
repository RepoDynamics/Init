"""Push event handler."""
from __future__ import annotations as _annotations

import shutil

from github_contexts import github as _gh_context
from loggerman import logger
import controlman
import fileex as _fileex

from proman.dtype import InitCheckAction
from proman.main import EventHandler


class PushEventHandler(EventHandler):
    """Push event handler.

    This handler is responsible for the setup process of new and existing repositories.
    It also runs Continuous pipelines on forked repositories.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.payload: _gh_context.payload.PushPayload = self.gh_context.event
        self.head_commit = self.gh_context.event.head_commit
        if self.manager and self.head_commit:
            self.head_commit_msg = self.manager.commit.create_from_msg(self.head_commit.message)
        return

    @logger.sectioner("Push Handler Execution")
    def run(self):
        if self.head_commit and self.head_commit.committer.username == "RepoDynamicsBot":
            self.reporter.add(
                name="event",
                status="skip",
                summary="Automated commit by RepoDynamicsBot.",
            )
            return
        if self.gh_context.ref_type is not _gh_context.enum.RefType.BRANCH:
            self.reporter.event(
                f"Push to tag `{self.gh_context.ref_name}`"
            )
            self.reporter.add(
                name="event",
                status="skip",
                summary="Push to tags does not trigger the workflow."
            )
            return
        action = self.payload.action
        if action not in (_gh_context.enum.ActionType.CREATED, _gh_context.enum.ActionType.EDITED):
            self.reporter.event(
                f"Deletion of branch `{self.gh_context.ref_name}`"
            )
            self.reporter.add(
                name="event",
                status="skip",
                summary="Branch deletion does not trigger the workflow.",
            )
            return
        is_main = self.gh_context.ref_is_main
        has_tags = bool(self._git_head.get_tags())
        if action is _gh_context.enum.ActionType.CREATED:
            if not is_main:
                self.reporter.event(f"Creation of branch `{self.gh_context.ref_name}`")
                self.reporter.add(
                    name="event",
                    status="skip",
                    summary="Branch creation does not trigger the workflow.",
                )
                return
            if not has_tags:
                self.reporter.event("Repository creation")
                return self._run_repository_creation()
            self.reporter.event(f"Creation of default branch `{self.gh_context.ref_name}`")
            self.reporter.add(
                name="event",
                status="skip",
                summary="Default branch created while a git tag is present. "
                        "This is likely a result of renaming the default branch.",
            )
            return
        # Branch edited
        if self.gh_context.event.repository.fork:
            return self._run_branch_edited_fork()
        if not is_main:
            self.reporter.event(f"Modification of branch `{self.gh_context.ref_name}`")
            self.reporter.add(
                name="event",
                status="skip",
                summary="Modification of non-default branches does not trigger the workflow.",
            )
            return
        # Main branch edited
        if not has_tags:
            # The repository is in the initialization phase
            if self.head_commit_msg.footer.initialize_project:
                # User is signaling the end of initialization phase
                return self._run_first_release()
            # User is still setting up the repository (still in initialization phase)
            self.reporter.event("Repository initialization phase")
            return self._run_init_phase()
        return self._run_branch_edited_main_normal()

    def _run_repository_creation(self):
        with logger.sectioning("Repository Preparation"):
            _fileex.directory.delete_contents(
                path=self._path_head,
                exclude=[".git", ".github", "template"],
            )
            _fileex.directory.delete_contents(
                path=self._path_head / ".github",
                exclude=["workflows"],
            )
            template_dir = self._path_head / "template"
            for item in template_dir.iterdir():
                shutil.move(item, self._path_head)
            shutil.rmtree(template_dir)
            self._git_head.commit(
                message=f"init: Create repository from RepoDynamics template v{self.current_proman_version}.",
                amend=True,
                stage="all"
            )
        main_manager, _ = self.run_cca(
            branch_manager=None,
            action=InitCheckAction.AMEND,
            future_versions={self.gh_context.event.repository.default_branch: "0.0.0"},
        )
        with logger.sectioning("Repository Update"):
            main_manager.git.push(force_with_lease=True)
        main_manager.repo.reset_labels()
        self.reporter.add(
            name="event",
            status="pass",
            summary=f"Repository created from RepoDynamics template.",
        )
        return

    def _run_init_phase(self):
        version = "0.0.0"
        new_manager, job_runs, latest_hash = self.run_sync_fix(
            branch_manager=self.manager,
            action=InitCheckAction.COMMIT,
            future_versions={self.gh_context.ref_name: version},
        )
        new_manager.repo.update_all(manager_before=self.manager, update_rulesets=False)
        self._output_manager.set(
            main_manager=new_manager,
            branch_manager=new_manager,
            version=version,
            ref=latest_hash,
            website_deploy=True,
            package_lint=True,
            test_lint=True,
            package_test=True,
            package_build=True,
        )
        return

    def _run_first_release(self):
        self.reporter.event("Project initialization")
        version = self.head_commit_msg.footer.version or "0.0.0"
        new_manager, job_runs, latest_hash = self.run_sync_fix(
            branch_manager=self.manager,
            action=InitCheckAction.COMMIT,
            future_versions={self.gh_context.ref_name: version},
        )
        # By default, squash all commits into a single commit
        if self.head_commit_msg.footer.squash is not False:
            # Ref: https://blog.avneesh.tech/how-to-delete-all-commit-history-in-github
            #      https://stackoverflow.com/questions/55325930/git-how-to-squash-all-commits-on-master-branch
            new_manager.git.checkout("temp", orphan=True)
            new_manager.git.commit(message=str(self.head_commit_msg))
            new_manager.git.branch_delete(self.gh_context.ref_name, force=True)
            new_manager.git.branch_rename(self.gh_context.ref_name, force=True)
            new_manager.git.push(
                target="origin", ref=self.gh_context.ref_name, force_with_lease=True
            )
            latest_hash = new_manager.git.commit_hash_normal()
        self._tag_version(
            ver=version,
            base=False,
            env_vars={"ccc": new_manager},
        )
        new_manager.repo.update_all(manager_before=self.manager)
        self._output_manager.set(
            main_manager=new_manager,
            branch_manager=new_manager,
            ref=latest_hash,
            version=str(version),
            website_deploy=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
        )
        return

    def _run_branch_edited_fork(self):
        self.reporter.event("CI on fork")
        new_manager, job_runs, latest_hash = self.run_sync_fix(action=InitCheckAction.COMMIT)
        website_deploy = False
        if self._has_admin_token:
            self.repo_manager.activate_gh_pages()
            if job_runs["website_build"]:
                website_deploy = True
            if self.gh_context.ref_is_main:
                self.repo_manager.update_all(
                    data_new=new_manager,
                    data_old=self._data_main,
                    rulesets="ignore"
                )
        self._output_manager.set(
            data_branch=new_manager,
            ref=latest_hash,
            ref_before=self.gh_context.hash_before,
            website_deploy=website_deploy,
            **job_runs,
        )
        return

    def _run_branch_edited_main_normal(self):
        self.reporter.event("Repository configuration synchronization")
        self.manager.repo.update_all(
            manager_before=self.manager_from_metadata_file(
                repo="base",
                commit_hash=self.gh_context.hash_before,
            )
        )
        return
