from versionman.pep440_semver import PEP440SemVer as _PEP440SemVer
import pyserials as _ps
from loggerman import logger

from proman.datatype import (
    BranchType as _BranchType,
    Branch as _Branch,
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
    SecondaryActionCommitType as _SecondaryActionCommitType,
)


class DataManager(_ps.NestedDict):

    def __init__(self, data: dict | _ps.NestedDict):
        if isinstance(data, _ps.NestedDict):
            data = data()
        super().__init__(data)
        self._commit_data: dict = {}
        self._issue_data: dict = {}
        return

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
        for label_data in self["label.all"]:
            if name == label_data["name"]:
                label = label_data
                break
        else:
            logger.warning(
                "Label Resolution",
                f"Could not find label '{name}' in label data.",
            )
            return _Label(category=_LabelType.UNKNOWN, name=name)

        label_type = label["type"]
        label_group_name = label["group_name"]
        label_id = label["id"]
        if label_type == "defined" and label_group_name == "status":
            suffix_type = _IssueStatus(label_id)
        elif label_type == "defined" and label_group_name == "type" and label_id in self.primary_action_commit_type_ids:
            suffix_type = _PrimaryActionCommitType(label_id)
        else:
            suffix_type = label_id
        return _Label(
            category=_LabelType(label["group_name" if label["type"] in ("defined", "auto") else "type"]),
            name=name,
            prefix=label["prefix"],
            type=suffix_type,
            description=label["description"],
            color=label["color"],
        )

    def get_issue_data_from_labels(self, label_names: list[str]) -> _Issue:
        if not self._issue_data:
            self._issue_data = self._initialize_issue_data()
        type_prefix = {
            "type": self["label.type.prefix"],
            "subtype": self["label.subtype.prefix"],
        }
        label = {}
        for label_name in label_names:
            for label_type, prefix in type_prefix.items():
                if prefix and label_name.startswith(prefix):
                    if label.get(label_type) is not None:
                        raise ValueError(f"Label '{label_name}' with type '{label_type}' is a duplicate.")
                    label[label_type] = label_name
                    break
        if "type" not in label:
            raise ValueError(f"Could not find type label in {label_names}.")
        issue_data = self._issue_data.get((label["type"], label.get("subtype")))
        if not issue_data:
            raise ValueError(
                f"Could not find issue type with primary type '{label['primary_type']}' "
                f"and sub type '{label.get('subtype')}'."
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

    def _initialize_issue_data(self):
        issue_data = {}
        for issue_form in self["issue.forms"]:
            type_label_id = issue_form["type"]
            type_label_prefix = self["label.type.prefix"]
            type_label_suffix = self[f"label.type.label.{type_label_id}.suffix"]
            type_label = f"{type_label_prefix}{type_label_suffix}"
            type_labels = [type_label]
            sub_id = issue_form.get("subtype")
            if sub_id:
                subtype_label_prefix = self["label.subtype.prefix"]
                subtype_label_suffix = self[f"label.subtype.label.{sub_id}.suffix"]
                subtype_label = f"{subtype_label_prefix}{subtype_label_suffix}"
                type_labels.append(subtype_label)
            else:
                subtype_label = None

            primary_commit = self[f"commit.primary.{type_label_id}"]
            if type_label_id in self.primary_action_commit_type_ids:
                commit = _PrimaryActionCommit(
                    action=_PrimaryActionCommitType(type_label_id),
                    conv_type=primary_commit["type"],
                )
            else:
                commit = _PrimaryCustomCommit(
                    group_id=type_label_id,
                    conv_type=primary_commit["type"],
                )
            issue_data[(type_label, subtype_label)] = _Issue(
                group_data=commit, type_labels=type_labels, form=issue_form
            )
        return issue_data

    @property
    def primary_action_commit_type_ids(self) -> list[str]:
        return [enum.value for enum in _PrimaryActionCommitType]

    # def _get_issue_labels(self, issue_number: int) -> tuple[dict[str, str | list[str]], list[str]]:
    #     label_prefix = {
    #         group_id: group_data["prefix"] for group_id, group_data in self["label"]["group"].items()
    #     }
    #     version_label_prefix = self["label"]["auto_group"]["version"]["prefix"]
    #     labels = (
    #         self._github_api.user(self["repo"]["owner"])
    #         .repo(self["repo"]["name"])
    #         .issue_labels(number=issue_number)
    #     )
    #     out_dict = {}
    #     out_list = []
    #     for label in labels:
    #         if label["name"].startswith(version_label_prefix):
    #             versions = out_dict.setdefault("version", [])
    #             versions.append(label["name"].removeprefix(version_label_prefix))
    #             continue
    #         for group_id, prefix in label_prefix.items():
    #             if label["name"].startswith(prefix):
    #                 if group_id in out_dict:
    #                     _logger.error(
    #                         f"Duplicate label group '{group_id}' found for issue {issue_number}.",
    #                         label["name"],
    #                     )
    #                 else:
    #                     out_dict[group_id] = label["name"].removeprefix(prefix)
    #                     break
    #         else:
    #             out_list.append(label["name"])
    #     for group_id in ("primary_type", "status"):
    #         if group_id not in out_dict:
    #             _logger.error(
    #                 f"Missing label group '{group_id}' for issue {issue_number}.",
    #                 out_dict,
    #             )
    #     return out_dict, out_list
    #
    # def get_issue_form_from_labels(self, label_names: list[str]) -> dict:
    #     """
    #     Get the issue form from a list of label names.
    #
    #     This is done by finding the primary type and subtype labels in the list of labels,
    #     finding their IDs, and then finding the issue form with the corresponding `primary_type`
    #     and `subtype`.
    #
    #     Parameters
    #     ----------
    #     label_names : list[str]
    #         List of label names.
    #
    #     Returns
    #     -------
    #     The corresponding form metadata in `issue.forms`.
    #     """
    #     prefix = {
    #         "primary_type": self["label"]["group"]["primary_type"]["prefix"],
    #         "subtype": self["label"]["group"].get("subtype", {}).get("prefix"),
    #     }
    #     suffix = {}
    #     for label_name in label_names:
    #         for label_type, prefix in prefix.items():
    #             if prefix and label_name.startswith(prefix):
    #                 if suffix.get(label_type) is not None:
    #                     raise ValueError(f"Label '{label_name}' with type {label_type} is a duplicate.")
    #                 suffix[label_type] = label_name.removeprefix(prefix)
    #                 break
    #     label_ids = {"primary_type": "", "subtype": ""}
    #     for label_id, label in self["label"]["group"]["primary_type"]["labels"].items():
    #         if label["suffix"] == suffix["primary_type"]:
    #             label_ids["primary_type"] = label_id
    #             break
    #     else:
    #         raise ValueError(f"Unknown primary type label suffix '{suffix['primary_type']}'.")
    #     if suffix["subtype"]:
    #         for label_id, label in self["label"]["group"]["subtype"]["labels"].items():
    #             if label["suffix"] == suffix["subtype"]:
    #                 label_ids["subtype"] = label_id
    #                 break
    #         else:
    #             raise ValueError(f"Unknown sub type label suffix '{suffix['subtype']}'.")
    #     for form in self["issue"]["forms"]:
    #         if (
    #             form["primary_type"] == label_ids["primary_type"]
    #             and form.get("subtype", "") == label_ids["subtype"]
    #         ):
    #             return form
    #     raise ValueError(
    #         f"Could not find issue form with primary type '{label_ids['primary_type']}' "
    #         f"and sub type '{label_ids['subtype']}'."
    #     )
    #
    # def get_issue_status_from_status_label(self, label_name: str):
    #     status_prefix = self["label"]["group"]["status"]["prefix"]
    #     if not label_name.startswith(status_prefix):
    #         raise ValueError(f"Label '{label_name}' is not a status label.")
    #     status = label_name.removeprefix(status_prefix)
    #     for status_label_id, status_label_info in self["label"]["group"]["status"]["labels"].items():
    #         if status_label_info["suffix"] == status:
    #             return _IssueStatus(status_label_id)
    #     raise ValueError(f"Unknown status label suffix '{status}'.")
    #
    # def get_primary_action_label_name(self, action_type: _PrimaryActionCommitType) -> str:
    #     """
    #     Get the label name for a primary action commit type.
    #
    #     Parameters
    #     ----------
    #     action_type : PrimaryActionCommitType
    #         Primary action commit type.
    #
    #     Returns
    #     -------
    #     The label name.
    #     """
    #     prefix = self["label"]["group"]["primary_type"]["prefix"]
    #     suffix = self["label"]["group"]["primary_type"]["labels"][action_type.value]["suffix"]
    #     return f"{prefix}{suffix}"
    #
    # def get_issue_form_identifying_labels(self, issue_form_id: str) -> tuple[str, str | None]:
    #     """
    #     Get the identifying labels for an issue form.
    #
    #     Each issue form is uniquely identified by a primary type label, and if necessary, a subtype label.
    #
    #     Returns
    #     -------
    #     A tuple of (primary_type, subtype) label names for the issue.
    #     Note that `subtype` may be `None`.
    #     """
    #     for form in self["issue"]["forms"]:
    #         if form["id"] == issue_form_id:
    #             issue_form = form
    #             break
    #     else:
    #         raise ValueError(f"Unknown issue form ID: {issue_form_id}")
    #     primary_type = issue_form["primary_type"]
    #     primary_type_label_name = self.get_label_grouped("primary_type", primary_type)["name"]
    #     subtype = issue_form.get("subtype")
    #     if subtype:
    #         subtype_label_name = self.get_label_grouped("subtype", subtype)["name"]
    #     else:
    #         subtype_label_name = None
    #     return primary_type_label_name, subtype_label_name
    #
    # def get_label_grouped(self, group_id: str, label_id: str) -> dict[str, str]:
    #     """
    #     Get information for a label in a label group.
    #
    #     Returns
    #     -------
    #     A dictionary with the following keys:
    #
    #     name : str
    #         Name of the label.
    #     color: str
    #         Color of the label in hex format.
    #     description: str
    #         Description of the label.
    #     """
    #     group = self["label"]["group"][group_id]
    #     label = group["labels"][label_id]
    #     out = {
    #         "name": f"{group['prefix']}{label['suffix']}",
    #         "color": group["color"],
    #         "description": label["description"],
    #     }
    #     return out