import pyserials as _ps
from loggerman import logger

from proman.datatype import (
    CommitGroup,
    IssueForm,
    Label,
    LabelType,
    ReleaseAction,
    ReleaseCommit,
)


class DataManager(_ps.NestedDict):

    def __init__(self, data: dict | _ps.NestedDict):
        if isinstance(data, _ps.NestedDict):
            data = data()
        super().__init__(data)
        self._commit_data: dict = {}
        self._issue_data: dict = {}
        self._label_name_to_obj: dict[str, Label] = {}
        self._label_id_to_obj: dict[tuple[str, str], Label] = {}
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
    def primary_action_commit_type_ids(self) -> list[str]:
        return [enum.value for enum in ReleaseAction]

    def issue_form_from_id(self, form_id: str) -> IssueForm:
        for issue_form in self["issue.forms"]:
            if issue_form["id"] == form_id:
                return self._make_issue_form(issue_form)
        raise ValueError(f"Could not find issue form with ID '{form_id}'.")

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

    def get_branch_from_version(self, version: str) -> str:
        return self["project.version"][version]["branch"]

    def get_all_conventional_commit_types(self, secondary_only: bool = False) -> list[str]:
        if not self._commit_data:
            self._commit_data = self._initialize_commit_data()
        if secondary_only:
            return [
                conv_type for conv_type, commit_data in self._commit_data.items()
                if commit_data.group is CommitGroup.SECONDARY_CUSTOM
            ]
        return list(self._commit_data.keys())

    def get_commit_type_from_conventional_type(
        self, conv_type: str
    ) -> _PrimaryActionCommit | _PrimaryCustomCommit | _SecondaryActionCommit | _SecondaryCustomCommit:
        if not self._commit_data:
            self._commit_data = self._initialize_commit_data()
        return self._commit_data[conv_type]

    def create_label_branch(self, source: Label | str) -> Label:
        prefix = self["label.branch.prefix"]
        if isinstance(source, str):
            branch_name = source
        elif isinstance(source, Label):
            if source.category is not LabelType.VERSION:
                raise ValueError(f"Label '{source.name}' is not a version label.")
            branch_name = self.get_branch_from_version(version=source.suffix)
        else:
            raise TypeError(f"Invalid type for source: {type(source)}")
        return Label(
            category=LabelType.BRANCH,
            name=f'{prefix}{branch_name}',
            prefix=prefix,
            suffix=branch_name,
            color=self.get("label.branch.color", ""),
            description=self.get("label.branch.description", ""),
        )

    def _initialize_commit_data(self):
        commit_type = {}
        for group_id, group_data in self["commit.primary"].items():
            if group_id in self.primary_action_commit_type_ids:
                commit_type[group_data["type"]] = _PrimaryActionCommit(
                    action=ReleaseAction(group_id),
                    conv_type=group_data["type"],
                )
            else:
                commit_type[group_data["type"]] = _PrimaryCustomCommit(
                    group_id=group_id,
                    conv_type=group_data["type"],
                )
        for conv_type, group_data in self["commit.secondary"].items():
            commit_type[conv_type] = _SecondaryCustomCommit(
                conv_type=conv_type,
                changelog_id=group_data["changelog_id"],
                changelog_section_id=group_data["changelog_section_id"],
            )
        for group_id, group_data in self["commit.auto"].items():
            commit_type[group_data["type"]] = _SecondaryActionCommit(
                action=_SecondaryActionCommitType(group_id),
                conv_type=group_data["type"],
            )
        return commit_type

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
        commit = self["commit.release"][issue_form["commit"]]
        return IssueForm(
            id=issue_form["id"],
            commit=ReleaseCommit(
                type=commit["type"],
                description=commit["description"],
                action=ReleaseAction(commit["action"]) if commit.get("action") else None,
                scope=commit.get("scope", ""),
                body=commit.get("body", ""),
                footer=commit.get("footer", {}),
                commit_description=commit.get("commit_description", ""),
            ),
            id_labels=id_labels,
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
            body=issue_form.get["body"],
        )