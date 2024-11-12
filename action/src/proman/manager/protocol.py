from __future__ import annotations

from typing import TYPE_CHECKING
import re

from loggerman import logger
import mdit
import pyserials as ps
from controlman.file_gen.forms import pre_process_existence

from proman.dtype import IssueStatus
from proman.exception import ProManException

if TYPE_CHECKING:
    from github_contexts.github.payload.object.user import User as GitHubUser
    from github_contexts.github.payload.object.issue import Issue
    from github_contexts.github.payload.object.pull_request import PullRequest
    from proman.manager.commit import Commit
    from proman.manager import Manager
    from proman.dstruct import IssueForm


class ProtocolManager:

    def __init__(self, manager: Manager):
        self._manager = manager
        self._protocol = ""
        self._protocol_comment_id = None
        self._protocol_issue_nr = None
        self._protocol_pull_nr = None
        return

    @property
    def protocol(self) -> str:
        return self._protocol

    @protocol.setter
    def protocol(self, value: str):
        self._protocol = value
        return

    def generate_from_issue(self, issue: Issue, issue_form: IssueForm) -> tuple[dict, str]:
        body_template = issue_form.post_process.get("body")
        issue_entries = self._extract_entries_from_issue_body(issue.body, issue_form.body)
        if body_template:
            body_processed = self._generate(
                template=body_template,
                issue_form=issue_form,
                issue_inputs=issue_entries,
                issue_body=issue.body
            )
        else:
            logger.info("Issue Post Processing", "No post-process action defined in issue form.")
            body_processed = issue.body
        self.protocol = self._generate(
            template=self._manager.data["doc.protocol.template"],
            issue_form=issue_form,
            issue_inputs=issue_entries,
            issue_body=body_processed,
        )
        return issue_entries, body_processed

    def load_from_issue(self, issue: Issue) -> str:
        if self._manager.data["doc.protocol.as_comment"]:
            comments = self._manager.gh_api_actions.issue_comments(number=issue.number, max_count=10)
            protocol_comment = comments[0]
            self._protocol_comment_id = protocol_comment.get("id")
            self.protocol = protocol_comment.get("body")
        else:
            self.protocol = issue.body
            self._protocol_issue_nr = issue.number
        return self.protocol

    def load_from_pull(self, pull: PullRequest) -> str:
        self._protocol_pull_nr = pull.number
        self.protocol = pull.body
        return self.protocol

    def update_on_github(self):
        if self._protocol_issue_nr:
            return self._manager.gh_api_actions.issue_update(
                number=self._protocol_issue_nr, body=self.protocol
            )
        if self._protocol_pull_nr:
            return self._manager.gh_api_actions.pull_update(
                number=self._protocol_pull_nr, body=self.protocol
            )
        return self._manager.gh_api_actions.issue_comment_update(
            comment_id=self._protocol_comment_id, body=self.protocol
        )

    def create_data(self, id: str, spec: dict, env_vars: dict) -> str:
        marker_start, marker_end = self.make_text_marker(id=id, data=spec)
        data_filled = self._manager.fill_jinja_template(template=spec["value"], env_vars=env_vars)
        return f"{marker_start}{data_filled}{marker_end}"

    def get_data(self, id: str, spec: dict) -> str:
        marker_start, marker_end = self.make_text_marker(id=id, data=spec)
        pattern = rf"{re.escape(marker_start)}(.*?){re.escape(marker_end)}"
        match = re.search(pattern, self.protocol, flags=re.DOTALL)
        return match.group(1) if match else ""

    def add_data(
        self,
        id: str,
        spec: dict,
        data: str,
        replace: bool = False
    ) -> str:
        marker_start, marker_end = self.make_text_marker(id=id, data=spec)
        pattern = rf"({re.escape(marker_start)})(.*?)({re.escape(marker_end)})"
        replacement = r"\1" + data + r"\3" if replace else r"\1\2" + data + r"\3"
        self.protocol = re.sub(pattern, replacement, self.protocol, flags=re.DOTALL)
        return self.protocol

    def add_timeline_entry(self, env_vars: dict | None = None) -> str:
        template = self._manager.data["doc.protocol.timeline.template"]
        if not template:
            return self.protocol
        entry = self._manager.fill_jinja_template(template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="timeline",
            spec=self._manager.data["doc.protocol.timeline"],
            data=entry,
            replace=False
        )

    def add_reference(
        self,
        ref_id: str,
        ref_title: str,
        ref_url: str,
    ) -> str:
        env_vars = {
            "ref": {
                "id": ref_id,
                "url": ref_url,
                "title": ref_title,
            }
        }
        template = self._manager.data["doc.protocol.references.template"]
        if not template:
            return self.protocol
        entry = self._manager.fill_jinja_template(template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="references",
            spec=self._manager.data["doc.protocol.references"],
            data=entry,
            replace=False
        )

    def add_reference_readthedocs(self, pull_nr: int) -> str:

        def create_readthedocs_preview_url():
            # Ref: https://github.com/readthedocs/actions/blob/v1/preview/scripts/edit-description.js
            # Build the ReadTheDocs website for pull-requests and add a link to the pull request's description.
            # Note: Enable "Preview Documentation from Pull Requests" in ReadtheDocs project at https://docs.readthedocs.io/en/latest/pull-requests.html
            # https://docs.readthedocs.io/en/latest/guides/pull-requests.html

            config = self._manager.data["tool.readthedocs.config.workflow"]
            domain = "org.readthedocs.build" if config["platform"] == "community" else "com.readthedocs.build"
            slug = config["name"]
            url = f"https://{slug}--{pull_nr}.{domain}/"
            if config["version_scheme"]["translation"]:
                language = config["language"]
                url += f"{language}/{pull_nr}/"
            return url

        if not self._manager.data["tool.readthedocs"]:
            return self.protocol
        return self.add_reference(
            ref_id="readthedocs-preview",
            ref_title="Website Preview on ReadTheDocs",
            ref_url=create_readthedocs_preview_url(),
        )

    def add_pr_list(self, pr_list: list[dict[str, str]]) -> str:
        pr_list_template = self._manager.data["doc.protocol.pr_list.template"]
        if not pr_list_template:
            return self.protocol
        env_vars = {"pulls": pr_list}
        entry = self._manager.fill_jinja_template(pr_list_template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="pr_list",
            spec=self._manager.data["doc.protocol.pr_list"],
            data=entry,
            replace=True
        )

    def update_status(self, status: IssueStatus, env_vars: dict | None = None) -> str:
        status_template = self._manager.data["doc.protocol.status.template"]
        if not status_template:
            return self.protocol
        self.add_data(
            id="status",
            spec=self._manager.data["doc.protocol.status"],
            data=self._manager.fill_jinja_template(status_template, (env_vars or {}) | {"status": status.value}),
            replace=True
        )
        checkbox_templates = self._manager.data.get("doc.protocol.status_checkbox", {})
        for status_id, checkbox_data in checkbox_templates.items():
            checkbox_level = IssueStatus(status_id).level if status_id != "deploy" else 10
            checkbox = self.get_data(id=f"status_checkbox.{status_id}", spec=checkbox_data)
            checkbox_set = self.toggle_checkbox(checkbox, check=status.level > checkbox_level)
            self.add_data(
                id=f"status_checkbox.{status_id}",
                spec=checkbox_data,
                data=checkbox_set,
                replace=True,
            )
        return self.protocol

    def get_tasklist(self) -> list[dict[str, bool | str | list]]:
        """
        Extract the implementation tasklist from the pull request body.

        Returns
        -------
        A list of dictionaries, each representing a tasklist entry.
        Each dictionary has the following keys:
        - complete : bool
            Whether the task is complete.
        - summary : str
            The summary of the task.
        - description : str
            The description of the task.
        - sublist : list[dict[str, bool | str | list]]
            A list of dictionaries, each representing a subtask entry, if any.
            Each dictionary has the same keys as the parent dictionary.
        """

        log_title = "Tasklist Extraction"

        def extract(tasklist_string: str, level: int = 0) -> list[dict[str, bool | str | list]]:
            # Regular expression pattern to match each task item
            task_pattern = rf'{" " * level * 2}- \[(X| )\] (.+?)(?=\n{" " * level * 2}- \[|\Z)'
            # Find all matches
            matches = re.findall(task_pattern, tasklist_string, flags=re.DOTALL)
            # Process each match into the required dictionary format
            tasklist_entries = []
            for match in matches:
                complete, summary_and_desc = match
                summary_and_body_split = summary_and_desc.split('\n', 1)
                summary = summary_and_body_split[0].strip()
                body = summary_and_body_split[1] if len(summary_and_body_split) > 1 else ''
                if body:
                    sublist_pattern = r'^( *- \[(?:X| )\])'
                    parts = re.split(sublist_pattern, body, maxsplit=1, flags=re.MULTILINE)
                    body = parts[0]
                    if len(parts) > 1:
                        sublist_str = ''.join(parts[1:])
                        sublist = extract(sublist_str, level + 1)
                    else:
                        sublist = []
                else:
                    sublist = []
                body = "\n".join([line.removeprefix(" " * (level + 1) * 2) for line in body.splitlines()])
                task_is_complete = complete or (
                    sublist and all([subtask['complete'] for subtask in sublist])
                )
                if level == 0:
                    conv_msg = self._manager.commit.create_from_msg(summary)
                    tasklist_entries.append({
                        'complete': task_is_complete,
                        'commit': conv_msg,
                        'body': body.rstrip(),
                        'subtasks': sublist
                    })
                else:
                    tasklist_entries.append({
                        'complete': task_is_complete,
                        'description': summary.strip(),
                        'body': body.rstrip(),
                        'subtasks': sublist
                    })
            return tasklist_entries

        tasklist = self.get_data(id="tasklist", spec=self._manager.data["doc.protocol.tasklist"]).strip()
        body_md = mdit.element.code_block(self.protocol, language="markdown", caption="Protocol")
        if not tasklist:
            logger.warning(
                log_title,
                "No tasklist found in the protocol.",
                body_md,
            )
            return []
        tasklist = extract(tasklist)
        logger.success(
            log_title,
            "Extracted tasklist from the document.",
            mdit.element.code_block(ps.write.to_yaml_string(tasklist), language="yaml", caption="Tasklist"),
            body_md,
        )
        return tasklist

    def get_pr_title(self):
        return self.get_data(id="pr_title", spec=self._manager.data["doc.protocol.pr_title"]).strip()

    def make_text_marker(self, id: str, data: dict) -> tuple[str, str]:
        return tuple(
            data[pos] if pos in data else self._manager.data["doc.protocol.marker"][pos].format(id)
            for pos in ("start", "end")
        )

    def update_tasklist(self, entries: list[dict[str, bool | Commit | list]]) -> str:
        """Write an implementation tasklist as Markdown string
        and update it in the protocol.

        Parameters
        ----------
        entries
            A list of dictionaries, each representing a tasklist entry.
            The format of each dictionary is the same as that returned by
            `_extract_tasklist_entries`.
        """
        string = []

        def write(entry_list, level=0):
            for entry in entry_list:
                check = 'X' if entry['complete'] else ' '
                if level == 0:
                    summary = entry["commit"].summary
                else:
                    summary = entry['description']
                string.append(f"{' ' * level * 2}- [{check}] {summary.strip()}")
                if entry["body"]:
                    for line in entry["body"].splitlines():
                        string.append(f"{' ' * (level + 1) * 2}{line}")
                write(entry['subtasks'], level + 1)

        write(entries)
        tasklist = "\n".join(string).strip()
        return self.add_data(
            id="tasklist",
            spec=self._manager.data["doc.protocol.tasklist"],
            data=f"\n{tasklist}\n",
            replace=True
        )

    def _generate(self, template: str, issue_form: IssueForm, issue_inputs: dict, issue_body: str) -> str:
        data = {}
        env_vars = {
            "data": data,
            "form": issue_form,
            "input": issue_inputs,
            "issue_body": issue_body,
        }
        for template_key in ("tasklist", "timeline", "references", "pr_list", "pr_title", "status"):
            template_data = self._manager.data.get(f"doc.protocol.{template_key}")
            if template_data:
                env_vars[template_key] = self.create_data(id=template_key, spec=template_data, env_vars=env_vars)
        status_checkbox = self._manager.data["doc.protocol.status_checkbox"]
        if status_checkbox:
            env_vars["status_checkbox"] = {}
            for status_id, status_checkbox_data in status_checkbox.items():
                env_vars["status_checkbox"][status_id] = self.create_data(
                    id=f"status_checkbox.{status_id}", spec=status_checkbox_data, env_vars=env_vars
                )
        for data_id, data_value in self._manager.data["doc.protocol.data"].items():
            data[data_id] = self.create_data(id=data_id, spec=data_value, env_vars=env_vars)
        protocol = self._manager.fill_jinja_template(
            template=template,
            env_vars=env_vars,
        )
        return protocol

    @staticmethod
    def toggle_checkbox(checkbox: str, check: bool) -> str:
        """Toggle the checkbox in a markdown tasklist entry."""

        def replacer(match):
            checkmark = "X" if check else " "
            return f"{match.group(1)}{checkmark}{match.group(3)}"

        pattern = re.compile(r"(^[\s\n]*-\s*\[)([ ]|X)(]\s*)", re.MULTILINE)
        matches = re.findall(pattern, checkbox)
        if len(matches) == 0 or len(matches) > 1:
            logger.warning(
                "Checkbox Toggle",
                f"Found {len(matches)} checkboxes in the input string:",
                mdit.element.code_block(checkbox, language="markdown", caption="Input String"),
            )
            return checkbox
        return re.sub(pattern, replacer, checkbox, count=1)

    @staticmethod
    def _extract_entries_from_issue_body(body: str, body_elems: list[dict]):
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
