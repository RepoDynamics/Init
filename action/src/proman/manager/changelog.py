from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from pathlib import Path
import re
import datetime

from loggerman import logger
import pyserials as ps
import mdit

from proman.dtype import LabelType

if _TYPE_CHECKING:
    from typing import Literal
    from github_contexts.github.payload.object import Issue, PullRequest, Milestone
    from proman.manager import Manager, ProtocolManager
    from proman.dstruct import IssueForm, User, Tasklist, Version, Label


class ChangelogManager:

    def __init__(self, manager: Manager):
        self._manager = manager
        self._path = self._manager.git.repo_path / self._manager.data["doc.changelog.path"]
        self._changelog = ps.read.json_from_file(self._path) if self._path.is_file() else {}
        return

    @property
    def full(self) -> dict:
        return self._changelog

    @property
    def current(self) -> dict:
        return self._changelog.setdefault("current", {})

    def update_current(self, data: dict):
        ps.update.dict_from_addon(
            data=data,
            addon=self.current,
        )
        self._changelog["current"] = data
        logger.info(
            "Changelog Update",
            mdit.element.code_block(
                ps.write.to_yaml_string(self._changelog["current"]),
                language="yaml"
            )
        )
        return

    def write(self, path: Path | None = None):
        out = ps.write.to_json_string(self._changelog, sort_keys=True, indent=4)
        with open(path or self._path, "w") as changelog_file:
            changelog_file.write(out)
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
            "assignees": self._update_contributors_with_assignees(pull),
            "changed_files": pull.changed_files,
            "commits": pull.commits,
            "deletions": pull.deletions,
            "title": pull.title,
            "labels": self._create_labels(label_list),
        }
        self.current["pull_request"].update(add)
        if pull.milestone:
            self.update_milestone(pull.milestone)
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
                self.update_contributor(
                    association=user.association,
                    id=user.id,
                    roles=roles,
                )
        return

    def add_pull_contributor(self, contributor: User, role: Literal["authors", "committers"]):
        contributors = self.current["pull_request"][role]
        contributor_entry = contributor.changelog_entry
        if contributor_entry not in contributors:
            contributors.append(contributor_entry)
        return

    def create_current_from_issue(
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
        self.update_parent(pull["base"].sha, base_version)
        self._update_contributors_with_assignees(issue=issue, issue_form=issue_form)
        for assignee in issue_form.pull_assignees + issue_form.review_assignees:
            self.update_contributor(
                association=assignee.association,
                id=assignee.id,
                roles=assignee.current_role,
            )
        if issue.milestone:
            self.update_milestone(milestone=issue.milestone)
        if issue_form.role["submitter"]:
            submitter = self._manager.user.from_issue_author(issue, add_to_contributors=True)
            self.update_contributor(
                association=submitter.association, id=submitter.id, roles=issue_form.role["submitter"]
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

    def update_parent(
        self,
        sha: str,
        version: Version
    ):
        self.current["parent"] = {
            "sha": sha,
            "version": version.public,
            "distance": version.local[0] if version.is_local else 0,
        }
        return

    def update_protocol(self, protocol: ProtocolManager):
        self.update_protocol_tasklist(protocol.get_tasklist())
        self.update_protocol_data(protocol.get_all_data())
        return

    def update_protocol_data(self, data: dict):
        self.current.setdefault("protocol", {})["data"] = data
        return

    def update_protocol_tasklist(self, tasklist: Tasklist):
        self.current.setdefault("protocol", {})["tasks"] = tasklist.as_list
        return

    def update_type_id(self, type_id: str):
        self.current["type_id"] = type_id
        return

    def update_milestone(self, milestone: Milestone):
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
                    association=user.association,
                    id=user.id,
                    roles=roles,
                )
        return

    def update_contributor(
        self,
        association: Literal["member", "user", "external"],
        id: str,
        roles: dict[str, int],
    ):
        contributor_roles = self.current.setdefault(
            "contributor", {}
        ).setdefault(association, {}).setdefault(id, {}).setdefault("role", {})
        for role_id, role_priority in roles.items():
            if role_id not in contributor_roles:
                contributor_roles[role_id] = role_priority
        return

    @staticmethod
    def _create_user_list(users: list[User]) -> list[dict]:
        return sorted(
            [user.changelog_entry for user in users],
            key=lambda changelog_entry: changelog_entry["id"]
        )

    @staticmethod
    def _create_labels(labels: list[Label]):
        out = []
        for label in labels:
            if label.category is LabelType.CUSTOM_GROUP:
                out.append({"group": label.group_id, "id": label.id})
            elif label.category is LabelType.CUSTOM_SINGLE:
                out.append({"group": "single", "id": label.id})
        return out
