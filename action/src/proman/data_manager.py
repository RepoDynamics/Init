from versionman.pep440_semver import PEP440SemVer as _PEP440SemVer
import pyserials as _ps

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
        self._version_to_branch_map: dict[str, str] = {}
        return

    def get_label_grouped(self, group_id: str, label_id: str) -> dict[str, str]:
        """
        Get information for a label in a label group.

        Returns
        -------
        A dictionary with the following keys:

        name : str
            Name of the label.
        color: str
            Color of the label in hex format.
        description: str
            Description of the label.
        """
        group = self._data["label"]["group"][group_id]
        label = group["labels"][label_id]
        out = {
            "name": f"{group['prefix']}{label['suffix']}",
            "color": group["color"],
            "description": label["description"],
        }
        return out

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
        def get_label_id():
            for label_id, label_data in group["labels"].items():
                if label_data["suffix"] == label_suffix:
                    break
            else:
                raise ValueError(f"Unknown label suffix '{label_suffix}' for group '{group_id}'.")
            return label_id

        for autogroup_id, autogroup in self._data["label"]["auto_group"].items():
            prefix = autogroup["prefix"]
            if name.startswith(prefix):
                return _Label(
                    category=_LabelType(autogroup_id),
                    name=name,
                    prefix=prefix,
                )
        for group_id, group in self._data["label"]["group"].items():
            prefix = group["prefix"]
            if name.startswith(prefix):
                label_suffix = name.removeprefix(prefix)
                if group_id == "primary_type":
                    category = _LabelType.TYPE
                    label_id = get_label_id()
                    try:
                        suffix_type = _PrimaryActionCommitType(label_id)
                    except ValueError:
                        suffix_type = label_id
                elif group_id == "subtype":
                    category = _LabelType.SUBTYPE
                    suffix_type = get_label_id()
                elif group_id == "status":
                    category = _LabelType.STATUS
                    suffix_type = _IssueStatus(get_label_id())
                else:
                    category = _LabelType.CUSTOM_GROUP
                    suffix_type = get_label_id()
                return _Label(
                    category=category,
                    name=name,
                    prefix=prefix,
                    type=suffix_type,
                )
        for label_id, label in self._data["label"]["single"].items():
            if name == label["name"]:
                return _Label(
                    category=_LabelType.SINGLE,
                    name=name,
                    type=label_id,
                )
        return _Label(category=_LabelType.UNKNOWN, name=name)

    def get_primary_action_label_name(self, action_type: _PrimaryActionCommitType) -> str:
        """
        Get the label name for a primary action commit type.

        Parameters
        ----------
        action_type : PrimaryActionCommitType
            Primary action commit type.

        Returns
        -------
        The label name.
        """
        prefix = self._data["label"]["group"]["primary_type"]["prefix"]
        suffix = self._data["label"]["group"]["primary_type"]["labels"][action_type.value]["suffix"]
        return f"{prefix}{suffix}"

    def get_issue_form_identifying_labels(self, issue_form_id: str) -> tuple[str, str | None]:
        """
        Get the identifying labels for an issue form.

        Each issue form is uniquely identified by a primary type label, and if necessary, a subtype label.

        Returns
        -------
        A tuple of (primary_type, subtype) label names for the issue.
        Note that `subtype` may be `None`.
        """
        for form in self._data["issue"]["forms"]:
            if form["id"] == issue_form_id:
                issue_form = form
                break
        else:
            raise ValueError(f"Unknown issue form ID: {issue_form_id}")
        primary_type = issue_form["primary_type"]
        primary_type_label_name = self.get_label_grouped("primary_type", primary_type)["name"]
        subtype = issue_form.get("subtype")
        if subtype:
            subtype_label_name = self.get_label_grouped("subtype", subtype)["name"]
        else:
            subtype_label_name = None
        return primary_type_label_name, subtype_label_name

    def get_issue_form_from_labels(self, label_names: list[str]) -> dict:
        """
        Get the issue form from a list of label names.

        This is done by finding the primary type and subtype labels in the list of labels,
        finding their IDs, and then finding the issue form with the corresponding `primary_type`
        and `subtype`.

        Parameters
        ----------
        label_names : list[str]
            List of label names.

        Returns
        -------
        The corresponding form metadata in `issue.forms`.
        """
        prefix = {
            "primary_type": self._data["label"]["group"]["primary_type"]["prefix"],
            "subtype": self._data["label"]["group"].get("subtype", {}).get("prefix"),
        }
        suffix = {}
        for label_name in label_names:
            for label_type, prefix in prefix.items():
                if prefix and label_name.startswith(prefix):
                    if suffix.get(label_type) is not None:
                        raise ValueError(f"Label '{label_name}' with type {label_type} is a duplicate.")
                    suffix[label_type] = label_name.removeprefix(prefix)
                    break
        label_ids = {"primary_type": "", "subtype": ""}
        for label_id, label in self._data["label"]["group"]["primary_type"]["labels"].items():
            if label["suffix"] == suffix["primary_type"]:
                label_ids["primary_type"] = label_id
                break
        else:
            raise ValueError(f"Unknown primary type label suffix '{suffix['primary_type']}'.")
        if suffix["subtype"]:
            for label_id, label in self._data["label"]["group"]["subtype"]["labels"].items():
                if label["suffix"] == suffix["subtype"]:
                    label_ids["subtype"] = label_id
                    break
            else:
                raise ValueError(f"Unknown sub type label suffix '{suffix['subtype']}'.")
        for form in self._data["issue"]["forms"]:
            if (
                form["primary_type"] == label_ids["primary_type"]
                and form.get("subtype", "") == label_ids["subtype"]
            ):
                return form
        raise ValueError(
            f"Could not find issue form with primary type '{label_ids['primary_type']}' "
            f"and sub type '{label_ids['subtype']}'."
        )

    def get_issue_data_from_labels(self, label_names: list[str]) -> _Issue:
        type_prefix = {
            "primary_type": self._data["label"]["group"]["primary_type"]["prefix"],
            "subtype": self._data["label"]["group"].get("subtype", {}).get("prefix"),
        }
        label = {}
        for label_name in label_names:
            for label_type, prefix in type_prefix.items():
                if prefix and label_name.startswith(prefix):
                    if label.get(label_type) is not None:
                        raise ValueError(f"Label '{label_name}' with type '{label_type}' is a duplicate.")
                    label[label_type] = label_name
                    break
        if "primary_type" not in label:
            raise ValueError(f"Could not find primary type label in {label_names}.")

        key = (label["primary_type"], label.get("subtype"))

        if not self._issue_data:
            self._issue_data = self._initialize_issue_data()

        issue_data = self._issue_data.get(key)

        if not issue_data:
            raise ValueError(
                f"Could not find issue type with primary type '{label['primary_type']}' "
                f"and sub type '{label.get('subtype')}'."
            )
        return issue_data

    def _get_issue_labels(self, issue_number: int) -> tuple[dict[str, str | list[str]], list[str]]:
        label_prefix = {
            group_id: group_data["prefix"] for group_id, group_data in self._data["label"]["group"].items()
        }
        version_label_prefix = self._data["label"]["auto_group"]["version"]["prefix"]
        labels = (
            self._github_api.user(self._data["repo"]["owner"])
            .repo(self._data["repo"]["name"])
            .issue_labels(number=issue_number)
        )
        out_dict = {}
        out_list = []
        for label in labels:
            if label["name"].startswith(version_label_prefix):
                versions = out_dict.setdefault("version", [])
                versions.append(label["name"].removeprefix(version_label_prefix))
                continue
            for group_id, prefix in label_prefix.items():
                if label["name"].startswith(prefix):
                    if group_id in out_dict:
                        _logger.error(
                            f"Duplicate label group '{group_id}' found for issue {issue_number}.",
                            label["name"],
                        )
                    else:
                        out_dict[group_id] = label["name"].removeprefix(prefix)
                        break
            else:
                out_list.append(label["name"])
        for group_id in ("primary_type", "status"):
            if group_id not in out_dict:
                _logger.error(
                    f"Missing label group '{group_id}' for issue {issue_number}.",
                    out_dict,
                )
        return out_dict, out_list

    def get_all_conventional_commit_types(self, secondary_custom_only: bool = False) -> list[str]:
        if not self._commit_data:
            self._commit_data = self._initialize_commit_data()
        if not secondary_custom_only:
            return list(self._commit_data.keys())
        return [
            conv_type for conv_type, commit_data in self._commit_data.items()
            if commit_data.group is _CommitGroup.SECONDARY_CUSTOM
        ]

    def get_commit_type_from_conventional_type(
        self, conv_type: str
    ) -> _PrimaryActionCommit | _PrimaryCustomCommit | _SecondaryActionCommit | _SecondaryCustomCommit:
        if self._commit_data:
            return self._commit_data[conv_type]
        self._commit_data = self._initialize_commit_data()
        return self._commit_data[conv_type]

    def get_branch_from_version(self, version: str) -> str:
        if self._version_to_branch_map:
            return self._version_to_branch_map[version]
        if not self._data.get("package"):
            raise ValueError("No package metadata found.")
        self._version_to_branch_map = {
            release["version"]: release["branch"]
            for release in self._data["package"]["releases"]["per_branch"]
        }
        return self._version_to_branch_map[version]

    def get_issue_status_from_status_label(self, label_name: str):
        status_prefix = self._data["label"]["group"]["status"]["prefix"]
        if not label_name.startswith(status_prefix):
            raise ValueError(f"Label '{label_name}' is not a status label.")
        status = label_name.removeprefix(status_prefix)
        for status_label_id, status_label_info in self._data["label"]["group"]["status"]["labels"].items():
            if status_label_info["suffix"] == status:
                return _IssueStatus(status_label_id)
        raise ValueError(f"Unknown status label suffix '{status}'.")

    def create_label_branch(self, source: _Label | str) -> _Label:
        prefix = self._data["label"]["auto_group"]["branch"]["prefix"]
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
        for group_id, group_data in self._data["commit"]["primary_action"].items():
            commit_type[group_data["type"]] = _PrimaryActionCommit(
                action=_PrimaryActionCommitType(group_id),
                conv_type=group_data["type"],
            )
        for group_id, group_data in self._data["commit"]["primary_custom"].items():
            commit_type[group_data["type"]] = _PrimaryCustomCommit(
                group_id=group_id,
                conv_type=group_data["type"],
            )
        for group_id, group_data in self._data["commit"]["secondary_action"].items():
            commit_type[group_data["type"]] = _SecondaryActionCommit(
                action=_SecondaryActionCommitType(group_id),
                conv_type=group_data["type"],
            )
        for conv_type, group_data in self._data["commit"]["secondary_custom"].items():
            commit_type[conv_type] = _SecondaryCustomCommit(
                conv_type=conv_type,
                changelog_id=group_data["changelog_id"],
                changelog_section_id=group_data["changelog_section_id"],
            )
        return commit_type

    def _initialize_issue_data(self):
        issue_data = {}
        for issue in self._data["issue"]["forms"]:
            prim_id = issue["primary_type"]

            prim_label_prefix = self._data["label"]["group"]["primary_type"]["prefix"]
            prim_label_suffix = self._data["label"]["group"]["primary_type"]["labels"][prim_id]["suffix"]
            prim_label = f"{prim_label_prefix}{prim_label_suffix}"

            type_labels = [prim_label]

            sub_id = issue.get("subtype")
            if sub_id:
                sub_label_prefix = self._data["label"]["group"]["subtype"]["prefix"]
                sub_label_suffix = self._data["label"]["group"]["subtype"]["labels"][sub_id]["suffix"]
                sub_label = f"{sub_label_prefix}{sub_label_suffix}"
                type_labels.append(sub_label)
            else:
                sub_label = None

            key = (prim_label, sub_label)

            prim_commit = self._data["commit"]["primary_action"].get(prim_id)
            if prim_commit:
                commit = _PrimaryActionCommit(
                    action=_PrimaryActionCommitType(prim_id),
                    conv_type=prim_commit["type"],
                )
            else:
                commit = _PrimaryCustomCommit(
                    group_id=prim_id,
                    conv_type=self._data["commit"]["primary_custom"][prim_id]["type"],
                )

            issue_data[key] = _Issue(group_data=commit, type_labels=type_labels, form=issue)
        return issue_data
