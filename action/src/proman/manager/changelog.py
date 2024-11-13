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

    def update_parent(
        self,
        sha: str,
        local_version: str,
        public_version: str
    ):
        self.current["parent"] = {
            "local_version": local_version,
            "public_version": public_version,
            "sha": sha
        }
        return

    def update_pull_request(
        self,
        pull: PullRequest,
        labels: dict[LabelType, list[Label]],
    ):

        label_list = []
        for label_type, label_entries in labels.items():
            if label_type in (LabelType.CUSTOM_SINGLE, LabelType.CUSTOM_GROUP):
                label_list.extend(label_entries)
        add = {
            "additions": pull.additions,
            "assignees": self._create_assignees_from_issue(pull),
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

    def update_pull_request_reviewers(self, pull: PullRequest):
        reviewers = [
            self._manager.user.get_from_github_rest_id(reviewer.id, add_to_contributors=True)
            for reviewer in pull.requested_reviewers
        ]
        self.current["pull_request"].update({"reviewers": self._create_user_list(reviewers)})
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
        self.update_issue(issue=issue)
        if issue.milestone:
            self.update_milestone(milestone=issue.milestone)
        self.update_protocol(protocol=protocol)
        self.current["pull_request"] = {
            "number": pull["number"],
            "id": pull["id"],
            "node_id": pull["node_id"],
            "url": pull["html_url"],
            "created_at": self._manager.normalize_github_date(pull["created_at"]),
            "creator": pull["user"].changelog_entry,
            "assignees": self._create_user_list(issue_form.pull_assignees),
            "labels": self._create_labels(labels),
            "authors": [],
            "commiters": [],
            "reviewers": self._create_user_list(issue_form.review_assignees),
            "title": pull["title"],
            "internal": True,
            "base": {
                "ref": pull["base"].name,
                "sha": pull["base"].sha,
                "version": str(base_version),
            },
            "head": {
                "ref": pull["head"].name,
                "sha": pull["head"].sha,
            },
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

    def update_issue(self, issue: Issue):

        self.current["issue"] = {
            "number": issue.number,
            "id": issue.id,
            "node_id": issue.node_id,
            "url": issue.html_url,
            "created_at": self._manager.normalize_github_date(issue.created_at),
            "assignees": self._create_assignees_from_issue(issue),
            "creator": self._manager.user.from_issue_author(issue, add_to_contributors=True).changelog_entry,
            "title": issue.title,
        }
        return

    def _create_assignees_from_issue(self, issue: Issue | PullRequest):
        assignee_gh_ids = []
        if issue.assignee:
            assignee_gh_ids.append(issue.assignee.id)
        if issue.assignees:
            for assignee in issue.assignees:
                if assignee:
                    assignee_gh_ids.append(assignee.id)
        return self._create_user_list(
            [
                self._manager.user.get_from_github_rest_id(
                    assignee_gh_id, add_to_contributors=True
                ) for assignee_gh_id in set(assignee_gh_ids)
            ]
        )

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








class ChangelogManager_old:
    def __init__(
        self,
        path_root: str,
        changelog_metadata: dict,
        ver_dist: str,
        commit_type: str,
        commit_title: str,
        parent_commit_hash: str,
        parent_commit_url: str,
    ):
        self._meta = changelog_metadata
        self._vars = {
            "ver_dist": ver_dist,
            "date": datetime.date.today().strftime("%Y.%m.%d"),
            "commit_type": commit_type,
            "commit_title": commit_title,
            "parent_commit_hash": parent_commit_hash,
            "parent_commit_url": parent_commit_url,
        }
        self._path_root = Path(path_root).resolve()
        self._name_to_id = {v["name"]: k for k, v in self._meta.items()}
        self._changes = {}
        return

    def add_change(self, changelog_id: str, section_id: str, change_title: str, change_details: str):
        if changelog_id not in self._meta:
            logger.error(f"Invalid changelog ID: {changelog_id}")
        changelog_dict = self._changes.setdefault(changelog_id, {})
        if not isinstance(changelog_dict, dict):
            logger.error(
                f"Changelog {changelog_id} is already updated with an entry; cannot add individual changes."
            )
        for section_idx, section in enumerate(
            self._meta[
                changelog_id if changelog_id != "package_public_prerelease" else "package_public"
            ]["sections"]
        ):
            if section["id"] == section_id:
                section_dict = changelog_dict.setdefault(
                    section_idx, {"title": section["title"], "changes": []}
                )
                section_dict["changes"].append({"title": change_title, "details": change_details})
                break
        else:
            logger.error(f"Invalid section ID: {section_id}")
        return

    def add_entry(self, changelog_id: str, sections: str):
        if changelog_id not in self._meta:
            logger.error(f"Invalid changelog ID: {changelog_id}")
        if changelog_id in self._changes:
            logger.error(
                f"Changelog {changelog_id} is already updated with an entry; cannot add new entry."
            )
        self._changes[changelog_id] = sections
        return

    def add_from_commit_body(self, body: str):
        heading_pattern = r"^#\s+(.*?)\n"
        sections = re.split(heading_pattern, body, flags=re.MULTILINE)
        for i in range(1, len(sections), 2):
            heading = sections[i]
            content = sections[i + 1]
            if not heading.startswith("Changelog: "):
                continue
            changelog_name = heading.removeprefix("Changelog: ").strip()
            changelog_id = self._name_to_id.get(changelog_name)
            if not changelog_id:
                logger.error(f"Invalid changelog name: {changelog_name}")
            self.add_entry(changelog_id, content)
        return

    def write_all_changelogs(self):
        for changelog_id in self._changes:
            self.write_changelog(changelog_id)
        return

    def write_changelog(self, changelog_id: str):
        if changelog_id not in self._changes:
            return
        changelog = self.get_changelog(changelog_id)
        with open(self._path_root / self._meta[changelog_id]["path"], "w") as f:
            f.write(changelog)
        return

    def get_changelog(self, changelog_id: str) -> str:
        if changelog_id not in self._changes:
            return ""
        path = self._path_root / self._meta[changelog_id]["path"]
        if not path.exists():
            title = f"# {self._meta[changelog_id]['title']}"
            intro = self._meta[changelog_id]["intro"].strip()
            text_before = f"{title}\n\n{intro}"
            text_after = ""
        else:
            with open(path) as f:
                text = f.read()
            parts = re.split(r"^## ", text, maxsplit=1, flags=re.MULTILINE)
            if len(parts) == 2:
                text_before, text_after = parts[0].strip(), f"## {parts[1].strip()}"
            else:
                text_before, text_after = text.strip(), ""
        entry, _ = self.get_entry(changelog_id)
        changelog = f"{text_before}\n\n{entry.strip()}\n\n{text_after}".strip() + "\n"
        return changelog

    def get_all_entries(self) -> list[tuple[str, str]]:
        return [self.get_entry(changelog_id) for changelog_id in self.open_changelogs]

    def get_entry(self, changelog_id: str) -> tuple[str, str]:
        if changelog_id not in self._changes:
            return "", ""
        entry_sections, needs_intro = self.get_sections(changelog_id)
        if needs_intro:
            entry_title = self._meta[changelog_id]["entry"]["title"].format(**self._vars).strip()
            entry_intro = self._meta[changelog_id]["entry"]["intro"].format(**self._vars).strip()
            entry = f"## {entry_title}\n\n{entry_intro}\n\n{entry_sections}"
        else:
            entry = entry_sections
        changelog_name = self._meta[changelog_id]["name"]
        return entry, changelog_name

    def get_sections(self, changelog_id: str) -> tuple[str, bool]:
        if changelog_id not in self._changes:
            return "", False
        if isinstance(self._changes[changelog_id], str):
            return self._changes[changelog_id], False
        changelog_dict = self._changes[changelog_id]
        sorted_sections = [value for key, value in sorted(changelog_dict.items())]
        sections_str = ""
        for section in sorted_sections:
            sections_str += f"### {section['title']}\n\n"
            for change in section["changes"]:
                sections_str += f"#### {change['title']}\n\n{change['details']}\n\n"
        return sections_str.strip() + "\n", True

    @property
    def open_changelogs(self) -> tuple[str, ...]:
        return tuple(self._changes.keys())
