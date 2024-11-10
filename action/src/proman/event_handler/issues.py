from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from enum import Enum
import copy

from github_contexts import github as gh_context
from loggerman import logger
import mdit

from proman.dtype import LabelType, IssueStatus
from proman.main import EventHandler
from proman.manager.changelog import ChangelogManager

if _TYPE_CHECKING:
    from proman.dstruct import Label
    from proman.manager.user import User


class IssuesEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.payload: gh_context.payload.IssuesPayload = self.gh_context.event
        self.issue = self.payload.issue
        self.issue_author = self.manager.user.from_issue_author(self.issue)
        issue = copy.deepcopy(self.issue.as_dict)
        issue["user"] = self.issue_author
        self.jinja_env_vars["issue"] = issue

        self._label_groups: dict[LabelType, list[Label]] = {}
        self._protocol_comment_id: int | None = None
        self._protocol_issue_nr: int | None = None
        return

    @logger.sectioner("Issues Handler Execution")
    def run(self):
        action = self.payload.action
        if action == gh_context.enum.ActionType.OPENED:
            return self._run_opened()
        self.manager.protocol.load_from_issue(self.issue)
        if action == gh_context.enum.ActionType.LABELED:
            return self._run_labeled()
        if action == gh_context.enum.ActionType.ASSIGNED:
            return self._run_assignment(assigned=True)
        if action == gh_context.enum.ActionType.UNASSIGNED:
            return self._run_assignment(assigned=False)
        self.manager.protocol.add_timeline_entry()
        self.manager.protocol.update_on_github()
        return

    def _run_opened(self):

        def assign() -> list[User]:
            users = issue_form.issue_assignees.copy()
            assignment = issue_form.post_process.get("assign_creator")
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
                        for user in users:
                            if user["github"]["id"] == self.issue_author["github"]["id"]:
                                break
                        else:
                            users.append(self.issue_author)
            self._gh_api.issue_add_assignees(
                number=self.issue.number, assignees=[user["github"]["id"] for user in users]
            )
            return users

        def add_labels() -> list[Label]:
            label_objs = [
                self.manager.label.status_label(IssueStatus.TRIAGE)
            ] + issue_form.id_labels + issue_form.labels
            if "version" in issue_entries:
                versions = [version.strip() for version in issue_entries["version"].split(",")]
                for version in versions:
                    label_objs.append(self.manager.label.label_version(version))
                    branch = self.manager.branch.from_version(version)
                    label_objs.append(self.manager.label.label_branch(branch.name))
            elif "branch" in issue_entries:
                branches = [branch.strip() for branch in issue_entries["branch"].split(",")]
                for branch in branches:
                    label_objs.append(self.manager.label.label_branch(branch))
            else:
                logger.info(
                    "Issue Label Update",
                    "Could not match branch or version in issue body to pattern defined in metadata.",
                )
            gh_response = self._gh_api.issue_labels_set(
                self.issue.number,
                [label_obj.name for label_obj in set(label_objs)]
            )
            logger.info(
                "Issue Labels Update",
                logger.pretty(gh_response)
            )
            return label_objs

        self.reporter.event(f"Issue #{self.issue.number} opened")
        issue_form = self.manager.issue.form_from_issue_body(self.issue.body)
        issue_entries, body_processed = self.manager.protocol.generate_from_issue(issue=self.issue, issue_form=issue_form)
        self.manager.protocol.add_timeline_entry()
        self.manager.protocol.update_status(IssueStatus.TRIAGE)

        labels = add_labels()
        assignees = assign()

        for assignee in assignees:
            self.manager.protocol.add_timeline_entry(
                env_vars={
                    "action": "assigned",
                    "assignee": assignee,
                },
            )
        for label in labels:
            self.manager.protocol.add_timeline_entry(
                env_vars={"action": "labeled", "label": self.make_label_env_var(label)}
            )
        logger.info(
            "Development Protocol",
            mdit.element.code_block(self.manager.protocol.protocol)
        )
        if self.manager.data["doc.protocol.as_comment"]:
            response = self._gh_api.issue_update(number=self.issue.number, body=body_processed)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
            response = self._gh_api.issue_comment_create(number=self.issue.number, body=self.manager.protocol.protocol)
            logger.info(
                "Dev Protocol Comment",
                logger.pretty(response)
            )
        else:
            response = self._gh_api.issue_update(number=self.issue.number, body=self.manager.protocol.protocol)
            logger.info(
                "Issue Body Update",
                logger.pretty(response)
            )
        return

    def _run_labeled(self):
        label = self.manager.label.resolve_label(self.payload.label.name)
        self.manager.protocol.add_timeline_entry(env_vars={"label": self.make_label_env_var(label)})
        if label.category is not LabelType.STATUS:
            self.reporter.event(f"Issue #{self.issue.number} labeled `{label.name}`")
            self.manager.protocol.update_on_github()
            return
        self.manager.protocol.update_status(label.id)
        # Remove all other status labels
        self._label_groups = self.manager.label.resolve_labels(self.issue.label_names)
        self.manager.label.update_status_label_on_github(
            issue_nr=self.issue.number,
            old_status_labels=self._label_groups[LabelType.STATUS],
            new_status_label=label,
        )
        if label.id in [IssueStatus.REJECTED, IssueStatus.DUPLICATE, IssueStatus.INVALID]:
            self._gh_api.issue_update(number=self.issue.number, state="closed", state_reason="not_planned")
        elif label.id is IssueStatus.IMPLEMENTATION:
            self._run_labeled_status_implementation()
        self.manager.protocol.update_on_github()
        return

    def _run_labeled_status_implementation(self):

        def assign_pr() -> None:
            if issue_form.pull_assignees:
                response = self._gh_api.issue_add_assignees(
                    number=pull_data["number"],
                    assignees=[user["github"]["id"] for user in issue_form.pull_assignees]
                )
                logger.info(
                    "Pull Request Assignment",
                    logger.pretty(response)
                )
            else:
                logger.info("Pull Request Assignment", "No assignees found for pull request.")
            return

        def get_base_branches() -> dict[str, list[Label]]:
            base_branches_and_labels = {}
            common_labels = []
            for label_group, group_labels in self._label_groups.items():
                if label_group not in [LabelType.BRANCH, LabelType.VERSION]:
                    common_labels.extend(group_labels)
            if self._label_groups.get(LabelType.VERSION):
                for version_label in self._label_groups[LabelType.VERSION]:
                    branch_label = self.manager.label.label_version_to_branch(version_label)
                    all_labels_for_branch = common_labels + [version_label, branch_label]
                    base_branches_and_labels[branch_label.suffix] = all_labels_for_branch
            else:
                for branch_label in self._label_groups[LabelType.BRANCH]:
                    base_branches_and_labels[branch_label.suffix] = common_labels + [branch_label.name]
            return base_branches_and_labels

        issue_form = self.manager.issue.form_from_id_labels(self.issue.label_names)
        branch_sha = {branch["name"]: branch["commit"]["sha"] for branch in self._gh_api.branches}
        implementation_branches_info = []
        for base_branch_name, labels in get_base_branches().items():
            head_branch = self.manager.branch.new_dev(
                issue_nr=self.issue.number, target=base_branch_name
            )
            new_branch = self._gh_api_admin.branch_create_linked(
                issue_id=self.issue.node_id,
                base_sha=branch_sha[base_branch_name],
                name=head_branch.name,
            )

            self.manager.git.fetch_remote_branches_by_name(branch_names=head_branch.name)
            self.manager.git.checkout(head_branch.name)

            # Create changelog entry
            changelog_entry = {
                "id": issue_form.id,
                "issue": self._create_changelog_issue_entry(),
            }
            if self.issue.milestone:
                changelog_entry["milestone"] = self._create_changelog_milestone_entry()
            self.manager.changelog.update_current(changelog_entry)
            # Write initial changelog to create a commit on dev branch to be able to open a draft pull request
            # Ref: https://stackoverflow.com/questions/46577500/why-cant-i-create-an-empty-pull-request-for-discussion-prior-to-developing-chan
            self.manager.changelog.write()

            branch_data = {
                "head": {
                    "name": head_branch.name,
                    "url": self._gh_link.branch(head_branch.name).homepage,
                },
                "base": {
                    "name": base_branch_name,
                    "sha": branch_sha[base_branch_name],
                    "url": self._gh_link.branch(base_branch_name).homepage,
                },
            }
            self.manager.git.commit(
                message=self.manager.commit.create_auto(
                    id="dev_branch_creation",
                    env_vars=branch_data,
                )
            )
            self.manager.git.push(target="origin", set_upstream=True)
            pull_data = self._gh_api.pull_create(
                head=new_branch["name"],
                base=base_branch_name,
                title=self.manager.protocol.get_pr_title() or self.issue.title,
                body=self.manager.protocol.protocol,
                maintainer_can_modify=True,
                draft=True,
            )
            logger.info(
                f"Pull Request Creation ({new_branch['name']} -> {base_branch_name})",
                logger.pretty(pull_data)
            )
            label_data = self._gh_api.issue_labels_set(
                number=pull_data["number"],
                labels=[label.name for label in labels]
            )
            logger.info(
                f"Pull Request Labels Update ({new_branch['name']} -> {base_branch_name})",
                logger.pretty(label_data)
            )
            assign_pr()

            pull_data["user"] = self.manager.user.get_from_github_rest_id(pull_data["user"]["id"])
            pull_data["head"] = branch_data["head"] | pull_data["head"]
            pull_data["base"] = branch_data["base"] | pull_data["base"]
            self.manager.protocol.add_timeline_entry(
                env_vars={
                    "event": "pull_request",
                    "action": "opened",
                    "pull_request": pull_data,
                },
            )

            # Update changelog entry with pull request information
            self.manager.changelog.update_current({"pull_request": self._create_changelog_pull_entry(pull_data)})
            self.manager.changelog.write()

            implementation_branches_info.append(pull_data)

            self.manager.git.commit(
                message=self.manager.commit.create_auto(
                    id="changelog_init",
                    env_vars={"pull_request": pull_data}
                )
            )
            self.manager.git.push()
            devdoc_pull = copy.copy(self.manager.protocol)

            for assignee in issue_form.pull_assignees:
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
        self.manager.protocol.add_pr_list(implementation_branches_info)
        return

    def _run_assignment(self, assigned: bool):
        assignee = self.manager.user.get_from_github_rest_id(self.payload.assignee.id)
        action_desc = "assigned to" if assigned else "unassigned from"
        self.reporter.event(f"Issue #{self.issue.number} {action_desc} {assignee['github']['id']}")
        self.manager.protocol.add_timeline_entry(env_vars={"assignee": assignee})
        self.manager.protocol.update_on_github()
        return

    def _create_changelog_issue_entry(self):
        assignee_gh_ids = []
        if self.issue.assignee:
            assignee_gh_ids.append(self.issue.assignee.id)
        if self.issue.assignees:
            for assignee in self.issue.assignees:
                if assignee:
                    assignee_gh_ids.append(assignee.id)
        return {
            "number": self.issue.number,
            "id": self.issue.id,
            "node_id": self.issue.node_id,
            "url": self.issue.html_url,
            "created_at": self.normalize_github_date(self.issue.created_at),
            "assignees": [
                self.manager.user.get_from_github_rest_id(assignee_gh_id).changelog_entry
                for assignee_gh_id in set(assignee_gh_ids)
            ],
            "creator": self.issue_author.changelog_entry,
            "title": self.issue.title,
        }

    def _create_changelog_milestone_entry(self):
        if not self.issue.milestone:
            return
        return {
            "number": self.issue.milestone.number,
            "id": self.issue.milestone.id,
            "node_id": self.issue.milestone.node_id,
            "url": self.issue.milestone.html_url,
            "title": self.issue.milestone.title,
            "description": self.issue.milestone.description,
            "due_on": self.normalize_github_date(self.issue.milestone.due_on),
            "created_at": self.normalize_github_date(self.issue.milestone.created_at),
        }

    def _create_changelog_pull_entry(self, pull: dict):
        return {
            "number": pull["number"],
            "id": pull["id"],
            "node_id": pull["node_id"],
            "url": pull["html_url"],
            "created_at": self.normalize_github_date(pull["created_at"]),
            "creator": self.payload_sender.changelog_entry,
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

