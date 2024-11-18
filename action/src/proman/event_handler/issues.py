from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

import copy

from github_contexts import github as gh_context
from loggerman import logger
import mdit

from proman.dtype import LabelType, IssueStatus
from proman.main import EventHandler

if _TYPE_CHECKING:
    from proman.dstruct import Label, Branch, Version


class IssuesEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.payload: gh_context.payload.IssuesPayload = self.gh_context.event
        self.issue = self.payload.issue
        issue = self.manager.add_issue_jinja_env_var(self.issue)
        self.issue_author = issue["user"]

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

        def assign() -> None:
            assignees = issue_form.issue_assignees.copy()
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
                        for user in assignees:
                            if user["github"]["id"] == self.issue_author["github"]["id"]:
                                break
                        else:
                            assignees.append(self.issue_author)
            self._gh_api.issue_add_assignees(
                number=self.issue.number, assignees=[user["github"]["id"] for user in assignees]
            )
            for assignee in assignees:
                self.manager.protocol.add_timeline_entry(
                    env_vars={
                        "action": "assigned",
                        "assignee": assignee,
                    },
                )
            return

        def add_labels() -> None:
            labels = [
                self.manager.label.status_label(IssueStatus.TRIAGE)
            ] + issue_form.id_labels + issue_form.labels
            if "version" in issue_entries:
                versions = [version.strip() for version in issue_entries["version"].split(",")]
                for version in versions:
                    labels.append(self.manager.label.label_version(version))
                    branch = self.manager.branch.from_version(version)
                    labels.append(self.manager.label.label_branch(branch.name))
            elif "branch" in issue_entries:
                branches = [branch.strip() for branch in issue_entries["branch"].split(",")]
                for branch in branches:
                    labels.append(self.manager.label.label_branch(branch))
            else:
                logger.info(
                    "Issue Label Update",
                    "Could not match branch or version in issue body to pattern defined in metadata.",
                )
            gh_response = self._gh_api.issue_labels_set(
                self.issue.number,
                [label_obj.name for label_obj in set(labels)]
            )
            logger.info(
                "Issue Labels Update",
                self.reporter.api_response_code_block(gh_response)
            )
            for label in labels:
                self.manager.protocol.add_timeline_entry(
                    env_vars={"action": "labeled", "label": label}
                )
            return

        self.reporter.event(f"Issue #{self.issue.number} opened")
        issue_form = self.manager.issue.form_from_issue_body(self.issue.body)
        issue_entries, body_processed = self.manager.protocol.generate_from_issue(
            issue=self.issue, issue_form=issue_form
        )
        self.manager.protocol.add_timeline_entry()
        self.manager.protocol.update_status(IssueStatus.TRIAGE)
        add_labels()
        assign()
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
        self.manager.protocol.add_timeline_entry(env_vars={"label": label})
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

        def get_base_branches() -> list[tuple[Branch, list[Label]]]:
            base_branches_and_labels = []
            common_labels = []
            for label_group, group_labels in self._label_groups.items():
                if label_group not in [LabelType.BRANCH, LabelType.VERSION]:
                    common_labels.extend(group_labels)
            if self._label_groups.get(LabelType.VERSION):
                for version_label in self._label_groups[LabelType.VERSION]:
                    branch_label = self.manager.label.label_version_to_branch(version_label)
                    branch = self.manager.branch.from_name(branch_label.suffix)
                    all_labels_for_branch = common_labels + [version_label, branch_label]
                    base_branches_and_labels.append((branch, all_labels_for_branch))
            else:
                for branch_label in self._label_groups[LabelType.BRANCH]:
                    branch = self.manager.branch.from_name(branch_label.suffix)
                    base_branches_and_labels.append((branch, common_labels + [branch_label]))
            return base_branches_and_labels

        def create_head_branch(base: Branch) -> Branch:
            head = self.manager.branch.new_dev(
                issue_nr=self.issue.number, target=base.name
            )
            api_response = self._gh_api_admin.branch_create_linked(
                issue_id=self.issue.node_id,
                base_sha=base.sha,
                name=head.name,
            )
            logger.success(
                "Head Branch Creation",
                self.reporter.api_response_code_block(api_response)
            )
            self._git_base.fetch_remote_branches_by_name(branch_names=head.name)
            self._git_base.checkout(head.name)
            # Write initial commit on dev branch to be able to open a draft pull request
            # Ref: https://stackoverflow.com/questions/46577500/why-cant-i-create-an-empty-pull-request-for-discussion-prior-to-developing-chan
            self._git_base.commit(message="[skip actions]", allow_empty=True)
            self._git_base.push(target="origin", set_upstream=True)
            return head

        def create_pull(head: Branch, base: Branch, labels: list[Label]) -> dict:
            api_response_pull = self._gh_api.pull_create(
                head=head.name,
                base=base.name,
                title=self.manager.protocol.get_pr_title() or self.issue.title,
                body=self.manager.protocol.protocol,
                maintainer_can_modify=True,
                draft=True,
            )
            logger.success(
                f"Pull Request Creation ({head.name} -> {base.name})",
                self.reporter.api_response_code_block(api_response_pull)
            )
            api_response_labels = self._gh_api.issue_labels_set(
                number=api_response_pull["number"],
                labels=[label.name for label in labels]
            )
            logger.success(
                f"Pull Request Labels Update ({head.name} -> {base.name})",
                self.reporter.api_response_code_block(api_response_labels)
            )
            if issue_form.pull_assignees:
                api_response_assignment = self._gh_api.issue_add_assignees(
                    number=api_response_pull["number"],
                    assignees=[user["github"]["id"] for user in issue_form.pull_assignees]
                )
                logger.info(
                    "Pull Request Assignment",
                    self.reporter.api_response_code_block(api_response_assignment)
                )
            else:
                logger.info("Pull Request Assignment", "No assignees found for pull request.")
            pull = self.manager.add_pull_request_jinja_env_var(
                pull=api_response_pull,
                author=self.payload_sender,
            )
            self.manager.protocol.add_timeline_entry(
                env_vars={"event": "pull_request", "action": "opened"},
            )
            devdoc_pull = copy.copy(base_protocol)
            devdoc_pull.add_reference_readthedocs(pull_nr=pull["number"])
            for assignee in issue_form.pull_assignees:
                devdoc_pull.add_timeline_entry(
                    env_vars={
                        "event": "pull_request",
                        "action": "assigned",
                        "pull_request": pull,
                        "assignee": assignee,
                    },
                )
            self._gh_api.pull_update(number=pull["number"], body=devdoc_pull.protocol)
            return pull

        issue_form = self.manager.issue.form_from_id_labels(self.issue.label_names)
        implementation_branches_info = []
        base_protocol = copy.copy(self.manager.protocol)
        for base_branch, labels in get_base_branches():
            head_branch = create_head_branch(base=base_branch)
            pull = create_pull(head=head_branch, base=base_branch, labels=labels)
            implementation_branches_info.append(pull)
            head_manager = self.manager_from_metadata_file(repo="base") if (
                base_branch.name != self.payload.repository.default_branch
            ) else self.manager
            base_version = head_manager.release.latest_version()
            head_manager.changelog.initialize_from_issue(
                issue_form=issue_form,
                issue=self.issue,
                labels=labels,
                pull=pull,
                protocol=self.manager.protocol,
                base_version=base_version,
            )
            if issue_form.commit.action:
                next_dev_version_tag = head_manager.release.calculate_next_dev_version(
                    version_base=base_version.public,
                    issue_num=self.issue.number,
                    action=issue_form.commit.action,
                )
                self.manager.release.github.get_or_make_draft(tag=next_dev_version_tag)
                self.manager.release.zenodo.get_or_make_drafts()
            head_manager.changelog.write_file()
            head_manager.user.write_contributors()
            self._git_base.commit(
                message=str(
                    self.manager.commit.create_auto(
                        id="dev_branch_creation",
                        env_vars={"head": head_branch, "base": base_branch, "pull_request": pull},
                    )
                ),
                amend=True
            )
            self._git_base.push(force_with_lease=True)
        self.manager.protocol.add_pr_list(implementation_branches_info)
        return

    def _run_assignment(self, assigned: bool):
        assignee = self.manager.user.get_from_github_rest_id(self.payload.assignee.id)
        action_desc = "assigned to" if assigned else "unassigned from"
        self.reporter.event(f"Issue #{self.issue.number} {action_desc} {assignee['github']['id']}")
        self.manager.protocol.add_timeline_entry(env_vars={"assignee": assignee})
        self.manager.protocol.update_on_github()
        return
