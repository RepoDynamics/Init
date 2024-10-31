from unicodedata import category

import pyserials as _ps
from loggerman import logger

from proman.datatype import (
    LabelType as _LabelType,
    Label as _Label,
    PrimaryActionCommitType as _PrimaryActionCommitType,
    IssueStatus as _IssueStatus,
    Issue as _Issue,
    CommitGroup as _CommitGroup,
    PrimaryActionCommit as _PrimaryActionCommit,
    PrimaryCustomCommit as _PrimaryCustomCommit,
    SecondaryActionCommit as _SecondaryActionCommit,
    SecondaryCustomCommit as _SecondaryCustomCommit,
    SecondaryActionCommitType as _SecondaryActionCommitType, LabelType,
)


class DataManager(_ps.NestedDict):

    def __init__(self, data: dict | _ps.NestedDict):
        if isinstance(data, _ps.NestedDict):
            data = data()
        super().__init__(data)
        self._commit_data: dict = {}
        self._issue_data: dict = {}
        self._label_name_to_obj: dict[str, _Label] = {}
        return

    @property
    def labels(self) -> dict[str, _Label]:
        """All repository labels, as a dictionary mapping full label names to Label objects."""
        if not self._label_name_to_obj:
            self._label_name_to_obj = self._initialize_label_data()
        return self._label_name_to_obj

    @property
    def primary_action_commit_type_ids(self) -> list[str]:
        return [enum.value for enum in _PrimaryActionCommitType]

    def resolve_labels(self, names: list[str]) -> dict[_LabelType, list[_Label]]:
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

    def resolve_label(self, name: str) -> _Label:
        """
        Resolve a label name to a label object.

        Parameters
        ----------
        name : str
            Name of the label.
        """
        label = self.labels.get(name)
        if label:
            return label
        logger.warning(
            "Label Resolution",
            f"Could not find label '{name}' in label data.",
        )
        return _Label(category=_LabelType.UNKNOWN, name=name)

    def get_issue_data_from_labels(self, label_names: list[str]) -> _Issue:
        if not self._issue_data:
            self._issue_data = self._initialize_issue_data()
        label_map = self.resolve_labels(label_names)
        if LabelType.TYPE not in label_map:
            raise ValueError(f"Could not find type label in {label_names}.")
        if len(label_map[LabelType.TYPE]) > 1:
            raise ValueError(f"Multiple type labels found in {label_names}.")
        if LabelType.SCOPE in label_map and len(label_map[LabelType.SCOPE]) > 1:
            raise ValueError(f"Multiple scope labels found in {label_names}.")
        type_label = label_map[LabelType.TYPE][0].name
        scope_label = label_map[LabelType.SCOPE][0].name if LabelType.SCOPE in label_map else None
        issue_data = self._issue_data.get((type_label, scope_label))
        if not issue_data:
            raise ValueError(
                f"Could not find issue form with type '{type_label}' "
                f"and scope '{scope_label}'."
            )
        return issue_data

    def get_branch_from_version(self, version: str) -> str:
        return self["project.version"][version]["branch"]

    def get_all_conventional_commit_types(self, secondary_only: bool = False) -> list[str]:
        if not self._commit_data:
            self._commit_data = self._initialize_commit_data()
        if secondary_only:
            return [
                conv_type for conv_type, commit_data in self._commit_data.items()
                if commit_data.group is _CommitGroup.SECONDARY_CUSTOM
            ]
        return list(self._commit_data.keys())

    def get_commit_type_from_conventional_type(
        self, conv_type: str
    ) -> _PrimaryActionCommit | _PrimaryCustomCommit | _SecondaryActionCommit | _SecondaryCustomCommit:
        if not self._commit_data:
            self._commit_data = self._initialize_commit_data()
        return self._commit_data[conv_type]

    def create_label_branch(self, source: _Label | str) -> _Label:
        prefix = self["label.branch.prefix"]
        if isinstance(source, str):
            branch_name = source
        elif isinstance(source, _Label):
            if source.category is not _LabelType.VERSION:
                raise ValueError(f"Label '{source.name}' is not a version label.")
            branch_name = self.get_branch_from_version(version=source.suffix)
        else:
            raise TypeError(f"Invalid type for source: {type(source)}")
        return _Label(
            category=_LabelType.BRANCH,
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
                    action=_PrimaryActionCommitType(group_id),
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

    def _initialize_issue_data(self) -> dict[tuple[str, str | None], _Issue]:
        issue_data = {}
        for issue_form in self["issue.forms"]:
            type_label_id = issue_form["type"]
            type_label = self[f"label.type.label.{type_label_id}.name"]
            type_labels = [type_label]
            scope_label_id = issue_form.get("scope")
            if scope_label_id:
                scope_label = self[f"label.scope.label.{scope_label_id}.name"]
                type_labels.append(scope_label)
            else:
                scope_label = None
            action = issue_form["action"]
            issue_data[(type_label, scope_label)] = _Issue(
                form=issue_form,
                action=_PrimaryActionCommitType(action) if action else None,
                identifying_labels=type_labels
            )
        return issue_data

    def _initialize_label_data(self) -> dict[str, _Label]:
        out = {}
        for label_type in ("type", "scope", "status"):
            group_data = self.get(f"label.{label_type}", {})
            if not group_data:
                continue
            for label_id, label_data in group_data.get("label", {}).items():
                out[label_data["name"]] = _Label(
                    category=LabelType(label_type),
                    name=label_data["name"],
                    id=label_id,
                    prefix=group_data["prefix"],
                    suffix=label_data["suffix"],
                    description=label_data.get("description", ""),
                    color=group_data.get("color", ""),
                )
        for group_id, group_data in self.get("label.custom.group", {}).items():
            for label_id, label_data in group_data.get("label", {}).items():
                out[label_data["name"]] = _Label(
                    category=LabelType.CUSTOM_GROUP,
                    name=label_data["name"],
                    group_id=group_id,
                    id=label_id,
                    prefix=group_data["prefix"],
                    suffix=label_data["suffix"],
                    description=label_data.get("description", ""),
                    color=group_data.get("color", ""),
                )
        for label_id, label_data in self.get("label.custom.single", {}).items():
            out[label_data["name"]] = _Label(
                category=LabelType.CUSTOM_SINGLE,
                name=label_data["name"],
                id=label_id,
                description=label_data.get("description", ""),
                color=label_data.get("color", ""),
            )
        for autogroup_name in ("version", "branch"):
            group_data = self[f"label.{autogroup_name}"]
            if not group_data:
                continue
            for label_data in group_data["labels"]:
                out[label_data["name"]] = _Label(
                    category=LabelType(autogroup_name),
                    name=label_data["name"],
                    prefix=group_data["prefix"],
                    suffix=label_data["suffix"],
                    description=label_data.get("description", ""),
                    color=label_data.get("color", ""),
                )
        return out