from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
from functools import partial
import re

import pyserials as ps
from controlman import data_helper, data_validator
import pylinks

from proman.dstruct import User

if _TYPE_CHECKING:
    from typing import Literal
    from github_contexts.github.payload.object.issue import Issue
    from github_contexts.github.payload.object.pull_request import PullRequest
    from proman.manager import Manager


class UserManager:

    def __init__(self, manager: Manager):
        self._manager = manager
        self._gh_api = pylinks.api.github(token=self._manager.gh_context.token)
        self._contributors_path = self._manager.git.repo_path / self._manager.data["doc.contributors.path"]
        self._contributors = ps.read.json_from_file(
            self._contributors_path
        ) if self._contributors_path.is_file() else {}
        return

    def from_member_id(self, member_id: str) -> User:
        member = self._manager.data["team"].get(member_id)
        if not member:
            raise ValueError(f"No member data found in the data branch or main data for member ID {member_id}.")
        return User(
            id=member_id,
            association="member",
            data=member,
        )

    def from_issue_form_id(
        self,
        issue_form_id: str,
        assignment: Literal["issue", "pull", "review"]
    ) -> list[User]:
        out = []
        for member_id, member in self._manager.data["team"].items():
            for member_role_id in member.get("role", {}).keys():
                role = self._manager.data["role"][member_role_id]
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
        add_to_contributors: bool = False,
        update_file: bool = False,
    ) -> User:
        for member_id, member_data in self._manager.data["team"].items():
            if member_data.get("github", {}).get("rest_id") == github_id:
                return User(id=member_id, association="member", data=member_data)
        for github_user_id, github_user in self._contributors["github"].items():
            if github_user_id == github_id:
                return User(id=github_id, association="user", data=github_user)
        data = data_helper.fill_entity(
            entity={"github": {"rest_id": github_id}},
            github_api=self._gh_api,
            cache_manager=self._manager.cache,
            )[0]
        user = User(id=github_id, association="user", data=data)
        if add_to_contributors:
            self.add_contributor(user=user, write=update_file)
        return user

    def from_github_username(
        self,
        username: str,
        add_to_contributors: bool = False,
        update_file: bool = False,
    ):
        for member_id, member_data in self._manager.data["team"].items():
            if member_data.get("github", {}).get("id") == username:
                return User(id=member_id, association="member", data=member_data)
        for github_user in self._contributors["github"].values():
            if github_user["github"]["id"] == username:
                return User(id=github_user["github"]["id"], association="user", data=github_user)
        data = data_helper.fill_entity(
            entity={"github": {"id": username}},
            github_api=self._gh_api,
            cache_manager=self._manager.cache,
            )[0]
        user = User(id=data["github"]["id"], association="user", data=data)
        if add_to_contributors:
            self.add_contributor(user=user, write=update_file)
        return user

    def from_name_and_email(
        self,
        name: str,
        email: str,
        add_to_contributors: bool = False,
        update_file: bool = False,
    ):
        for member_id, member_data in self._manager.data["team"].items():
            if member_data["name"]["full"] == name and member_data.get("email", {}).get("id") == email:
                return User(id=member_id, association="member", data=member_data)
        for github_user in self._contributors["github"].values():
            if github_user["name"]["full"] == name and github_user.get("email", {}).get("id") == email:
                return User(id=github_user["github"]["id"], association="user", data=github_user)
        user = User(
            id=f"{name}_{email}",
            association="external",
            data={"name": {"full": name}, "email": {"id": email, "url": f"mailto:{email}"}}
        )
        if add_to_contributors:
            self.add_contributor(user=user, write=update_file)
        return user

    def from_issue_author(
        self,
        issue: Issue | PullRequest | dict,
        add_to_contributors: bool = False,
        update_file: bool = False,
    ) -> User:
        user = self.get_from_github_rest_id(
            issue["user"]["id"],
            add_to_contributors=add_to_contributors,
            update_file=update_file,
        )
        return User(
            id=user.id,
            association=user.association,
            data=user.as_dict,
            github_association=issue.get("author_association"),
        )

    def add_contributor(self, user: User, write: bool = False):
        github_id = user.get("github", {}).get("id")
        entity = user.as_dict
        if github_id:
            existing_data = self._contributors.setdefault("github", {}).setdefault(github_id, {})
            ps.update.dict_from_addon(existing_data, entity)
        else:
            user_id = f"{user.name.full}_{user.email.id}"
            existing_data = self._contributors.setdefault("external", {}).setdefault(user_id, {})
            ps.update.dict_from_addon(existing_data, entity)
        if write:
            self.write_contributors()
        return

    def write_contributors(self):
        with open(self._contributors_path, "w") as contributors_file:
            contributors_file.write(ps.write.to_json_string(self._contributors, sort_keys=True, indent=4))
        return


