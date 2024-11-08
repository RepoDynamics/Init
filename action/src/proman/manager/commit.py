from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
from functools import partial

import conventional_commits
from loggerman import logger as _logger

from proman.dtype import ReleaseAction
from proman.dstruct import Commit

if _TYPE_CHECKING:
    from proman.manager import Manager


class CommitManager:

    def __init__(self, manager: Manager):
        self._manager = manager
        self._msg_parser = conventional_commits.create_parser(
            type_regex=self._manager.data["commit.config.regex.validator.type"],
            scope_regex=self._manager.data["commit.config.regex.validator.scope"],
            description_regex=self._manager.data["commit.config.regex.validator.description"],
            scope_start_separator_regex=self._manager.data["commit.config.regex.separator.scope_start"],
            scope_end_separator_regex=self._manager.data["commit.config.regex.separator.scope_end"],
            scope_items_separator_regex=self._manager.data["commit.config.regex.separator.scope_items"],
            description_separator_regex=self._manager.data["commit.config.regex.separator.description"],
            body_separator_regex=self._manager.data["commit.config.regex.separator.body"],
            footer_separator_regex=self._manager.data["commit.config.regex.separator.footer"],
        )
        self._msg_writer = partial(
            conventional_commits.create,
            scope_start=self._manager.data["commit.config.scope_start"],
            scope_separator=self._manager.data["commit.config.scope_separator"],
            scope_end=self._manager.data["commit.config.scope_end"],
            description_separator=self._manager.data["commit.config.description_separator"],
            body_separator=self._manager.data["commit.config.body_separator"],
            footer_separator=self._manager.data["commit.config.footer_separator"],
            type_regex=self._manager.data["commit.config.regex.validator.type"],
            scope_regex=self._manager.data["commit.config.regex.validator.scope"],
            description_regex=self._manager.data["commit.config.regex.validator.description"],
        )
        return

    def create_from_msg(self, message: str) -> Commit:
        try:
            msg = self._msg_parser.parse(message)
            return Commit(
                writer=self._msg_writer,
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
                writer=self._msg_writer,
                description=description,
                body=body,
            )

    def create_auto(self, id: str, env_vars: dict | None = None) -> Commit:
        commit_data = self._manager.data[f"commit.auto.{id}"]
        scope = commit_data.get("scope")
        if isinstance(scope, str):
            scope = (scope,)
        return Commit(
            writer=self._msg_writer,
            type=commit_data["type"],
            description=commit_data["description"],
            scope=scope,
            body=commit_data.get("body"),
            footer=commit_data.get("footer"),
            type_description=commit_data.get("type_description"),
            jinja_env_vars=self._manager.jinja_env_vars | (env_vars or {}),
        )

    def create_release(self, id: str, env_vars: dict | None = None) -> Commit:
        commit_data = self._manager.data[f"commit.release"][id]
        return Commit(
            writer=self._msg_writer,
            type=commit_data["type"],
            description=commit_data["description"],
            scope=commit_data.get("scope"),
            body=commit_data.get("body"),
            footer=commit_data.get("footer"),
            action=ReleaseAction(commit_data["action"]) if commit_data.get("action") else None,
            jinja_env_vars=self._manager.jinja_env_vars | (env_vars or {}),
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


