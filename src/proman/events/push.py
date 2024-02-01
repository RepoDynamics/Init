import shutil

from github_contexts import GitHubContext
from github_contexts.github.payloads.push import PushPayload
from github_contexts.github.enums import RefType, ActionType
import conventional_commits

from repodynamics.action.events._base import EventHandler
from repodynamics.control.content import from_json_file
from repodynamics.control import ControlCenterContentManager
from repodynamics.logger import Logger
from repodynamics.datatype import (
    EventType,
    BranchType,
    Branch,
    InitCheckAction,
    TemplateType,
)
from repodynamics.control.manager import ControlCenterManager
from repodynamics.version import PEP440SemVer


class PushEventHandler(EventHandler):
    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str | None = None,
        logger: Logger | None = None,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
            logger=logger
        )
        self._payload: PushPayload = self._context.event

        self._ccm_main_before: ControlCenterContentManager | None = None
        return

    def _run_event(self):
        ref_type = self._context.ref_type
        action = self._payload.action
        if ref_type is RefType.BRANCH:
            if action is ActionType.CREATED:
                self._run_branch_created()
            elif action is ActionType.EDITED:
                self._run_branch_edited()
            elif action is ActionType.DELETED:
                self._run_branch_deleted()
            else:
                self.error_unsupported_triggering_action()
        elif ref_type is RefType.TAG:
            if action is ActionType.CREATED:
                self._run_tag_created()
            elif action is ActionType.DELETED:
                self._run_tag_deleted()
            elif action is ActionType.EDITED:
                self._run_tag_edited()
            else:
                self.error_unsupported_triggering_action()
        else:
            self._logger.error(
                f"Unsupported reference type for 'push' event.",
                "The workflow was triggered by a 'push' event, "
                f"but the reference type '{ref_type}' is not supported.",
            )
        return

    def _run_branch_created(self):
        if self._context.ref_is_main:
            if not self._git_head.get_tags():
                self._run_repository_created()
            else:
                self._logger.skip(
                    "Creation of default branch detected while a version tag is present; skipping.",
                    "This is likely a result of a repository transfer, or renaming of the default branch.",
                )
        else:
            self._logger.skip(
                "Creation of non-default branch detected; skipping.",
            )
        return

    def _run_repository_created(self):
        self._logger.info("Detected event: repository creation")
        meta = ControlCenterManager(path_repo=self._path_root_head, logger=self._logger)
        shutil.rmtree(meta.path_manager.dir_meta)
        shutil.rmtree(meta.path_manager.dir_website)
        (meta.path_manager.dir_docs / "website_template").rename(meta.path_manager.dir_website)
        (meta.path_manager.root / ".control_template").rename(meta.path_manager.dir_meta)
        shutil.rmtree(meta.path_manager.dir_local)
        meta.path_manager.file_path_meta.unlink(missing_ok=True)
        for path_dynamic_file in meta.path_manager.all_files:
            path_dynamic_file.unlink(missing_ok=True)
        for changelog_data in self._ccm_main.changelog.values():
            path_changelog_file = meta.path_manager.root / changelog_data["path"]
            path_changelog_file.unlink(missing_ok=True)
        if self._is_pypackit:
            shutil.rmtree(meta.path_manager.dir_source)
            shutil.rmtree(meta.path_manager.dir_tests)
        self._git_head.commit(
            message=f"init: Create repository from RepoDynamics {self._template_name_ver} template",
            stage="all"
        )
        self._git_head.push()
        self.add_summary(
            name="Init",
            status="pass",
            oneliner=f"Repository created from RepoDynamics {self._template_name_ver} template.",
        )
        return

    def _run_branch_edited(self):
        if self._context.ref_is_main:
            self._branch = Branch(type=BranchType.MAIN, name=self._context.ref_name)
            return self._run_branch_edited_main()
        # self._branch = self._ccm_main.get_branch_info_from_name(branch_name=self._context.ref_name)
        # self._git_head.fetch_remote_branches_by_name(branch_names=self._context.ref_name)
        # self._git_head.checkout(self._context.ref_name)
        # self._meta = Meta(
        #     path_root=self._path_root_base,
        #     github_token=self._context.token,
        #     hash_before=self._context.hash_before,
        #     logger=self._logger,
        # )
        # if self._branch.type == BranchType.RELEASE:
        #     self._event_type = EventType.PUSH_RELEASE
        #     return self._run_branch_edited_release()
        # if self._branch.type == BranchType.DEV:
        #     self._event_type = EventType.PUSH_DEV
        #     return self._run_branch_edited_dev()
        # if self._branch.type == BranchType.AUTOUPDATE:
        #     self._event_type = EventType.PUSH_CI_PULL
        #     return self._run_branch_edited_ci_pull()
        # self._event_type = EventType.PUSH_OTHER
        # return self._run_branch_edited_other()

    def _run_branch_edited_main(self):
        if not self._git_head.get_tags():
            # The repository is in the initialization phase
            if self._context.event.head_commit.message.startswith("init:"):
                # User is signaling the end of initialization phase
                return self._run_first_release()
            # User is still setting up the repository (still in initialization phase)
            return self._run_init_phase()
        self._ccm_main_before = from_json_file(
            path_repo=self._path_root_base,
            commit_hash=self._context.hash_before,
            git=self._git_base,
            logger=self._logger,
        )
        if not self._ccm_main_before:
            return self._run_existing_repository_initialized()
        return self._run_branch_edited_main_normal()

    def _run_init_phase(self, version: str = "0.0.0", finish: bool = True):
        meta = ControlCenterManager(
            path_repo=self._path_root_head,
            github_token=self._context.token,
            future_versions={self._context.ref_name: version},
            logger=self._logger,
        )
        self._ccm_main = meta.generate_data()
        self._config_repo()
        self._config_repo_pages()
        self._config_repo_labels_reset()
        hash_hooks = self._action_hooks(
            action=InitCheckAction.COMMIT,
            branch=self._branch,
            base=False,
            ref_range=(self._context.hash_before, self._context.hash_after),
        ) if self._ccm_main["workflow"].get("pre_commit", {}).get("main") else None
        hash_meta = self._action_meta(
            action=InitCheckAction.COMMIT,
            meta=meta,
            base=False,
            branch=self._branch
        )
        if finish:
            latest_hash = self._git_head.push() if hash_hooks or hash_meta else self._context.hash_after
            self._config_repo_branch_names(
                ccs_new=self._ccm_main.settings,
                ccs_old=from_json_file(
                    path_repo=self._path_root_head,
                    commit_hash=self._context.hash_before,
                    git=self._git_head,
                    logger=self._logger,
                ).settings
            )
            self._set_output(
                ccm_branch=self._ccm_main,
                ref=latest_hash,
                website_deploy=True,
                package_lint=self._is_pypackit,
                package_test=self._is_pypackit,
                package_build=self._is_pypackit,
            )
            return
        return

    def _run_first_release(self):
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
        commit_msg = conventional_commits.parser.create(types=["init"]).parse(head_commit_msg_final)
        if commit_msg.footer.get("version"):
            version_input = commit_msg.footer["version"]
            try:
                version = str(PEP440SemVer(version_input))
            except ValueError:
                self._logger.error(
                    f"Invalid version string in commit footer: {version_input}",
                    raise_error=False,
                )
                self._failed = True
                return
        else:
            version = "0.0.0"
        self._run_init_phase(version=version, finish=False)
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
        else:
            latest_hash = self._git_head.push() or self._context.hash_after
        self._tag_version(ver=version, msg=f"Release version {version}", base=False)
        self._config_repo_branch_names(
            ccs_new=self._ccm_main.settings,
            ccs_old=from_json_file(
                path_repo=self._path_root_head,
                commit_hash=self._context.hash_before,
                git=self._git_head,
                logger=self._logger,
            ).settings
        )
        self._config_rulesets(ccs_new=self._ccm_main.settings)
        self._set_output(
            ccm_branch=self._ccm_main,
            ref=latest_hash,
            version=version,
            website_deploy=True,
            package_publish_testpypi=self._is_pypackit,
            package_publish_pypi=self._is_pypackit,
        )
        return

    def _run_existing_repository_initialized(self):
        return

    def _run_branch_edited_main_normal(self):
        self._config_repo_labels_update(
            ccs_new=self._ccm_main.settings,
            ccs_old=self._ccm_main_before.settings,
        )
        self._config_rulesets(
            ccs_new=self._ccm_main.settings,
            ccs_old=self._ccm_main_before.settings,
        )
        if self._ccm_main.repo__config != self._ccm_main_before.repo__config:
            self._config_repo()
        if self._ccm_main.web__base_url != self._ccm_main_before.web__base_url:
            self._config_repo_pages()

        # self.action_repo_labels_sync()
        #
        # self.action_file_change_detector()
        # for job_id in ("package_build", "package_test_local", "package_lint", "website_build"):
        #     self.set_job_run(job_id)
        #
        # self.action_meta(action=metadata_raw["workflow"]["init"]["meta_check_action"][self.event_type.value])
        # self._action_hooks()
        # self.last_ver_main, self.dist_ver_main = self._get_latest_version()
        # commits = self._get_commits()
        # if len(commits) != 1:
        #     self._logger.error(
        #         f"Push event on main branch should only contain a single commit, but found {len(commits)}.",
        #         raise_error=False,
        #     )
        #     self.fail = True
        #     return
        # commit = commits[0]
        # if commit.group_data.group not in [CommitGroup.PRIMARY_ACTION, CommitGroup.PRIMARY_CUSTOM]:
        #     self._logger.error(
        #         f"Push event on main branch should only contain a single conventional commit, but found {commit}.",
        #         raise_error=False,
        #     )
        #     self.fail = True
        #     return
        # if self.fail:
        #     return
        #
        # if commit.group_data.group == CommitGroup.PRIMARY_CUSTOM or commit.group_data.action in [
        #     PrimaryActionCommitType.WEBSITE,
        #     PrimaryActionCommitType.META,
        # ]:
        #     ver_dist = f"{self.last_ver_main}+{self.dist_ver_main + 1}"
        #     next_ver = None
        # else:
        #     next_ver = self._get_next_version(self.last_ver_main, commit.group_data.action)
        #     ver_dist = str(next_ver)
        #
        # changelog_manager = ChangelogManager(
        #     changelog_metadata=self.metadata_main["changelog"],
        #     ver_dist=ver_dist,
        #     commit_type=commit.group_data.conv_type,
        #     commit_title=commit.msg.title,
        #     parent_commit_hash=self.hash_before,
        #     parent_commit_url=self._gh_link.commit(self.hash_before),
        #     path_root=self._path_root_self,
        #     logger=self._logger,
        # )
        # changelog_manager.add_from_commit_body(commit.msg.body)
        # changelog_manager.write_all_changelogs()
        # self.commit(amend=True, push=True)
        #
        # if next_ver:
        #     self._tag_version(ver=next_ver)
        #     for job_id in ("package_publish_testpypi", "package_publish_pypi", "github_release"):
        #         self.set_job_run(job_id)
        #     self._release_info["body"] = changelog_manager.get_entry("package_public")[0]
        #     self._release_info["name"] = f"{self.metadata_main['name']} {next_ver}"
        #
        # if commit.group_data.group == CommitGroup.PRIMARY_ACTION:
        #     self.set_job_run("website_deploy")
        return

    def _run_branch_edited_release(self):
        # self.event_type = EventType.PUSH_RELEASE
        # action_hooks = self.metadata["workflow"]["init"]["hooks_check_action"][self.event_type.value]
        return

    def _run_branch_edited_dev(self):
        # changed_file_groups = self._action_file_change_detector()
        # for file_type in (RepoFileType.SUPERMETA, RepoFileType.META, RepoFileType.DYNAMIC):
        #     if changed_file_groups[file_type]:
        #         self._action_meta()
        #         break
        # else:
        #     self._metadata_branch = read_from_json_file(path_root=self._path_root_base, logger=self._logger)
        # self._action_hooks()
        # commits = self._get_commits()
        # head_commit = commits[0]
        # if head_commit.group_data.group != CommitGroup.NON_CONV:
        #     footers = head_commit.msg.footer
        #     ready_for_review = footers.get("ready-for-review", False)
        #     if not isinstance(ready_for_review, bool):
        #         self._logger.error(
        #             f"Footer 'ready-for-review' should be a boolean, but found {ready_for_review}.",
        #             raise_error=False,
        #         )
        #         self._failed = True
        #         return
        #     if ready_for_review:
        #         if self._metadata_main["repo"]["full_name"] == self._context.repository:
        #             # workflow is running from own repository
        #             matching_pulls = self._gh_api.pull_list(
        #                 state="open",
        #                 head=f"{self._context.repository_owner}:{self._context.ref_name}",
        #                 base=self._branch.suffix[1],
        #             )
        #             if not matching_pulls:
        #                 self._gh_api.pull_create(
        #                     title=head_commit.msg.title,
        #                     body=head_commit.msg.body,
        #                     head=self._context.ref_name,
        #                     base=self._branch.suffix[1],
        #                 )
        #             elif len(matching_pulls) != 1:
        #                 self._logger.error(
        #                     f"Found {len(matching_pulls)} matching pull requests, but expected 0 or 1.",
        #                     raise_error=False,
        #                 )
        #                 self._failed = True
        #                 return
        #             else:
        #                 self._gh_api.pull_update(
        #                     number=matching_pulls[0]["number"],
        #                     draft=False,
        #                 )
        #             return
        # if changed_file_groups[RepoFileType.WEBSITE]:
        #     self._set_job_run(website_build=True)
        # if changed_file_groups[RepoFileType.TEST]:
        #     self._set_job_run(package_test_local=True)
        # if changed_file_groups[RepoFileType.PACKAGE]:
        #     self._set_job_run(
        #         package_build=True,
        #         package_lint=True,
        #         package_test_local=True,
        #         website_build=True,
        #         package_publish_testpypi=True,
        #     )
        # elif any(
        #     filepath in changed_file_groups[RepoFileType.DYNAMIC]
        #     for filepath in (
        #         RelativePath.file_python_pyproject,
        #         RelativePath.file_python_manifest,
        #     )
        # ):
        #     self._set_job_run(
        #         package_build=True,
        #         package_lint=True,
        #         package_test_local=True,
        #         package_publish_testpypi=True,
        #     )
        # if self._job_run_flag["package_publish_testpypi"]:
        #     issue_labels = [
        #         label["name"] for label in self._gh_api.issue_labels(number=self._branch.suffix[0])
        #     ]
        #     final_commit_type = self._metadata_main.get_issue_data_from_labels(issue_labels).group_data
        #     if final_commit_type.group == CommitGroup.PRIMARY_CUSTOM or final_commit_type.action in (
        #         PrimaryActionCommitType.WEBSITE,
        #         PrimaryActionCommitType.META,
        #     ):
        #         self._set_job_run(package_publish_testpypi=False)
        #         return
        #     self._git_head.fetch_remote_branches_by_name(branch_names=self._branch.suffix[1])
        #     ver_last_target, _ = self._get_latest_version(branch=self._branch.suffix[1])
        #     ver_last_dev, _ = self._get_latest_version(dev_only=True)
        #     if ver_last_target.pre:
        #         next_ver = ver_last_target.next_post
        #         if not ver_last_dev or (
        #             ver_last_dev.release != next_ver.release or ver_last_dev.pre != next_ver.pre
        #         ):
        #             dev = 0
        #         else:
        #             dev = (ver_last_dev.dev or -1) + 1
        #         next_ver_str = f"{next_ver}.dev{dev}"
        #     else:
        #         next_ver = self._get_next_version(ver_last_target, final_commit_type.action)
        #         next_ver_str = str(next_ver)
        #         if final_commit_type.action != PrimaryActionCommitType.RELEASE_POST:
        #             next_ver_str += f".a{self._branch.suffix[0]}"
        #         if not ver_last_dev:
        #             dev = 0
        #         elif final_commit_type.action == PrimaryActionCommitType.RELEASE_POST:
        #             if ver_last_dev.post is not None and ver_last_dev.post == next_ver.post:
        #                 dev = ver_last_dev.dev + 1
        #             else:
        #                 dev = 0
        #         elif ver_last_dev.pre is not None and ver_last_dev.pre == ("a", self._branch.suffix[0]):
        #             dev = ver_last_dev.dev + 1
        #         else:
        #             dev = 0
        #         next_ver_str += f".dev{dev}"
        #     self._tag_version(
        #         ver=PEP440SemVer(next_ver_str),
        #         msg=f"Developmental release (issue: #{self._branch.suffix[0]}, target: {self._branch.suffix[1]})",
        #     )
        return

    def _run_branch_edited_other(self):
        # changed_file_groups = self._action_file_change_detector()
        # for file_type in (RepoFileType.SUPERMETA, RepoFileType.META, RepoFileType.DYNAMIC):
        #     if changed_file_groups[file_type]:
        #         self._action_meta()
        #         break
        # else:
        #     self._metadata_branch = read_from_json_file(path_root=self._path_root_self, logger=self._logger)
        # self._action_hooks()
        # if changed_file_groups[RepoFileType.WEBSITE]:
        #     self._set_job_run(website_build=True)
        # if changed_file_groups[RepoFileType.TEST]:
        #     self._set_job_run(package_test_local=True)
        # if changed_file_groups[RepoFileType.PACKAGE]:
        #     self._set_job_run(
        #         package_build=True,
        #         package_lint=True,
        #         package_test_local=True,
        #         website_build=True,
        #     )
        # elif any(
        #     filepath in changed_file_groups[RepoFileType.DYNAMIC]
        #     for filepath in (
        #         RelativePath.file_python_pyproject,
        #         RelativePath.file_python_manifest,
        #     )
        # ):
        #     self._set_job_run(
        #         package_build=True,
        #         package_lint=True,
        #         package_test_local=True,
        #     )
        return

    def _run_branch_deleted(self):
        return

    def _run_tag_created(self):
        return

    def _run_tag_deleted(self):
        return

    def _run_tag_edited(self):
        return
