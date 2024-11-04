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
    from conventional_commits import ConventionalCommitMessage


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

    def create_from_message(self, message: str) -> Commit:
        try:
            msg = self._commit_msg_parser.parse(message)
        except Exception as e:
            msg = message
            _logger.warning(
                "Commit Message Processing",
                f"Failed to parse commit message: {e}",
                message,
            )
        return Commit(msg)

    def create_auto_commit_msg(self, commit_type: str, env_vars: dict | None = None) -> Commit:
        commit_data = self._data_main[f"commit.auto.{commit_type}"]
        scope = commit_data.get("scope")
        if isinstance(scope, str):
            scope = (scope,)
        commit_msg = self._commit_msg_writer(
            type=commit_data["type"],
            description=self._fill_jinja_templates(commit_data["description"], env_vars=env_vars),
            scope=scope,
            body=self._fill_jinja_templates(commit_data.get("body", ""), env_vars=env_vars),
            footer=self._fill_jinja_templates(commit_data.get("footer", {}), env_vars=env_vars),
        )
        return Commit(msg=commit_msg, type_description=commit_data.get("type_description"))

    def _fill_jinja_templates(self, templates: dict | list | str, env_vars: dict | None = None) -> dict | list | str:

        def recursive_fill(template):
            if isinstance(template, dict):
                return {recursive_fill(key): recursive_fill(value) for key, value in template.items()}
            if isinstance(template, list):
                return [recursive_fill(value) for value in template]
            if isinstance(template, str):
                return jinja2.Template(template).render(
                    self._jinja_env_vars | {"now": datetime.datetime.now(tz=datetime.UTC)} | (env_vars or {})
                )
            return template

        return recursive_fill(templates)


class Commit:
    def __init__(
        self,
        msg: str | ConventionalCommitMessage,
        action: ReleaseAction | None = None,
        type_description: str | None = None,
        sha: str | None = None,
        author: dict | None = None,
        committer: dict | None = None,
    ):
        self.msg = msg
        self.action = action
        self.type_description = type_description
        self.sha = sha
        self.author = author
        self.committer = committer
        return

    @property
    def type(self) -> str:
        return "" if isinstance(self.msg, str) else self.msg.type

    @property
    def scope(self) -> tuple[str, ...]:
        return tuple() if isinstance(self.msg, str) else self.msg.scope

    @property
    def description(self) -> str:
        return self.msg.splitlines()[0] if isinstance(self.msg, str) else self.msg.description

    @property
    def body(self) -> str:
        if isinstance(self.msg, str):
            parts = self.msg.split("\n", 1)
            return parts[1] if len(parts) > 1 else ""
        return self.msg.body

    @property
    def footer(self) -> CommitFooter:
        return CommitFooter({} if isinstance(self.msg, str) else self.msg.footer)

    @property
    def summary(self) -> str:
        return self.description if isinstance(self.msg, str) else self.msg.summary

    @property
    def footerless(self) -> str:
        return self.msg if isinstance(self.msg, str) else self.msg.footerless

    def __str__(self):
        return str(self.msg)


class CommitFooter:

    def __init__(self, data):
        self._data = data or {}
        return

    @property
    def initialize_project(self) -> bool:
        return "initialize-project" in self._data

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
