import re
import datetime
from enum import Enum

from github_contexts import github as gh_context
from loggerman import logger
from controlman.file_gen.forms import pre_process_existence
import mdit
import pylinks as pl

from proman.datatype import LabelType, Label, IssueStatus
from proman.exception import ProManException
from proman.main import EventHandler


class IssuesEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: gh_context.payload.IssuesPayload = self._context.event
        self._issue = self._payload.issue

        self._label_groups: dict[LabelType, list[Label]] = {}
        self._dev_protocol: str = ""
        self._dev_protocol_comment_id: int | None = None
        self._dev_protocol_issue_nr: int | None = None
        return

    @logger.sectioner("Issues Handler Execution")
    def run(self):
        action = self._payload.action
        if action == gh_context.enum.ActionType.OPENED:
            return self._run_opened()
        if self._data_main["doc.dev_protocol.as_comment"]:
            comments = self._gh_api.issue_comments(number=self._issue.number, max_count=100)
            dev_protocol_comment = comments[0]
            self._dev_protocol_comment_id = dev_protocol_comment.get("id")
            self._dev_protocol = dev_protocol_comment.get("body")
        else:
            self._dev_protocol = self._issue.body
            self._dev_protocol_issue_nr = self._issue.number
        if action == gh_context.enum.ActionType.LABELED:
            return self._run_labeled()
        # self.error_unsupported_triggering_action()
        return

    def _run_opened(self):

        def identify():
            ids = [form["id"] for form in self._data_main["issue.forms"]]
            id_pattern = '|'.join(map(re.escape, ids))
            pattern = rf"<!-- ISSUE-ID: ({id_pattern}) -->"
            match = re.search(pattern, self._issue.body)
            if not match:
                logger.critical(
                    "Issue ID Extraction",
                    "Could not match the issue ID in the issue body."
                )
                raise ProManException()
            issue_id = match.group(1)
            issue_form = next(form for form in self._data_main["issue.forms"] if form["id"] == issue_id)
            cleaned_body = re.sub(pattern, '', self._issue.body)
            return cleaned_body, issue_form

        def assign():
            assignees = []
            maintainers = self._data_main.get("maintainer.issue", {})
            if issue_form["id"] in maintainers.keys():
                for maintainer in maintainers[issue_form["id"]]:
                    assignees.append(
                        pl.api.github(token=self._context.token).user(maintainer["github"]["id"]).info
                    )
            assignment = issue_form.get("post_process", {}).get("assign_creator")
            if assignment:
                if_checkbox = assignment.get("if_checkbox")
                if if_checkbox:
                    checkbox = issue_entries[if_checkbox["id"]].splitlines()[if_checkbox["number"] - 1]
                    if checkbox.startswith("- [X]"):
                        checked = True
                    elif checkbox.startswith("- [ ]"):
                        checked = False
                    else:
                        logger.warning(
                            "Issue Assignment",
                            "Could not match checkbox in issue body to pattern defined in metadata.",
                        )
                        return
                    if (if_checkbox["is_checked"] and checked) or (
                        not if_checkbox["is_checked"] and not checked
                    ):
                        assignees.append(self._issue.user._user)
            self._gh_api.issue_add_assignees(
                number=self._issue.number, assignees=[assignee["login"] for assignee in assignees]
            )
            return assignees

        def add_labels():
            labels = []
            type_label_prefix = self._data_main["label.type.prefix"]
            type_label_suffix = self._data_main["label.type.label"][issue_form["type"]]["suffix"]
            labels.append(f"{type_label_prefix}{type_label_suffix}")
            if issue_form["subtype"]:
                subtype_label_prefix = self._data_main["label.subtype.prefix"]
                subtype_label_suffix = self._data_main["label.subtype.label"][issue_form["subtype"]]["suffix"]
                labels.append(f"{subtype_label_prefix}{subtype_label_suffix}")
            status_label_prefix = self._data_main["label.status.prefix"]
            status_label_suffix = self._data_main["label.status.label.triage.suffix"]
            labels.append(f"{status_label_prefix}{status_label_suffix}")

            branch_label_prefix = self._data_main["label.branch.prefix"]
            if "version" in issue_entries:
                versions = [version.strip() for version in issue_entries["version"].split(",")]
                version_label_prefix = self._data_main["label.version.prefix"]
                for version in versions:
                    labels.append(f"{version_label_prefix}{version}")
                    branch = self._data_main.get_branch_from_version(version)
                    labels.append(f"{branch_label_prefix}{branch}")
            elif "branch" in issue_entries:
                branches = [branch.strip() for branch in issue_entries["branch"].split(",")]
                for branch in branches:
                    labels.append(f"{branch_label_prefix}{branch}")
            else:
                logger.warning(
                    "Issue Label Update",
                    "Could not match branch or version in issue body to pattern defined in metadata.",
                )
            labels.extend(issue_form.get("labels", []))
            response = self._gh_api.issue_labels_set(self._issue.number, labels)
            logger.info(
                "Issue Labels Update",
                logger.pretty(response)
            )
            return labels

        def make_template_vars(body: str):
            data = {}
            for data_id, data_value in self._data_main["doc.dev_protocol.data"].items():
                marker_start, marker_end = self.make_text_marker(id=data_id)
                data[data_id] = f"{marker_start}{data_value}{marker_end}"
            env_vars = self._make_template_env_vars() | {
                "data": data,
                "form": issue_form,
                "input": issue_entries,
                "issue_body": body,
            }
            return env_vars

        self._reporter.event(f"Issue #{self._issue.number} opened")
        logger.info("Labels", str(self._issue.label_names))
        body, issue_form = identify()
        issue_form = self._data_main.get_issue_data_from_labels(self._issue.label_names).form
        issue_entries = self._extract_entries_from_issue_body(issue_form["body"])
        labels = add_labels()
        assignees = assign()
        body_template = issue_form.get("post_process", {}).get("body")
        if body_template:
            body_processed = self.fill_jinja_template(
                template=body_template,
                env_vars=make_template_vars(self._issue.body),
            )
        else:
            logger.info("Issue Post Processing", "No post-process action defined in issue form.")
            body_processed = self._issue.body
        dev_protocol_env_vars = make_template_vars(body_processed)
        dev_protocol = self.fill_jinja_template(
            template=self._data_main["doc.dev_protocol.template"],
            env_vars=dev_protocol_env_vars,
        )
        timeline_entries = []
        timeline_template = self._data_main.get("doc.dev_protocol.timeline_template", {})
        if "opened" in timeline_template:
            entry = self.fill_jinja_template(
                template=timeline_template["opened"],
                env_vars=dev_protocol_env_vars,
            )
            timeline_entries.append(entry)
        if "labeled" in timeline_template:
            for label in labels:
                label_env_var = self.make_label_env_var(self._data_main.resolve_label(label))
                env_vars = dev_protocol_env_vars | {"label": label_env_var}
                entry = self.fill_jinja_template(
                    template=timeline_template["labeled"],
                    env_vars=env_vars,
                )
                timeline_entries.append(entry)
        if "assigned" in timeline_template:
            for assignee in assignees:
                env_vars = dev_protocol_env_vars | {"assignee": assignee}
                entry = self.fill_jinja_template(
                    template=timeline_template["assigned"],
                    env_vars=env_vars,
                )
                timeline_entries.append(entry)
        if timeline_entries:
            timeline = "".join(timeline_entries)
            dev_protocol = self.add_data_to_marked_document(
                data=timeline, document=dev_protocol, data_id="timeline"
            )
        logger.info(
            "Development Protocol",
            mdit.element.code_block(dev_protocol)
        )
        if self._data_main["doc.dev_protocol.as_comment"]:
            response = self._gh_api.issue_update(number=self._issue.number, body=body_processed)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
            response = self._gh_api.issue_comment_create(number=self._issue.number, body=dev_protocol)
            logger.info(
                "Dev Protocol Comment",
                logger.pretty(response)
            )
        else:
            response = self._gh_api.issue_update(number=self._issue.number, body=dev_protocol)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
        return

    def _run_labeled(self):
        label = self._data_main.resolve_label(self._payload.label.name)
        timeline_entry_template = self._data_main["doc.dev_protocol.timeline_template.labeled"]
        if timeline_entry_template:
            env_vars = self._make_template_env_vars() | {
                "label": self.make_label_env_var(label),
            }
            entry = self.fill_jinja_template(template=timeline_entry_template, env_vars=env_vars)
            self._add_to_issue_timeline(entry=entry)
        if label.category is not LabelType.STATUS:
            return
        # Remove all other status labels
        self._label_groups = self._data_main.resolve_labels(self._issue.label_names)
        self._update_issue_status_labels(
            issue_nr=self._issue.number,
            labels=self._label_groups[LabelType.STATUS],
            current_label=label,
        )
        self._reporter.event(f"Issue #{self._issue.number} status changed to `{label.type.value}`")
        if label.type in [IssueStatus.REJECTED, IssueStatus.DUPLICATE, IssueStatus.INVALID]:
            self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
            return
        if label.type is IssueStatus.IMPLEMENTATION:
            return self._run_labeled_status_implementation()
        return

    def _run_labeled_status_implementation(self):
        branches = self._gh_api.branches
        branch_sha = {branch["name"]: branch["commit"]["sha"] for branch in branches}
        base_branches_and_labels: list[tuple[str, list[str]]] = []
        common_labels = []
        for label_group, group_labels in self._label_groups.items():
            if label_group not in [LabelType.BRANCH, LabelType.VERSION]:
                common_labels.extend([label.name for label in group_labels])
        if self._label_groups.get(LabelType.VERSION):
            for version_label in self._label_groups[LabelType.VERSION]:
                branch_label = self._data_main.create_label_branch(source=version_label)
                labels = common_labels + [version_label.name, branch_label.name]
                base_branches_and_labels.append((branch_label.suffix, labels))
        else:
            for branch_label in self._label_groups[LabelType.BRANCH]:
                base_branches_and_labels.append((branch_label.suffix, common_labels + [branch_label.name]))
        implementation_branches_info = []
        for base_branch_name, labels in base_branches_and_labels:
            head_branch_name = self.create_branch_name_implementation(
                issue_nr=self._issue.number, base_branch_name=base_branch_name
            )
            new_branch = self._gh_api_admin.branch_create_linked(
                issue_id=self._issue.node_id,
                base_sha=branch_sha[base_branch_name],
                name=head_branch_name,
            )
            # Create empty commit on dev branch to be able to open a draft pull request
            # Ref: https://stackoverflow.com/questions/46577500/why-cant-i-create-an-empty-pull-request-for-discussion-prior-to-developing-chan
            self._git_head.fetch_remote_branches_by_name(branch_names=head_branch_name)
            self._git_head.checkout(head_branch_name)
            self._git_head.commit(
                message=(
                    f"init: Create development branch '{head_branch_name}' "
                    f"from base branch '{base_branch_name}' for issue #{self._issue.number}"
                ),
                allow_empty=True,
            )
            self._git_head.push(target="origin", set_upstream=True)
            pull_data = self._gh_api.pull_create(
                head=new_branch["name"],
                base=base_branch_name,
                title=self._get_pr_title(),
                body=self._dev_protocol,
                maintainer_can_modify=True,
                draft=True,
            )
            self._gh_api.issue_labels_set(number=pull_data["number"], labels=labels)
            self._add_readthedocs_reference_to_pr(pull_nr=pull_data["number"], pull_body=self._dev_protocol)
            implementation_branches_info.append(
                {
                    "branch_name": head_branch_name,
                    "branch_url": self._gh_link.branch(head_branch_name).homepage,
                    "pull_number": pull_data["number"],
                }
            )
        if self._data_main["doc.dev_protocol.data.pr_list"]:
            template = self._data_main["doc.dev_protocol.pr_list_template"]
            env_vars = {"targets": implementation_branches_info}
            entry = self.fill_jinja_template(template=template, env_vars=env_vars)
            new_protocol = self.add_data_to_marked_document(data=entry, document=self._dev_protocol, data_id="pr_list")
            self._update_dev_protocol(new_protocol=new_protocol)
        return

    def _add_to_issue_timeline(self, entry: str):
        self._dev_protocol = self.add_data_to_marked_document(
            data=entry, document=self._dev_protocol, data_id="timeline", replace=False
        )
        self._update_dev_protocol(new_protocol=self._dev_protocol)
        return

    def _update_dev_protocol(self, new_protocol: str):
        self._dev_protocol = new_protocol
        if self._dev_protocol_issue_nr:
            return self._gh_api.issue_update(number=self._dev_protocol_issue_nr, body=new_protocol)
        return self._gh_api.issue_comment_update(comment_id=self._dev_protocol_comment_id, body=new_protocol)

    def _get_pr_title(self):
        marker_start, marker_end = self.make_text_marker(id="commit_summary")
        pattern = rf"{re.escape(marker_start)}(.*?){re.escape(marker_end)}"
        match = re.search(pattern, self._dev_protocol, flags=re.DOTALL)
        title = match.group(1).strip()
        return title or self._issue.title

    def _make_template_env_vars(self):
        return self.make_base_template_env_vars() | {
            "actor": self._payload.sender,
            "payload": self._payload,
            "issue": self._issue,
        }

    def make_label_env_var(self, label: Label):
        return {
            "category": label.category.value,
            "id": label.type.value if isinstance(label.type, Enum) else label.type,
        }

    @logger.sectioner("Extract Entries from Issue Body")
    def _extract_entries_from_issue_body(self, body_elems: list[dict]):
        def create_pattern(parts_):
            pattern_sections = []
            for idx, part in enumerate(parts_):
                pattern_content = f"(?P<{part['id']}>.*)" if part["id"] else "(?:.*)"
                pattern_section = rf"### {re.escape(part['title'])}\n{pattern_content}"
                if idx != 0:
                    pattern_section = f"\n{pattern_section}"
                if part["optional"]:
                    pattern_section = f"(?:{pattern_section})?"
                pattern_sections.append(pattern_section)
            return "".join(pattern_sections)

        parts = []
        for elem in body_elems:
            if elem["type"] == "markdown":
                continue
            pre_process = elem.get("pre_process")
            if not pre_process or pre_process_existence(pre_process):
                optional = False
            else:
                optional = True
            parts.append({"id": elem.get("id"), "title": elem["attributes"]["label"], "optional": optional})
        pattern = create_pattern(parts)
        compiled_pattern = re.compile(pattern, re.S)
        # Search for the pattern in the markdown
        logger.debug("Issue body", mdit.element.code_block(self._issue.body))
        match = re.search(compiled_pattern, self._issue.body)
        if not match:
            logger.critical(
                "Issue Body Pattern Matching",
                "Could not match the issue body to pattern defined in control center settings."
            )
        # Create a dictionary with titles as keys and matched content as values
        sections = {
            section_id: content.strip() if content else None
            for section_id, content in match.groupdict().items()
        }
        logger.debug("Matched sections", str(sections))
        return sections
