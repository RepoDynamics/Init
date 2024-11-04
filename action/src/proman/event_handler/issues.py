import re
from enum import Enum
import copy

from github_contexts import github as gh_context
from loggerman import logger
from controlman.file_gen.forms import pre_process_existence
from controlman import data_helper
import mdit

from proman.datatype import LabelType, Label, IssueStatus
from proman.exception import ProManException
from proman.main import EventHandler
from proman.changelog_manager import ChangelogManager


class IssuesEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: gh_context.payload.IssuesPayload = self._context.event
        self._issue = self._payload.issue
        self._issue_author = self._user_manager.from_issue_author(self._issue)
        issue_payload = copy.deepcopy(self._issue.as_dict)
        issue_payload["user"] = self._issue_author
        self._jinja_env_vars["issue"] = issue_payload

        self._label_groups: dict[LabelType, list[Label]] = {}
        self._protocol_comment_id: int | None = None
        self._protocol_issue_nr: int | None = None
        return

    @logger.sectioner("Issues Handler Execution")
    def run(self):
        action = self._payload.action
        if action == gh_context.enum.ActionType.OPENED:
            return self._run_opened()
        if self._data_main["doc.protocol.as_comment"]:
            comments = self._gh_api.issue_comments(number=self._issue.number, max_count=100)
            protocol_comment = comments[0]
            self._protocol_comment_id = protocol_comment.get("id")
            self._devdoc.protocol = protocol_comment.get("body")
        else:
            self._devdoc.protocol = self._issue.body
            self._protocol_issue_nr = self._issue.number
        if action == gh_context.enum.ActionType.LABELED:
            return self._run_labeled()
        if action == gh_context.enum.ActionType.ASSIGNED:
            return self._run_assignment(assigned=True)
        if action == gh_context.enum.ActionType.UNASSIGNED:
            return self._run_assignment(assigned=False)
        self._devdoc.add_timeline_entry()
        self._update_protocol()
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

        def assign() -> list[dict]:
            out = self.get_assignees_for_issue_type(issue_id=issue_form["id"], assignment="issue")
            assignment = issue_form.get("post_process", {}).get("assign_creator")
            if assignment:
                if_checkbox = assignment.get("if_checkbox")
                if if_checkbox:
                    checkbox = issue_entries[if_checkbox["id"]].splitlines()[if_checkbox["number"] - 1]
                    if checkbox.startswith("- [X]"):
                        checked = True
                        process = True
                    elif checkbox.startswith("- [ ]"):
                        checked = False
                        process = True
                    else:
                        logger.warning(
                            "Issue Assignment",
                            "Could not match checkbox in issue body to pattern defined in metadata.",
                        )
                        checked = None
                        process = False
                    if process and (
                        (if_checkbox["is_checked"] and checked) or
                        (not if_checkbox["is_checked"] and not checked)
                    ):
                        for assignee in out:
                            if assignee["github"]["id"] == self._issue_author["github"]["id"]:
                                break
                        else:
                            out.append(self._issue_author)
            self._gh_api.issue_add_assignees(
                number=self._issue.number, assignees=[assignee["github"]["id"] for assignee in out]
            )
            return out

        def add_labels():
            labels = [self._data_main["label.status.label.triage.name"]]
            for label_group_id, label_id in issue_form["id_labels"] + issue_form.get("labels", []):
                labels.append(
                    self._data_main["label"][label_group_id]["label"][label_id]["name"]
                )
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
            response = self._gh_api.issue_labels_set(self._issue.number, list(set(labels)))
            logger.info(
                "Issue Labels Update",
                logger.pretty(response)
            )
            return labels

        self._reporter.event(f"Issue #{self._issue.number} opened")
        body, issue_form = identify()
        issue_entries = self._extract_entries_from_issue_body(body, issue_form["body"])
        labels = add_labels()
        assignees = assign()
        body_template = issue_form.get("post_process", {}).get("body")
        if body_template:
            body_processed = self._devdoc.generate(
                template=body_template, issue_form=issue_form, issue_inputs=issue_entries, issue_body=body
            )
        else:
            logger.info("Issue Post Processing", "No post-process action defined in issue form.")
            body_processed = body
        self._devdoc.generate(
            template=self._data_main["doc.protocol.template"],
            issue_form=issue_form,
            issue_inputs=issue_entries,
            issue_body=body_processed,
        )
        self._devdoc.add_timeline_entry()
        self._devdoc.update_status(IssueStatus.TRIAGE)
        for assignee in assignees:
            self._devdoc.add_timeline_entry(
                env_vars={
                    "action": "assigned",
                    "assignee": assignee,
                },
            )
        for label in labels:
            self._devdoc.add_timeline_entry(
                env_vars={
                    "action": "labeled",
                    "label": self.make_label_env_var(self._data_main.resolve_label(label))
                },
            )
        logger.info(
            "Development Protocol",
            mdit.element.code_block(self._devdoc.protocol)
        )
        if self._data_main["doc.protocol.as_comment"]:
            response = self._gh_api.issue_update(number=self._issue.number, body=body_processed)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
            response = self._gh_api.issue_comment_create(number=self._issue.number, body=self._devdoc.protocol)
            logger.info(
                "Dev Protocol Comment",
                logger.pretty(response)
            )
        else:
            response = self._gh_api.issue_update(number=self._issue.number, body=self._devdoc.protocol)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
        return

    def _run_labeled(self):
        label = self._data_main.resolve_label(self._payload.label.name)
        self._devdoc.add_timeline_entry(env_vars={"label": self.make_label_env_var(label)})
        if label.category is not LabelType.STATUS:
            self._reporter.event(f"Issue #{self._issue.number} labeled `{label.name}`")
            self._update_protocol()
            return
        self._devdoc.update_status(label.id)
        # Remove all other status labels
        self._label_groups = self._data_main.resolve_labels(self._issue.label_names)
        self._update_issue_status_labels(
            issue_nr=self._issue.number,
            labels=self._label_groups[LabelType.STATUS],
            current_label=label,
        )
        if label.type in [IssueStatus.REJECTED, IssueStatus.DUPLICATE, IssueStatus.INVALID]:
            self._gh_api.issue_update(number=self._issue.number, state="closed", state_reason="not_planned")
        elif label.type is IssueStatus.IMPLEMENTATION:
            self._run_labeled_status_implementation()
        self._update_protocol()
        return

    def _run_labeled_status_implementation(self):

        def assign_pr():
            assignees = self.get_assignees_for_issue_type(issue_id=issue_form.id, assignment="pull")
            if assignees:
                response = self._gh_api.issue_add_assignees(
                    number=pull_data["number"], assignees=[assignee["github"]["id"] for assignee in assignees]
                )
                logger.info(
                    "Pull Request Assignment",
                    logger.pretty(response)
                )
            else:
                logger.info("Pull Request Assignment", "No assignees found for pull request.")
            return assignees

        issue_form = self._data_main.issue_form_from_id_labels(self._issue.label_names)
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
            new_branch = self._gh_api.branch_create_linked(
                issue_id=self._issue.node_id,
                base_sha=branch_sha[base_branch_name],
                name=head_branch_name,
            )

            self._git_head.fetch_remote_branches_by_name(branch_names=head_branch_name)
            self._git_head.checkout(head_branch_name)

            # Create changelog entry
            changelogger = ChangelogManager(self._path_head / self._data_main["doc.changelog.path"])
            changelog_entry = {
                "id": issue_form.id,
                "issue": self._create_changelog_issue_entry(),
            }
            if self._issue.milestone:
                changelog_entry["milestone"] = self._create_changelog_milestone_entry()
            changelogger.update_current(changelog_entry)
            # Write initial changelog to create a commit on dev branch to be able to open a draft pull request
            # Ref: https://stackoverflow.com/questions/46577500/why-cant-i-create-an-empty-pull-request-for-discussion-prior-to-developing-chan
            changelogger.write()

            branch_data = {
                "head": {
                    "name": head_branch_name,
                    "url": self._gh_link.branch(head_branch_name).homepage,
                },
                "base": {
                    "name": base_branch_name,
                    "sha": branch_sha[base_branch_name],
                    "url": self._gh_link.branch(base_branch_name).homepage,
                },
            }
            self._git_head.commit(
                message=self._commit_manager.create_auto_commit_msg(
                    commit_type="dev_branch_creation",
                    env_vars=branch_data,
                )
            )
            self._git_head.push(target="origin", set_upstream=True)
            pull_data = self._gh_api.pull_create(
                head=new_branch["name"],
                base=base_branch_name,
                title=self._devdoc.get_pr_title() or self._issue.title,
                body=self._devdoc.protocol,
                maintainer_can_modify=True,
                draft=True,
            )
            logger.info(
                f"Pull Request Creation ({new_branch['name']} -> {base_branch_name})",
                logger.pretty(pull_data)
            )
            label_data = self._gh_api.issue_labels_set(number=pull_data["number"], labels=labels)
            logger.info(
                f"Pull Request Labels Update ({new_branch['name']} -> {base_branch_name})",
                logger.pretty(label_data)
            )
            assignees = assign_pr()

            pull_data["user"] = self._user_manager.get_from_github_rest_id(pull_data["user"]["id"])
            pull_data["head"] = branch_data["head"] | pull_data["head"]
            pull_data["base"] = branch_data["base"] | pull_data["base"]
            self._devdoc.add_timeline_entry(
                env_vars={
                    "event": "pull_request",
                    "action": "opened",
                    "pull_request": pull_data,
                },
            )

            # Update changelog entry with pull request information
            changelogger.update_current({"pull_request": self._create_changelog_pull_entry(pull_data)})
            changelogger.write()

            implementation_branches_info.append(pull_data)

            self._git_head.commit(
                message=self._commit_manager.create_auto_commit_msg(
                    commit_type="changelog_init",
                    env_vars={"pull_request": pull_data}
                )
            )
            self._git_head.push()
            devdoc_pull = copy.copy(self._devdoc)

            for assignee in assignees:
                devdoc_pull.add_timeline_entry(
                    env_vars={
                        "event": "pull_request",
                        "action": "assigned",
                        "pull_request": pull_data,
                        "assignee": assignee,
                    },
                )
            devdoc_pull.add_reference_readthedocs(pull_nr=pull_data["number"])
            self._gh_api.pull_update(number=pull_data["number"], body=devdoc_pull.protocol)
        self._devdoc.add_pr_list(implementation_branches_info)
        return

    def _run_assignment(self, assigned: bool):
        assignee = self._user_manager.get_from_github_rest_id(self._payload.assignee.id)
        action_desc = "assigned to" if assigned else "unassigned from"
        self._reporter.event(f"Issue #{self._issue.number} {action_desc} {assignee['github']['id']}")
        self._devdoc.add_timeline_entry(env_vars={"assignee": assignee})
        self._update_protocol()
        return

    def _create_changelog_issue_entry(self):
        assignee_gh_ids = []
        if self._issue.assignee:
            assignee_gh_ids.append(self._issue.assignee.id)
        if self._issue.assignees:
            for assignee in self._issue.assignees:
                if assignee:
                    assignee_gh_ids.append(assignee.id)
        return {
            "number": self._issue.number,
            "id": self._issue.id,
            "node_id": self._issue.node_id,
            "url": self._issue.html_url,
            "created_at": self.normalize_github_date(self._issue.created_at),
            "assignees": [
                self._user_manager.get_from_github_rest_id(assignee_gh_id).changelog_entry
                for assignee_gh_id in set(assignee_gh_ids)
            ],
            "creator": self._issue_author.changelog_entry,
            "title": self._issue.title,
        }

    def _create_changelog_milestone_entry(self):
        if not self._issue.milestone:
            return
        return {
            "number": self._issue.milestone.number,
            "id": self._issue.milestone.id,
            "node_id": self._issue.milestone.node_id,
            "url": self._issue.milestone.html_url,
            "title": self._issue.milestone.title,
            "description": self._issue.milestone.description,
            "due_on": self.normalize_github_date(self._issue.milestone.due_on),
            "created_at": self.normalize_github_date(self._issue.milestone.created_at),
        }

    def _create_changelog_pull_entry(self, pull: dict):
        return {
            "number": pull["number"],
            "id": pull["id"],
            "node_id": pull["node_id"],
            "url": pull["html_url"],
            "created_at": self.normalize_github_date(pull["created_at"]),
            "creator": self._payload_sender.changelog_entry,
            "title": pull["title"],
            "internal": True,
            "base": {
                "ref": pull["base"]["ref"],
                "sha": pull["base"]["sha"],
                "url": pull["base"]["url"],
            },
            "head": {
                "ref": pull["head"]["ref"],
                "sha": pull["head"]["sha"],
                "url": pull["head"]["url"],
            },
        }

    def _update_protocol(self):
        if self._protocol_issue_nr:
            return self._gh_api.issue_update(number=self._protocol_issue_nr, body=self._devdoc.protocol)
        return self._gh_api.issue_comment_update(comment_id=self._protocol_comment_id, body=self._devdoc.protocol)

    def make_label_env_var(self, label: Label):
        return {
            "category": label.category.value,
            "name": label.name,
            "group_id": label.group_id,
            "id": label.id.value if isinstance(label.id, Enum) else label.id,
            "prefix": label.prefix,
            "suffix": label.suffix,
            "color": label.color,
            "description": label.description,
        }

    @logger.sectioner("Extract Entries from Issue Body")
    def _extract_entries_from_issue_body(self, body: str, body_elems: list[dict]):
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
        logger.debug("Issue body", mdit.element.code_block(body))
        match = re.search(compiled_pattern, body)
        if not match:
            logger.critical(
                "Issue Body Pattern Matching",
                "Could not match the issue body to pattern defined in control center settings."
            )
            raise ProManException()
        # Create a dictionary with titles as keys and matched content as values
        sections = {
            section_id: content.strip() if content else None
            for section_id, content in match.groupdict().items()
        }
        logger.debug("Matched sections", str(sections))
        return sections
