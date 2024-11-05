from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
from functools import partial
import datetime

import jinja2

import conventional_commits
from proman.data_manager import DataManager
from versionman.pep440_semver import PEP440SemVer as _PEP440SemVer
from loggerman import logger as _logger

from proman.datatype import ReleaseAction
from proman.exception import ProManException


if _TYPE_CHECKING:
    from typing import Sequence, Callable, Literal
    from conventional_commits import ConventionalCommitMessage
    from proman.user_manager import User


class CommitManager:

    def __init__(self, data_main: DataManager, jinja_env_vars: dict | None = None):
        self._data_main = data_main
        self._jinja_env_vars = jinja_env_vars or {}
        self._commit_msg_parser = conventional_commits.create_parser(
            type_regex=self._data_main["commit.config.regex.validator.type"],
            scope_regex=self._data_main["commit.config.regex.validator.scope"],
            description_regex=self._data_main["commit.config.regex.validator.description"],
            scope_start_separator_regex=self._data_main["commit.config.regex.separator.scope_start"],
            scope_end_separator_regex=self._data_main["commit.config.regex.separator.scope_end"],
            scope_items_separator_regex=self._data_main["commit.config.regex.separator.scope_items"],
            description_separator_regex=self._data_main["commit.config.regex.separator.description"],
            body_separator_regex=self._data_main["commit.config.regex.separator.body"],
            footer_separator_regex=self._data_main["commit.config.regex.separator.footer"],
        )
        self._commit_msg_writer = partial(
            conventional_commits.create,
            scope_start=self._data_main["commit.config.scope_start"],
            scope_separator=self._data_main["commit.config.scope_separator"],
            scope_end=self._data_main["commit.config.scope_end"],
            description_separator=self._data_main["commit.config.description_separator"],
            body_separator=self._data_main["commit.config.body_separator"],
            footer_separator=self._data_main["commit.config.footer_separator"],
            type_regex=self._data_main["commit.config.regex.validator.type"],
            scope_regex=self._data_main["commit.config.regex.validator.scope"],
            description_regex=self._data_main["commit.config.regex.validator.description"],
        )
        return

    def create_from_msg(self, message: str) -> Commit:
        try:
            msg = self._commit_msg_parser.parse(message)
            return Commit(
                writer=self._commit_msg_writer,
                type=msg.type,
                scope=msg.scope,
                description=msg.description,
                body=msg.body,
                footer=msg.footer,
            )
        except Exception as e:
            _logger.warning(
                "Commit Message Processing",
                f"Failed to parse commit message: {e}",
                message,
            )
            parts = message.split("\n", 1)
            description = parts[0]
            body = parts[1] if len(parts) > 1 else ""
            return Commit(
                writer=self._commit_msg_writer,
                description=description,
                body=body,
            )

    def create_auto(self, id: str, env_vars: dict | None = None) -> Commit:
        commit_data = self._data_main[f"commit.auto.{id}"]
        scope = commit_data.get("scope")
        if isinstance(scope, str):
            scope = (scope,)
        return Commit(
            writer=self._commit_msg_writer,
            type=commit_data["type"],
            description=commit_data["description"],
            scope=scope,
            body=commit_data.get("body"),
            footer=commit_data.get("footer"),
            type_description=commit_data.get("type_description"),
            jinja_env_vars=self._jinja_env_vars | (env_vars or {}),
        )

    def create_release(self, id: str, env_vars: dict | None = None) -> Commit:
        commit_data = self._data_main[f"commit.release"][id]
        return Commit(
            writer=self._commit_msg_writer,
            type=commit_data["type"],
            description=commit_data["description"],
            scope=commit_data.get("scope"),
            body=commit_data.get("body"),
            footer=commit_data.get("footer"),
            action=ReleaseAction(commit_data["action"]) if commit_data.get("action") else None,
            jinja_env_vars=self._jinja_env_vars | (env_vars or {}),
        )

    # def _get_commits(self, base: bool = False) -> list[Commit]:
    #     git = self._git_base if base else self._git_head
    #     commits = git.get_commits(f"{self._context.hash_before}..{self._context.hash_after}")
    #     logger.info("Read commits from git history", json.dumps(commits, indent=4))
    #
    #     parsed_commits = []
    #     for commit in commits:
    #         conv_msg = parser.parse(message=commit["msg"])
    #         if not conv_msg:
    #             parsed_commits.append(
    #                 Commit(
    #                     **commit, group_data=NonConventionalCommit()
    #                 )
    #             )
    #         else:
    #             group = self._data_main.get_commit_type_from_conventional_type(conv_type=conv_msg.type)
    #             commit["msg"] = conv_msg
    #             parsed_commits.append(Commit(**commit, group_data=group))
    #     return parsed_commits


class Commit:

    def __init__(
        self,
        writer: Callable[..., ConventionalCommitMessage],
        type: str | None = None,
        scope: Sequence[str] | None = None,
        description: str | None = None,
        body: str | None = None,
        footer: dict | None = None,
        action: ReleaseAction | None = None,
        type_description: str | None = None,
        sha: str | None = None,
        author: User | None = None,
        committer: User | None = None,
        jinja_env_vars: dict | None = None,
    ):
        self._writer = writer
        self.type = type
        self.scope = scope or []
        self.description = description or ""
        self.body = body or ""
        self.footer = CommitFooter(footer or {})
        self.action = action
        self.type_description = type_description
        self.sha = sha
        self.author = author
        self.committer = committer
        self.jinja_env_vars = jinja_env_vars or {}
        return

    @property
    def conv_msg(self) -> ConventionalCommitMessage:
        return self._writer(
            type=self.type,
            scope=self.scope,
            description=self._fill_jinja_templates(self.description),
            body=self._fill_jinja_templates(self.body),
            footer=self._fill_jinja_templates(self.footer.as_dict),
        )

    def _fill_jinja_templates(self, templates: dict | list | str, env_vars: dict | None = None) -> dict | list | str:

        def recursive_fill(template):
            if isinstance(template, dict):
                return {recursive_fill(key): recursive_fill(value) for key, value in template.items()}
            if isinstance(template, list):
                return [recursive_fill(value) for value in template]
            if isinstance(template, str):
                return jinja2.Template(template).render(
                    self.jinja_env_vars | {"now": datetime.datetime.now(tz=datetime.UTC)} | (env_vars or {})
                )
            return template

        return recursive_fill(templates)


class CommitFooter:

    def __init__(self, data):
        self._data = data or {}
        return

    @property
    def initialize_project(self) -> bool:
        return "initialize-project" in self._data

    @property
    def squash(self) -> bool | None:
        return self._data.get("squash")

    @property
    def publish(self) -> bool | None:
        return self._data.get("publish")

    @property
    def version(self) -> _PEP440SemVer | None:
        version = self._data.get("version")
        if version:
            try:
                return _PEP440SemVer(version)
            except Exception as e:
                _logger.critical(f"Invalid version string '{version}' in commit footer: {e}")
                raise ProManException()
        return

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        return

    def get(self, key, default=None):
        return self._data.get(key, default)

    def pop(self, key, default=None):
        return self._data.pop(key, default)

    def setdefault(self, key, default):
        return self._data.setdefault(key, default)

    @property
    def as_dict(self):
        return self._data
