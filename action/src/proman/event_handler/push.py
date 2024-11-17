"""Push event handler."""
from __future__ import annotations as _annotations
from typing import TYPE_CHECKING
import shutil

from github_contexts import github as _gh_context
from loggerman import logger
import controlman
import fileex as _fileex

from proman.dstruct import Version
from proman.dtype import InitCheckAction
from proman.main import EventHandler
from versionman.pep440_semver import PEP440SemVer

if TYPE_CHECKING:
    from pathlib import Path


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
            logger.info(
                "Head Commit",
                repr(self.head_commit_msg)
            )
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

        def move_and_merge_directories(src: Path, dest: Path):
            """
            Moves the source directory into the destination directory.
            All files and subdirectories from src will be moved to dest.
            Existing files in dest will be overwritten.

            Parameters:
                src (str): Path to the source directory.
                dest (str): Path to the destination directory.
            """
            for item in src.iterdir():
                dest_item = dest / item.name
                if item.is_dir():
                    if dest_item.exists():
                        # Merge the subdirectory
                        move_and_merge_directories(item, dest_item)
                    else:
                        shutil.move(str(item), str(dest_item))  # Move the whole directory
                else:
                    # Move or overwrite the file
                    if dest_item.exists():
                        dest_item.unlink()  # Remove the existing file
                    shutil.move(str(item), str(dest_item))
            # Remove the source directory if it's empty
            src.rmdir()
            return

        with logger.sectioning("Repository Preparation"):
            _fileex.directory.delete_contents(
                path=self._path_head,
                exclude=[".git", ".github", "template"],
            )
            _fileex.directory.delete_contents(
                path=self._path_head / ".github",
                exclude=["workflows"],
            )
            move_and_merge_directories(self._path_head / "template", self._path_head)
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
        version = self.head_commit_msg.footer.version or PEP440SemVer("0.0.0")
        version_tag = self.manager.release.create_version_tag(version)
        self.manager.release.github.get_or_make_draft(tag=version_tag)
        self.manager.release.zenodo.get_or_make_drafts()
        self.manager.changelog.update_version(str(version))
        self.manager.changelog.update_date()

        hash_after = self.gh_context.hash_after
        vars_is_updated = self.manager.variable.write_file()
        if vars_is_updated:
            hash_after = self.manager.git.commit(
                message=self.manager.commit.create_auto("vars_sync")
            )
        changelog_is_updated = self.manager.changelog.write_file()
        if changelog_is_updated:
            hash_after = self.manager.git.commit(
                message=self.manager.commit.create_auto("changelog_sync")
            )
        self.run_change_detection(branch_manager=self.manager)
        new_manager, commit_hash_cca = self.run_cca(
            branch_manager=self.manager,
            action=InitCheckAction.COMMIT,
            future_versions={self.gh_context.ref_name: version},
        )
        commit_hash_refactor = self.run_refactor(
            branch_manager=new_manager,
            action=InitCheckAction.COMMIT,
            ref_range=(self.gh_context.hash_before, hash_after),
        ) if new_manager.data["tool.pre-commit.config.file.content"] else None
        new_manager.repo.update_all(manager_before=self.manager, update_rulesets=False)
        gh_release_output, _ = new_manager.release.github.update_draft(tag=version_tag, on_main=True)
        zenodo_output, zenodo_sandbox_output, _, _ = new_manager.release.zenodo.update_drafts(version=version)
        self._output_manager.set(
            main_manager=new_manager,
            branch_manager=new_manager,
            version=version_tag,
            ref=commit_hash_refactor or commit_hash_cca or hash_after,
            website_deploy=True,
            package_lint=True,
            test_lint=True,
            package_test=True,
            package_build=True,
            github_release_config=gh_release_output,
            zenodo_config=zenodo_output,
            zenodo_sandbox_config=zenodo_sandbox_output,
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
        version_tag = new_manager.release.tag_version(
            ver=version,
            env_vars={"ccc": new_manager},
        )
        new_manager.repo.update_all(manager_before=self.manager)
        self._output_manager.set(
            main_manager=new_manager,
            branch_manager=new_manager,
            version=version_tag,
            ref=latest_hash,
            website_deploy=True,
            package_lint=True,
            test_lint=True,
            package_test=True,
            package_publish_testpypi=True,
            package_publish_pypi=True,
        )
        return

    def _run_branch_edited_fork(self):
        self.reporter.event("CI on fork")
        branch_manager = self.manager_from_metadata_file(repo="head")
        new_manager, job_runs, latest_hash = self.run_sync_fix(
            branch_manager=branch_manager,
            action=InitCheckAction.COMMIT,
        )
        website_deploy = False
        if self._has_admin_token:
            new_manager.repo.activate_gh_pages()
            if job_runs["web_build"]:
                website_deploy = True
            new_manager.repo.update_all(
                manager_before=branch_manager,
                update_rulesets=False,
            )
        self._output_manager.set(
            main_manager=new_manager,
            branch_manager=new_manager,
            website_deploy=website_deploy,

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
