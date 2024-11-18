from __future__ import annotations as _annotations

import copy
import datetime
from typing import TYPE_CHECKING as _TYPE_CHECKING

import pyserials as ps
import controlman

from github_contexts.github.payload.object import Issue

from proman.dtype import LabelType

if _TYPE_CHECKING:
    from typing import Literal
    from pathlib import Path
    from github_contexts.github.payload.object import PullRequest, Milestone
    from proman.manager import Manager, ProtocolManager
    from proman.dstruct import IssueForm, Tasklist, Version, Label


class BareChangelogManager:

    def __init__(self, repo_path: Path):
        self._path = repo_path / controlman.const.FILEPATH_CHANGELOG
        self._changelog = ps.read.json_from_file(self._path) if self._path.is_file() else []
        if not self._changelog or not self._changelog[0].get("ongoing"):
            self._current = {"ongoing": True}
            self._changelog.insert(0, self._current)
        else:
            self._current = self._changelog[0]
        self._read_current = copy.deepcopy(self._current)
        return

    @property
    def current(self) -> dict:
        return self._current

    @property
    def full(self) -> list:
        return self._changelog

    def get_release(self, platform: Literal["zenodo", "zenodo_sandbox", "github"]) -> dict | None:
        return self.current.get("release", {}).get(platform)

    def update_date(self):
        self.current["date"] = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
        return

    def update_parent(self, sha: str, version: Version):
        self.current["parent"] = {
            "sha": sha,
            "version": str(version.public),
            "distance": version.local[0] if version.is_local else 0,
        }
        return

    def update_protocol(self, protocol: ProtocolManager):
        self.update_protocol_data(protocol.get_all_data())
        self.update_protocol_tasklist(protocol.get_tasklist())
        return

    def update_protocol_data(self, data: dict):
        self.current.setdefault("protocol", {})["data"] = data
        return

    def update_protocol_tasklist(self, tasklist: Tasklist):
        self.current.setdefault("protocol", {})["tasks"] = tasklist.as_list
        return

    def update_release_github(self, id: int, node_id: str):
        release = self.current.setdefault("release", {})
        release["github"] = {"id": id, "node_id": node_id}
        return

    def update_release_zenodo(self, id: str, doi: str, draft: bool, sandbox: bool):
        release = self.current.setdefault("release", {})
        release["zenodo_sandbox" if sandbox else "zenodo"] = {"id": id, "doi": doi, "draft": draft}
        return

    def update_type_id(self, type_id: str):
        self.current["type_id"] = type_id
        return

    def update_version(self, version: str):
        self.current["version"] = version

    def finalize(self):
        self.current.pop("ongoing")
        return self.current

    def write_file(self):
        if self._current == self._read_current:
            return False
        self._path.write_text(
            ps.write.to_json_string(self._changelog, sort_keys=True, indent=4).strip() + "\n",
            newline="\n"
        )
        return


class ChangelogManager(BareChangelogManager):

    def __init__(self, manager: Manager):
        self._manager = manager
        super().__init__(repo_path=self._manager.git.repo_path)
        return

    def initialize_from_issue(
        self,
        issue_form: IssueForm,
        issue: Issue,
        labels: list[Label],
        pull: dict,
        protocol: ProtocolManager,
        base_version: Version,
    ):
        self.update_type_id(issue_form.id)
        self.update_protocol(protocol=protocol)
        self.update_parent(sha=pull["base"].sha, version=base_version)
        self._update_contributors_with_assignees(issue=issue, issue_form=issue_form)
        for assignee in issue_form.pull_assignees + issue_form.review_assignees:
            self.update_contributor(
                id=assignee.id,
                member=assignee.member,
                roles=assignee.current_role,
            )
        if issue.milestone:
            self._update_milestone(milestone=issue.milestone)
        if issue_form.role["submitter"]:
            submitter = self._manager.user.from_issue_author(issue, add_to_contributors=True)
            self.update_contributor(
                member=submitter.member, id=submitter.id, roles=issue_form.role["submitter"]
            )
        self.current["issue"] = {
            "number": issue.number,
            "id": issue.id,
            "node_id": issue.node_id,
            "url": issue.html_url,
            "created_at": self._manager.normalize_github_date(issue.created_at),
            "title": issue.title,
        }
        self.current["pull_request"] = {
            "number": pull["number"],
            "id": pull["id"],
            "node_id": pull["node_id"],
            "url": pull["html_url"],
            "created_at": self._manager.normalize_github_date(pull["created_at"]),
            "title": pull["title"],
            "labels": self._create_labels(labels),
            "base": {
                "ref": pull["base"].name,
                "sha": pull["base"].sha,
                "version": str(base_version.public),
                "distance": base_version.local[0] if base_version.is_local else 0
            },
            "head": {
                "ref": pull["head"].name,
                "sha": pull["head"].sha,
                "version": str(base_version.public),
                "distance": base_version.local[0] if base_version.is_local else 0
            },
        }
        return

    def update_pull_request(
        self,
        issue_form: IssueForm,
        pull: PullRequest,
        labels: dict[LabelType, list[Label]],
        protocol: ProtocolManager,
        tasklist: Tasklist,
        base_sha: str,
        base_version: Version,
    ):
        self._update_contributors_with_assignees(issue=pull, issue_form=issue_form)
        self.update_parent(sha=base_sha, version=base_version)
        self.update_protocol_data(protocol.get_all_data())
        self.update_protocol_tasklist(tasklist)
        label_list = []
        for label_type, label_entries in labels.items():
            if label_type in (LabelType.CUSTOM_SINGLE, LabelType.CUSTOM_GROUP):
                label_list.extend(label_entries)
        add = {
            "additions": pull.additions,
            "assignees": self._update_contributors_with_assignees(issue=pull, issue_form=issue_form),
            "changed_files": pull.changed_files,
            "commits": pull.commits,
            "deletions": pull.deletions,
            "title": pull.title,
            "labels": self._create_labels(label_list),
        }
        self.current["pull_request"].update(add)
        if pull.milestone:
            self._update_milestone(pull.milestone)
        return

    def update_pull_request_reviewers(self, pull: PullRequest, issue_form: IssueForm):
        for reviewer in pull.requested_reviewers:
            user = self._manager.user.get_from_github_rest_id(
                reviewer.id, add_to_contributors=True
            )
            for predefined_reviewer in issue_form.review_assignees:
                if predefined_reviewer == user:
                    roles = predefined_reviewer.current_role
                    break
            else:
                roles = issue_form.role["review_assignee"]
            if roles:
                self.update_contributor(id=user.id, member=user.member, roles=roles)
        return

    def update_contributor(self, id: str, member: bool, roles: dict[str, int]):
        category = "member" if member else "collaborator"
        contributor_roles = self.current.setdefault(
            "contributor", {}
        ).setdefault(category, {}).setdefault(id, {}).setdefault("role", {})
        for role_id, role_priority in roles.items():
            if role_id not in contributor_roles:
                contributor_roles[role_id] = role_priority
        return

    def _update_contributors_with_assignees(self, issue: Issue | PullRequest, issue_form: IssueForm):
        assignee_gh_ids = []
        if issue.assignee:
            assignee_gh_ids.append(issue.assignee.id)
        if issue.assignees:
            for assignee in issue.assignees:
                if assignee:
                    assignee_gh_ids.append(assignee.id)
        for assignee_gh_id in set(assignee_gh_ids):
            user = self._manager.user.get_from_github_rest_id(
                assignee_gh_id, add_to_contributors=True
            )
            predefined_assignees = issue_form.issue_assignees if isinstance(issue, Issue) else issue_form.pull_assignees
            for predefined_assignee in predefined_assignees:
                if predefined_assignee == user:
                    roles = predefined_assignee.current_role
                    break
            else:
                roles = issue_form.role[
                    "issue_assignee" if isinstance(issue, Issue) else "pull_assignee"
                ]
            if roles:
                self.update_contributor(
                    id=user.id,
                    member=user.member,
                    roles=roles,
                )
        return

    def _update_milestone(self, milestone: Milestone):
        self.current["milestone"] = {
            "number": milestone.number,
            "id": milestone.id,
            "node_id": milestone.node_id,
            "url": milestone.html_url,
            "title": milestone.title,
            "description": milestone.description,
            "due_on": self._manager.normalize_github_date(milestone.due_on),
            "created_at": self._manager.normalize_github_date(milestone.created_at),
        }
        return

    @staticmethod
    def _create_labels(labels: list[Label]):
        out = []
        for label in labels:
            if label.category is LabelType.CUSTOM_GROUP:
                out.append({"group": label.group_id, "id": label.id})
            elif label.category is LabelType.CUSTOM_SINGLE:
                out.append({"group": "single", "id": label.id})
        return out
