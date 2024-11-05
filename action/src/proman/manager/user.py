from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
from functools import partial
import re

import pyserials as ps
from controlman import data_helper, data_validator

from proman.dstruct import User

if _TYPE_CHECKING:
    from typing import Literal
    from pathlib import Path
    from controlman.cache_manager import CacheManager
    from proman.manager.data import DataManager
    from pylinks.api.github import GitHub
    from github_contexts.github.payload.object.issue import Issue


class UserManager:

    def __init__(
        self,
        data_main: DataManager,
        root_path: Path,
        github_api: GitHub,
        cache_manager: CacheManager,
    ):
        self._github_api = github_api
        self._cache_manager = cache_manager
        self._data_main = data_main
        self._contributors_path_main = root_path / self._data_main["doc.contributors.path"]
        self._contributors_main = ps.read.json_from_file(
            self._contributors_path_main
        ) if self._contributors_path_main.is_file() else {}
        return

    def citation_authors(self, data: DataManager | None = None) -> list[User]:
        data = data or self._data_main
        out = []
        for member_id in data["citation"]["authors"]:
            user = User(
                id=member_id,
                association="member",
                data=data["team"][member_id],
            )
            out.append(user)
        return out

    def citation_contributors(self, data: DataManager | None = None) -> list[User]:
        data = data or self._data_main
        out = []
        for member_id in data["citation"]["contributors"]:
            user = User(
                id=member_id,
                association="member",
                data=data["team"][member_id],
            )
            out.append(user)
        return out

    def from_issue_form_id(
        self,
        issue_form_id: str,
        assignment: Literal["issue", "pull", "review"]
    ) -> list[User]:
        out = []
        for member_id, member in self._data_main["team"].items():
            for member_role_id in member.get("role", {}).keys():
                role = self._data_main["role"][member_role_id]
                issue_id_regex = role.get("assignment", {}).get(assignment)
                if issue_id_regex and re.match(issue_id_regex, issue_form_id):
                    user = User(
                        id=member_id,
                        association="member",
                        data=member,
                    )
                    out.append(user)
                    break
        return out

    def get_from_github_rest_id(
        self,
        github_id: int,
        add_to_contributors: bool = True,
        update_file: bool = False,
    ) -> User:
        for member_id, member_data in self._data_main["team"].items():
            if member_data.get("github", {}).get("rest_id") == github_id:
                return User(id=member_id, association="member", data=member_data)
        for github_user_id, github_user in self._contributors_main.items():
            if github_user_id == github_id:
                return User(id=github_id, association="user", data=github_user)
        data = data_helper.fill_entity(
            entity={"github": {"rest_id": github_id}},
            github_api=self._github_api,
            cache_manager=self._cache_manager,
            validator=partial(data_validator.validate, schema="entity"),
        )[0]
        if add_to_contributors:
            existing_data = self._contributors_main.setdefault("github", {}).setdefault(github_id, {})
            ps.update.dict_from_addon(data, existing_data)
            if update_file:
                self.write_contributors()
        return User(id=github_id, association="user", data=data)

    def from_issue_author(self, issue: Issue) -> User:
        user = self.get_from_github_rest_id(issue.user.id)
        return User(
            id=user.id,
            association=user.association,
            data=user.as_dict,
            github_association=issue.author_association,
        )

    def write_contributors(self):
        with open(self._contributors_path_main, "w") as contributors_file:
            contributors_file.write(ps.write.to_json_string(self._contributors_main, sort_keys=True, indent=4))
        return


