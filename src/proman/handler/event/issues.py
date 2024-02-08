import re
import datetime

from github_contexts import GitHubContext
from github_contexts.github.payloads.issues import IssuesPayload
from github_contexts.github.enums import ActionType
from loggerman import logger
from repodynamics.datatype import IssueStatus, LabelType, Label
from repodynamics.control.files.generator import forms

from proman.datatype import TemplateType
from proman.handler.main import EventHandler


class IssuesEventHandler(EventHandler):

    @logger.sectioner("Initialize Event Handler")
    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
        )
        self._payload: IssuesPayload = self._context.event
        self._issue = self._payload.issue

        self._label_groups: dict[LabelType, list[Label]] = {}
        return

    def _run_event(self):
        action = self._payload.action
        logger.info(title="Action", message=action.value)
        if action == ActionType.OPENED:
            self._run_opened()
        elif action == ActionType.LABELED:
            self._run_labeled()
        else:
            self.error_unsupported_triggering_action()
        return

    def _run_opened(self):
        issue_body = self._post_process_issue()
        dev_protocol = self._create_dev_protocol(issue_body)
        self._gh_api.issue_comment_create(number=self._issue.number, body=dev_protocol)
        return

    def _run_labeled(self):
        label = self._ccm_main.resolve_label(self._payload.label.name)
        if label.category is LabelType.STATUS:
            self._label_groups = self._ccm_main.resolve_labels(self._issue.label_names)
            self._update_issue_status_labels(
                issue_nr=self._issue.number,
                labels=self._label_groups[LabelType.STATUS],
                current_label=label,
            )
            self._run_labeled_status(label.type)
        return

    def _run_labeled_status(self, status: IssueStatus):
        if status is IssueStatus.TRIAGE:
            self._run_labeled_status_triage()
        elif status is IssueStatus.REJECTED:
            self._run_labeled_status_rejected()
        elif status is IssueStatus.DUPLICATE:
            self._run_labeled_status_duplicate()
        elif status is IssueStatus.INVALID:
            self._run_labeled_status_invalid()
        elif status is IssueStatus.PLANNING:
            self._run_labeled_status_planning()
        elif status is IssueStatus.REQUIREMENT_ANALYSIS:
            self._run_labeled_status_requirement_analysis()
        elif status is IssueStatus.DESIGN:
            self._run_labeled_status_design()
        elif status is IssueStatus.IMPLEMENTATION:
            self._run_labeled_status_implementation()
        return

    def _run_labeled_status_triage(self):
        # self._add_to_issue_timeline(entry=f"The issue entered the triage phase (actor: @{self._payload.sender.login}).")
        return

    def _run_labeled_status_rejected(self):
        self._add_to_issue_timeline(entry=f"The issue was rejected and closed (actor: @{self._payload.sender.login}).")
        self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
        return

    def _run_labeled_status_duplicate(self):
        self._add_to_issue_timeline(entry=f"The issue was marked as a duplicate and closed (actor: @{self._payload.sender.login}).")
        self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
        return

    def _run_labeled_status_invalid(self):
        self._add_to_issue_timeline(entry=f"The issue was marked as invalid and closed (actor: @{self._payload.sender.login}).")
        self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
        return

    def _run_labeled_status_planning(self):
        self._add_to_issue_timeline(entry=f"The issue entered the planning phase (actor: @{self._payload.sender.login}).")
        return

    def _run_labeled_status_requirement_analysis(self):
        self._add_to_issue_timeline(entry=f"The issue entered the requirement analysis phase (actor: @{self._payload.sender.login}).")
        return

    def _run_labeled_status_design(self):
        self._add_to_issue_timeline(entry=f"The issue entered the design phase (actor: @{self._payload.sender.login}).")
        return

    def _run_labeled_status_implementation(self):
        branches = self._gh_api.branches
        branch_sha = {branch["name"]: branch["commit"]["sha"] for branch in branches}
        pull_title, pull_body = self._get_pr_title_and_body()

        base_branches_and_labels: list[tuple[str, list[str]]] = []
        common_labels = []
        for label_group, group_labels in self._label_groups.items():
            if label_group not in [LabelType.BRANCH, LabelType.VERSION]:
                common_labels.extend([label.name for label in group_labels])
        if self._label_groups.get(LabelType.VERSION):
            for version_label in self._label_groups[LabelType.VERSION]:
                branch_label = self._ccm_main.create_label_branch(source=version_label)
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
    def _post_process_issue(self) -> str:
        logger.info(title="Issue labels", message=self._issue.label_names)
        issue_form = self._ccm_main.get_issue_data_from_labels(self._issue.label_names).form
        logger.success("Retrieve issue form", issue_form)
        issue_entries = self._extract_entries_from_issue_body(issue_form["body"])
        labels = []
        branch_label_prefix = self._ccm_main["label"]["auto_group"]["branch"]["prefix"]
        if "version" in issue_entries:
            versions = [version.strip() for version in issue_entries["version"].split(",")]
            version_label_prefix = self._ccm_main["label"]["auto_group"]["version"]["prefix"]
            for version in versions:
                labels.append(f"{version_label_prefix}{version}")
                branch = self._ccm_main.get_branch_from_version(version)
                labels.append(f"{branch_label_prefix}{branch}")
        elif "branch" in issue_entries:
            branches = [branch.strip() for branch in issue_entries["branch"].split(",")]
            for branch in branches:
                labels.append(f"{branch_label_prefix}{branch}")
        else:
            logger.error(
                "Could not match branch or version in issue body to pattern defined in metadata.",
            )
        self._gh_api.issue_labels_add(self._issue.number, labels)
        if "post_process" not in issue_form:
            logger.skip(
                "No post-process action defined in issue form; skip❗",
            )
            return self._issue.body
        assign_creator = issue_form["post_process"].get("assign_creator")
        if assign_creator:
            if_checkbox = assign_creator.get("if_checkbox")
            if if_checkbox:
                checkbox = issue_entries[if_checkbox["id"]].splitlines()[if_checkbox["number"] - 1]
                if checkbox.startswith("- [X]"):
                    checked = True
                elif not checkbox.startswith("- [ ]"):
                    logger.error(
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

    def _extract_entries_from_issue_body(self, body_elems: list[dict]):
        def create_pattern(parts):
            pattern_sections = []
            for idx, part in enumerate(parts):
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
            if not pre_process or forms.pre_process_existence(pre_process):
                optional = False
            else:
                optional = True
            parts.append({"id": elem.get("id"), "title": elem["attributes"]["label"], "optional": optional})
        pattern = create_pattern(parts)
        compiled_pattern = re.compile(pattern, re.S)
        # Search for the pattern in the markdown
        logger.success("Retrieve issue body", self._issue.body)
        match = re.search(compiled_pattern, self._issue.body)
        if not match:
            logger.error("Could not match the issue body to pattern defined in metadata.")
        # Create a dictionary with titles as keys and matched content as values
        sections = {
            section_id: content.strip() if content else None
            for section_id, content in match.groupdict().items()
        }
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
        dev_protocol_template = self._ccm_main["issue"]["dev_protocol"]["template"]
        dev_protocol_title = dev_protocol_template["title"]
        dev_protocol_body = dev_protocol_template["body"].format(**args).strip()
        return f"# {dev_protocol_title}\n\n{dev_protocol_body}\n"
