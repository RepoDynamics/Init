from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING


import pyserials as _ps
from loggerman import logger

from proman.datatype import (
    IssueForm,
    Label,
    LabelType,
    ReleaseAction,
)


if _TYPE_CHECKING:
    from proman.commit_manager import CommitManager
    from proman.user_manager import UserManager
    from proman.datatype import IssueStatus
    from versionman.pep440_semver import PEP440SemVer



class DataManager(_ps.NestedDict):

    def __init__(self, data: dict | _ps.NestedDict):
        if isinstance(data, _ps.NestedDict):
            data = data()
        super().__init__(data)
        self._commit_data: dict = {}
        self._issue_data: dict = {}
        self._label_name_to_obj: dict[str, Label] = {}
        self._label_id_to_obj: dict[tuple[str, str], Label] = {}
        self.commit_manager: CommitManager | None = None
        self.user_manager: UserManager | None = None
        return

    @property
    def label_name_to_obj_map(self) -> dict[str, Label]:
        """All repository labels, as a dictionary mapping full label names to Label objects."""
        if not self._label_name_to_obj:
            self._label_name_to_obj, self._label_id_to_obj = self._initialize_label_data()
        return self._label_name_to_obj

    @property
    def label_id_to_obj_map(self) -> dict[tuple[str, str], Label]:
        """All repository labels, as a dictionary mapping full label IDs to Label objects."""
        if not self._label_id_to_obj:
            self._label_name_to_obj, self._label_id_to_obj = self._initialize_label_data()
        return self._label_id_to_obj

    @property
    def release_action_ids(self) -> list[str]:
        return [enum.value for enum in ReleaseAction]

    def issue_form_from_id(self, form_id: str) -> IssueForm:
        for issue_form in self["issue.forms"]:
            if issue_form["id"] == form_id:
                return self._make_issue_form(issue_form)
        raise ValueError(f"Could not find issue form with ID '{form_id}'.")

    def label_from_id(self, group_id: str, label_id: str) -> Label:
        return self.label_id_to_obj_map[(group_id, label_id)]

    def label_status(self, status: str | IssueStatus) -> Label:
        if not isinstance(status, str):
            status = status.value
        return self.label_from_id("status", status)

    def label_version(self, version: str) -> Label:
        return self.label_from_id("version", version)

    def label_branch(self, branch: str) -> Label:
        return self.label_from_id("branch", branch)

    def label_version_to_branch(self, version_label: Label) -> Label:
        branch_name = self.branch_name_from_version(version=version_label.suffix)
        return self.label_branch(branch=branch_name)

    def resolve_labels(self, names: list[str]) -> dict[LabelType, list[Label]]:
        """
        Resolve a list of label names to label objects.

        Parameters
        ----------
        names : list[str]
            List of label names.
        """
        labels = {}
        for name in names:
            label = self.resolve_label(name)
            labels.setdefault(label.category, []).append(label)
        return labels

    def resolve_label(self, name: str) -> Label:
        """
        Resolve a label name to a label object.

        Parameters
        ----------
        name : str
            Name of the label.
        """
        label = self.label_name_to_obj_map.get(name)
        if label:
            return label
        logger.warning(
            "Label Resolution",
            f"Could not find label '{name}' in label data.",
        )
        return Label(category=LabelType.UNKNOWN, name=name)

    def issue_form_from_id_labels(self, label_names: list[str]) -> IssueForm:
        for issue_form_data in self["issue.forms"]:
            issue_form = self._make_issue_form(issue_form_data)
            if all(label.name in label_names for label in issue_form.id_labels):
                return issue_form
        raise ValueError(f"Could not find issue form from labels {label_names}.")

    def branch_name_from_version(self, version: str) -> str:
        return self["project.version"][version]["branch"]

    def branch_name_release(self, major_version: int) -> str:
        """Generate the name of the release branch for a given major version."""
        release_branch_prefix = self["branch.release.name"]
        return f"{release_branch_prefix}{major_version}"

    def branch_name_pre(self, version: PEP440SemVer) -> str:
        """Generate the name of the pre-release branch for a given version."""
        pre_release_branch_prefix = self["branch.pre.name"]
        return f"{pre_release_branch_prefix}{version}"

    def branch_name_dev(self, issue_nr: int, base_branch_name: str) -> str:
        """Generate the name of the development branch for a given issue number and base branch."""
        dev_branch_prefix = self["branch.dev.name"]
        return f"{dev_branch_prefix}{issue_nr}/{base_branch_name}"


    def _initialize_label_data(self) -> tuple[dict[str, Label], dict[tuple[str, str], Label]]:
        name_to_obj = {}
        id_to_obj = {}
        for group_id, group_data in self.get("label", {}).items():
            if group_id == "single":
                for label_id, label_data in group_data.items():
                    label = Label(
                        category=LabelType.CUSTOM_SINGLE,
                        name=label_data["name"],
                        id=label_id,
                        description=label_data.get("description", ""),
                        color=label_data.get("color", ""),
                    )
                    name_to_obj[label_data["name"]] = id_to_obj[(group_id, label_id)] = label
            else:
                for label_id, label_data in group_data.get("label", {}).items():
                    label = Label(
                        category=LabelType(group_id) if group_id in (
                            "status", "version", "branch"
                        ) else LabelType.CUSTOM_GROUP,
                        name=label_data["name"],
                        id=label_id,
                        prefix=group_data["prefix"],
                        suffix=label_data["suffix"],
                        description=label_data.get("description", group_data.get("description", "")),
                        color=group_data.get("color", ""),
                    )
                    name_to_obj[label_data["name"]] = id_to_obj[(group_id, label_id)] = label
        return name_to_obj, id_to_obj

    def _make_issue_form(self, issue_form: dict) -> IssueForm:
        id_labels = [
            self.label_id_to_obj_map[(group_id, label_id)]
            for group_id, label_id in issue_form["id_labels"]
        ]
        return IssueForm(
            id=issue_form["id"],
            commit=self.commit_manager.create_release(id=issue_form["commit"]),
            id_labels=id_labels,
            issue_assignees=self.user_manager.from_issue_form_id(
                issue_form_id=issue_form["id"],
                assignment="issue",
            ),
            pull_assignees=self.user_manager.from_issue_form_id(
                issue_form_id=issue_form["id"],
                assignment="pull",
            ),
            review_assignees=self.user_manager.from_issue_form_id(
                issue_form_id=issue_form["id"],
                assignment="review",
            ),
            labels=[
                self.label_id_to_obj_map[(group_id, label_id)]
                for group_id, label_id in issue_form.get("labels", [])
            ],
            pre_process=issue_form.get("pre_process", {}),
            post_process=issue_form.get("post_process", {}),
            name=issue_form["name"],
            description=issue_form["description"],
            projects=issue_form.get("projects", []),
            title=issue_form.get("title", ""),
            body=issue_form.get("body", []),
        )