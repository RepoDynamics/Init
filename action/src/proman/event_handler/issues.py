import re
import datetime

from github_contexts import github as gh_context
from loggerman import logger
import controlman
from proman.datatype import IssueStatus

from proman.datatype import TemplateType
from proman.main import EventHandler


class IssuesEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: gh_context.payload.IssuesPayload = self._context.event
        self._issue = self._payload.issue

        self._label_groups: dict[controlman.datatype.LabelType, list[controlman.datatype.Label]] = {}
        return

    def _run_event(self):
        action = self._payload.action
        logger.info("Action", action.value)
        if action == gh_context.enum.ActionType.OPENED:
            return self._run_opened()
        if action == gh_context.enum.ActionType.LABELED:
            label = self._data_main.resolve_label(self._payload.label.name)
            if label.category is not controlman.datatype.LabelType.STATUS:
                return
            self._label_groups = self._data_main.resolve_labels(self._issue.label_names)
            self._update_issue_status_labels(
                issue_nr=self._issue.number,
                labels=self._label_groups[controlman.datatype.LabelType.STATUS],
                current_label=label,
            )
            return self._run_labeled_status(label.type)
        self.error_unsupported_triggering_action()
        return

    def _run_opened(self):
        self._reporter.event(f"Issue #{self._issue.number} opened")
        issue_body = self.process_issue()
        dev_protocol = self._create_dev_protocol(issue_body)
        self._gh_api.issue_comment_create(number=self._issue.number, body=dev_protocol)
        return

    def _run_labeled_status(self, status: IssueStatus):
        self._reporter.event(
            f"Issue #{self._issue.number} status changed to <code>{status.value}</code>"
        )
        if status in [IssueStatus.REJECTED, IssueStatus.DUPLICATE, IssueStatus.INVALID]:
            description = {
                IssueStatus.REJECTED: "rejected",
                IssueStatus.DUPLICATE: "marked as duplicate",
                IssueStatus.INVALID: "marked as invalid",
            }
            self._add_to_issue_timeline(
                entry=f"The issue was {description[status]} and closed (actor: @{self._payload.sender.login})."
            )
            self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
            return
        if status in [IssueStatus.PLANNING, IssueStatus.REQUIREMENT_ANALYSIS, IssueStatus.DESIGN]:
            self._add_to_issue_timeline(
                entry=f"The issue entered the <code>{status.value}</code> phase (actor: @{self._payload.sender.login})."
            )
            return
        if status is controlman.datatype.IssueStatus.IMPLEMENTATION:
            return self._run_labeled_status_implementation()
        return

    def _run_labeled_status_implementation(self):
        branches = self._gh_api.branches
        branch_sha = {branch["name"]: branch["commit"]["sha"] for branch in branches}
        pull_title, pull_body = self._get_pr_title_and_body()

        base_branches_and_labels: list[tuple[str, list[str]]] = []
        common_labels = []
        for label_group, group_labels in self._label_groups.items():
            if label_group not in [
                controlman.datatype.LabelType.BRANCH, controlman.datatype.LabelType.VERSION
            ]:
                common_labels.extend([label.name for label in group_labels])
        if self._label_groups.get(controlman.datatype.LabelType.VERSION):
            for version_label in self._label_groups[controlman.datatype.LabelType.VERSION]:
                branch_label = self._data_main.create_label_branch(source=version_label)
                labels = common_labels + [version_label.name, branch_label.name]
                base_branches_and_labels.append((branch_label.suffix, labels))
        else:
            for branch_label in self._label_groups[controlman.datatype.LabelType.BRANCH]:
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
                    f"init: Create implementation branch '{head_branch_name}' "
                    f"from base branch '{base_branch_name}' for issue #{self._issue.number}"
                ),
                allow_empty=True,
            )
            self._git_head.push(target="origin", set_upstream=True)
            pull_data = self._gh_api.pull_create(
                head=new_branch["name"],
                base=base_branch_name,
                title=pull_title,
                body=pull_body,
                maintainer_can_modify=True,
                draft=True,
            )
            self._gh_api.issue_labels_set(number=pull_data["number"], labels=labels)
            self._add_readthedocs_reference_to_pr(pull_nr=pull_data["number"], pull_body=pull_body)
            implementation_branches_info.append((head_branch_name, pull_data["number"]))
        timeline_entry_details = "\n".join(
            [
                f"  - #{pull_nr} (Branch: [{branch_name}]({self._gh_link.branch(branch_name).homepage}))"
                for branch_name, pull_nr in implementation_branches_info
            ]
        )
        self._add_to_issue_timeline(
            entry=(
                f"The issue entered the implementation phase (actor: @{self._payload.sender.login}).\n"
                f"The implementation is tracked in the following pull requests:\n{timeline_entry_details}"
            )
        )
        return

    def _add_to_issue_timeline(self, entry: str):
        comment = self._get_dev_protocol_comment()
        self._add_to_timeline(entry=entry, body=comment["body"], comment_id=comment["id"])
        return

    def _get_pr_title_and_body(self):
        dev_protocol_comment = self._get_dev_protocol_comment()
        body = dev_protocol_comment["body"]
        pattern = rf"{self._MARKER_COMMIT_START}(.*?){self._MARKER_COMMIT_END}"
        match = re.search(pattern, body, flags=re.DOTALL)
        title = match.group(1).strip()
        return title or self._issue.title, body

    def _get_dev_protocol_comment(self):
        comments = self._gh_api.issue_comments(number=self._issue.number, max_count=100)
        comment = comments[0]
        return comment

    @logger.sectioner("Process Issue")
    def process_issue(self) -> str:
        logger.info(code_title="Issue labels", code=self._issue.label_names)
        issue_form = self._data_main.get_issue_data_from_labels(self._issue.label_names).form
        logger.debug(code_title="Issue form", code=issue_form)
        issue_entries = self._extract_entries_from_issue_body(issue_form["body"])
        labels = []
        branch_label_prefix = self._data_main["label"]["auto_group"]["branch"]["prefix"]
        if "version" in issue_entries:
            versions = [version.strip() for version in issue_entries["version"].split(",")]
            version_label_prefix = self._data_main["label"]["auto_group"]["version"]["prefix"]
            for version in versions:
                labels.append(f"{version_label_prefix}{version}")
                branch = self._data_main.get_branch_from_version(version)
                labels.append(f"{branch_label_prefix}{branch}")
        elif "branch" in issue_entries:
            branches = [branch.strip() for branch in issue_entries["branch"].split(",")]
            for branch in branches:
                labels.append(f"{branch_label_prefix}{branch}")
        else:
            logger.critical(
                "Could not match branch or version in issue body to pattern defined in metadata.",
            )
        self._gh_api.issue_labels_add(self._issue.number, labels)
        if "post_process" not in issue_form:
            logger.info("Skip", "No post-process action defined in issue form.")
            return self._issue.body
        assign_creator = issue_form["post_process"].get("assign_creator")
        if assign_creator:
            if_checkbox = assign_creator.get("if_checkbox")
            if if_checkbox:
                checkbox = issue_entries[if_checkbox["id"]].splitlines()[if_checkbox["number"] - 1]
                if checkbox.startswith("- [X]"):
                    checked = True
                elif not checkbox.startswith("- [ ]"):
                    logger.critical(
                        "Could not match checkbox in issue body to pattern defined in metadata.",
                    )
                else:
                    checked = False
                if (if_checkbox["is_checked"] and checked) or (not if_checkbox["is_checked"] and not checked):
                    self._gh_api.issue_add_assignees(
                        number=self._issue.number, assignees=self._issue.user.login
                    )
        post_body = issue_form["post_process"].get("body")
        if post_body:
            new_body = post_body.format(**issue_entries)
            self._gh_api.issue_update(number=self._issue.number, body=new_body)
            return new_body
        return self._issue.body

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
            if not pre_process or controlman.pre_process_existence(pre_process):
                optional = False
            else:
                optional = True
            parts.append({"id": elem.get("id"), "title": elem["attributes"]["label"], "optional": optional})
        pattern = create_pattern(parts)
        compiled_pattern = re.compile(pattern, re.S)
        # Search for the pattern in the markdown
        logger.debug(code_title="Issue body", code=self._issue.body)
        match = re.search(compiled_pattern, self._issue.body)
        if not match:
            logger.critical("Could not match the issue body to pattern defined in control center settings.")
        # Create a dictionary with titles as keys and matched content as values
        sections = {
            section_id: content.strip() if content else None
            for section_id, content in match.groupdict().items()
        }
        logger.debug(code_title="Matched sections", code=sections)
        return sections

    def _create_dev_protocol(self, issue_body: str) -> str:
        now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y.%m.%d %H:%M:%S")
        timeline_entry = (
            f"- **{now}**: The issue was submitted (actor: @{self._issue.user.login})."
        )
        args = {
            "issue_number": f"{self._MARKER_ISSUE_NR_START}#{self._issue.number}{self._MARKER_ISSUE_NR_END}",
            "issue_body": issue_body,
            "primary_commit_summary": f"{self._MARKER_COMMIT_START}{self._MARKER_COMMIT_END}",
            "secondary_commits_tasklist": (
                f"{self._MARKER_TASKLIST_START}\n\n{self._MARKER_TASKLIST_END}"
            ),
            "references": f"{self._MARKER_REFERENCES_START}\n\n{self._MARKER_REFERENCES_END}",
            "timeline": f"{self._MARKER_TIMELINE_START}\n{timeline_entry}\n{self._MARKER_TIMELINE_END}",
        }
        dev_protocol_template = self._data_main["issue"]["dev_protocol"]["template"]
        dev_protocol_title = dev_protocol_template["title"]
        dev_protocol_body = dev_protocol_template["body"].format(**args).strip()
        return f"# {dev_protocol_title}\n\n{dev_protocol_body}\n"
