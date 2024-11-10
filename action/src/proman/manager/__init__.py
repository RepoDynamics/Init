from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime

import jinja2

import controlman
from controlman.cache_manager import CacheManager
from loggerman import logger

from proman.exception import ProManException
#from proman.manager.announcement import AnnouncementManager
from proman.manager.branch import BranchManager
from proman.manager.changelog import ChangelogManager
from proman.manager.commit import CommitManager
from proman.manager.issue import IssueManager
from proman.manager.label import LabelManager
from proman.manager.protocol import ProtocolManager
from proman.manager.release import ReleaseManager
from proman.manager.repo import RepoManager
from proman.manager.user import UserManager

if _TYPE_CHECKING:
    from github_contexts import GitHubContext
    from gittidy import Git
    from pyserials.nested_dict import NestedDict
    from pylinks.api.github import Repo as GitHubRepoAPI
    from proman.report import Reporter


class Manager:

    def __init__(
        self,
        data: NestedDict,
        git_api: Git,
        jinja_env_vars: dict,
        github_context: GitHubContext,
        github_api_actions: GitHubRepoAPI,
        github_api_admin: GitHubRepoAPI,
    ):
        self._data = data
        self._git = git_api
        self._jinja_env_vars = jinja_env_vars
        self._gh_context = github_context
        self._gh_api_actions = github_api_actions
        self._gh_api_admin = github_api_admin

        self._cache_manager = CacheManager(
            path_local_cache=self._git.repo_path / data["local.cache.path"],
            retention_hours=data["control.cache.retention.hours"],
        )
        self._branch_manager = BranchManager(self)
        self._changelog_manager = ChangelogManager(self)
        self._commit_manager = CommitManager(self)
        self._issue_manager = IssueManager(self)
        self._label_manager = LabelManager(self)
        self._protocol_manager = ProtocolManager(self)
        self._user_manager = UserManager(self)
        self._repo_manager = RepoManager(self)
        self._release_manager = ReleaseManager(self)
        return

    @property
    def data(self) -> NestedDict:
        return self._data

    @property
    def git(self) -> Git:
        return self._git

    @property
    def jinja_env_vars(self) -> dict:
        return self._jinja_env_vars

    @property
    def gh_context(self) -> GitHubContext:
        return self._gh_context

    @property
    def gh_api_actions(self) -> GitHubRepoAPI:
        return self._gh_api_actions

    @property
    def gh_api_admin(self) -> GitHubRepoAPI:
        return self._gh_api_admin


    @property
    def branch(self) -> BranchManager:
        return self._branch_manager

    @property
    def changelog(self) -> ChangelogManager:
        return self._changelog_manager

    @property
    def commit(self) -> CommitManager:
        return self._commit_manager

    @property
    def cache(self) -> CacheManager:
        return self._cache_manager

    @property
    def issue(self) -> IssueManager:
        return self._issue_manager

    @property
    def label(self) -> LabelManager:
        return self._label_manager

    @property
    def protocol(self) -> ProtocolManager:
        return self._protocol_manager

    @property
    def release(self) -> ReleaseManager:
        return self._release_manager

    @property
    def repo(self) -> RepoManager:
        return self._repo_manager

    @property
    def user(self) -> UserManager:
        return self._user_manager

    def fill_jinja_template(self, template: str, env_vars: dict | None = None) -> str:
        return jinja2.Template(template).render(
            self.jinja_env_vars | {"now": datetime.datetime.now(tz=datetime.UTC)} | (env_vars or {})
        )

    def fill_jinja_templates(self, templates: dict, env_vars: dict | None = None) -> dict:

        def recursive_fill(template):
            if isinstance(template, dict):
                return {recursive_fill(key): recursive_fill(value) for key, value in template.items()}
            if isinstance(template, list):
                return [recursive_fill(value) for value in template]
            if isinstance(template, str):
                return self.fill_jinja_template(template, env_vars)
            return template

        return recursive_fill(templates)


def from_metadata_json(
    git_api: Git,
    jinja_env_vars: dict,
    github_context: GitHubContext,
    github_api_actions: GitHubRepoAPI,
    github_api_admin: GitHubRepoAPI,
    reporter: Reporter,
    commit_hash: str | None = None,
) -> Manager:
    branch_name = git_api.current_branch_name()
    address = f"from the {branch_name} branch of the {repo} repository"
    log_title = f"Metadata Load ({branch_name})"
    err_msg = f"Failed to load metadata file {address}."
    try:
        if commit_hash:
            data = controlman.from_json_file_at_commit(
                git_manager=git_api,
                commit_hash=commit_hash,
            )
        else:
            data = controlman.from_json_file(repo_path=git_api.repo_path)
        logger.success(
            log_title,
            f"Metadata loaded successfully {address}.",
        )
        logger.info(
            "Commit Config",
            repr(data["commit.config.regex"]),
            "test",
            repr("\\n")
        )
        return Manager(
            data=data,
            git_api=git_api,
            jinja_env_vars=jinja_env_vars,
            github_context=github_context,
            github_api_actions=github_api_actions,
            github_api_admin=github_api_admin,
        )
    except controlman.exception.load.ControlManInvalidMetadataError as e:
        logger.critical(
            log_title,
            err_msg,
            e.report.body["problem"].content,
        )
        reporter.add(
            name="main",
            status="fail",
            summary=f"Failed to load metadata {address}.",
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
            summary=f"Failed to load metadata {address}.",
            section="",
        )
        raise ProManException()