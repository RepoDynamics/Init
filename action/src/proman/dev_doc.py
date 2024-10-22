from __future__ import annotations

from typing import TYPE_CHECKING
import re
import datetime

import jinja2
from loggerman import logger
import mdit
import pyserials as ps


if TYPE_CHECKING:
    from github_contexts.github.payload.object.user import User as GitHubUser
    from proman.datatype import IssueStatus

class DevDoc:

    def __init__(
        self,
        data_main: ps.NestedDict,
        github_context: dict,
        event_payload: dict,
        env_vars: dict | None = None,
        protocol: str | None = None,
    ):
        self._data_main = data_main
        self.env_vars = {
            "event": github_context["event_name"],
            "action": event_payload.get("action", ""),
            "ccc": self._data_main,
            "context": github_context,
            "payload": event_payload,
            "actor": event_payload["sender"],
        } | (env_vars or {})
        self.protocol = protocol or ""
        return

    def generate(self, template: str, issue_form: dict, issue_inputs: dict, issue_body: str) -> str:
        data = {}
        env_vars = {
            "data": data,
            "form": issue_form,
            "input": issue_inputs,
            "issue_body": issue_body,
        }
        for template_key in ("tasklist", "timeline", "references", "pr_list", "status"):
            template_data = self._data_main.get(f"doc.protocol.{template_key}")
            if template_data:
                env_vars[template_key] = self.create_data(id=template_key, spec=template_data, env_vars=env_vars)
        status_checkbox = self._data_main["doc.protocol.status_checkbox"]
        if status_checkbox:
            env_vars["status_checkbox"] = {}
            for status_id, status_checkbox_data in status_checkbox.items():
                env_vars["status_checkbox"][status_id] = self.create_data(
                    id=f"status_checkbox.{status_id}", spec=status_checkbox_data, env_vars=env_vars
                )
        for data_id, data_value in self._data_main["doc.protocol.data"].items():
            data[data_id] = self.create_data(id=data_id, spec=data_value, env_vars=env_vars)
        self.protocol = self.fill_jinja_template(
            template=template,
            env_vars=env_vars,
        )
        return self.protocol

    def create_data(self, id: str, spec: dict, env_vars: dict) -> str:
        marker_start, marker_end = self.make_text_marker(id=id, data=spec)
        data_filled = self.fill_jinja_template(template=spec["value"], env_vars=env_vars)
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
        template = self._data_main["doc.protocol.timeline.template"]
        if not template:
            return self.protocol
        entry = self.fill_jinja_template(template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="timeline",
            spec=self._data_main["doc.protocol.timeline"],
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
        template = self._data_main["doc.protocol.references.template"]
        if not template:
            return self.protocol
        entry = self.fill_jinja_template(template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="references",
            spec=self._data_main["doc.protocol.references"],
            data=entry,
            replace=False
        )

    def add_pr_list(self, pr_list: list[dict[str, str]]) -> str:
        pr_list_template = self._data_main["doc.protocol.pr_list.template"]
        if not pr_list_template:
            return self.protocol
        env_vars = {"pulls": pr_list}
        entry = self.fill_jinja_template(pr_list_template, env_vars)
        if not entry.strip():
            return self.protocol
        return self.add_data(
            id="pr_list",
            spec=self._data_main["doc.protocol.pr_list"],
            data=entry,
            replace=True
        )

    def update_status(self, status: IssueStatus, env_vars: dict | None) -> str:
        status_template = self._data_main["doc.protocol.status.template"]
        if not status_template:
            return self.protocol
        self.add_data(
            id="status",
            spec=self._data_main["doc.protocol.status"],
            data=self.fill_jinja_template(status_template, env_vars | {"status": status.value}),
            replace=True
        )
        checkbox_templates = self._data_main.get("doc.protocol.status_checkbox", {})
        for status_id, checkbox_data in checkbox_templates.items():
            checkbox_level = IssueStatus(status_id).level if status_id != "deploy" else 10
            checkbox = self.get_data(id=f"status_checkbox.{status_id}", spec=checkbox_data)
            checkbox_set = self.toggle_checkbox(checkbox, check=status.level >= checkbox_level)
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
                summary_and_desc_split = summary_and_desc.split('\n', 1)
                summary = summary_and_desc_split[0]
                description = summary_and_desc_split[1] if len(summary_and_desc_split) > 1 else ''
                if description:
                    sublist_pattern = r'^( *- \[(?:X| )\])'
                    parts = re.split(sublist_pattern, description, maxsplit=1, flags=re.MULTILINE)
                    description = parts[0]
                    if len(parts) > 1:
                        sublist_str = ''.join(parts[1:])
                        sublist = extract(sublist_str, level + 1)
                    else:
                        sublist = []
                else:
                    sublist = []
                tasklist_entries.append({
                    'complete': complete == 'X',
                    'summary': summary.strip(),
                    'description': description.rstrip(),
                    'sublist': sublist
                })
            return tasklist_entries

        tasklist = self.get_data(id="tasklist", spec=self._data_main["doc.protocol.tasklist"]).strip()
        body_md = mdit.element.code_block(self.protocol, language="markdown", caption="Protocol")
        if not tasklist:
            logger.warning(
                log_title,
                "No tasklist found in the protocol.",
                body_md,
            )
            return []
        logger.success(
            log_title,
            "Extracted tasklist from the document.",
            mdit.element.code_block(ps.write.to_yaml_string(tasklist), language="yaml", caption="Tasklist"),
            body_md,
        )
        return tasklist

    def get_pr_title(self):
        return self.get_data(id="pr_title", spec=self._data_main["doc.protocol.pr_title"]).strip()

    def make_text_marker(self, id: str, data: dict) -> tuple[str, str]:
        return tuple(
            data[pos] if pos in data else self._data_main["doc.protocol.marker"][pos].format(id)
            for pos in ("start", "end")
        )

    def fill_jinja_template(self, template: str, env_vars: dict | None = None) -> str:
        return jinja2.Template(template).render(
            self.env_vars | {"now": datetime.datetime.now(tz=datetime.UTC)} | (env_vars or {})
        )

    def update_tasklist(self, entries: list[dict[str, bool | str | list]]) -> str:
        """Write an implementation tasklist as Markdown string
        and update it in the protocol.

        Parameters
        ----------
        entries : list[dict[str, bool | str | list]]
            A list of dictionaries, each representing a tasklist entry.
            The format of each dictionary is the same as that returned by
            `_extract_tasklist_entries`.
        """
        string = []

        def write(entry_list, level=0):
            for entry in entry_list:
                check = 'X' if entry['complete'] else ' '
                string.append(f"{' ' * level * 2}- [{check}] {entry['summary']}")
                if entry["description"]:
                    for line in entry["description"].splitlines():
                        string.append(f"{' ' * (level + 1) * 2}{line}".strip())
                write(entry['sublist'], level + 1)

        write(entries)
        tasklist = "\n".join(string).strip()
        return self.add_data(
            id="tasklist",
            spec=self._data_main["doc.protocol.tasklist"],
            data=f"\n{tasklist}\n",
            replace=True
        )

    @staticmethod
    def make_user_mention(user: GitHubUser) -> str:
        linked_username = f"[{user.login}]({user.html_url})"
        if not user.name:
            return linked_username
        return f"{user.name} ({linked_username})"

    @staticmethod
    def toggle_checkbox(checkbox: str, check: bool) -> str:
        """Toggle the checkbox in a markdown tasklist entry."""

        def replacer(match):
            checkmark = "X" if check else " "
            return f"{match.group(1)}{checkmark}{match.group(3)}"

        pattern = re.compile(r"(^[\s\n]*-\s*\[)([ ]|X)(\]\s*)", re.MULTILINE)
        matches = re.findall(pattern, checkbox, flags=re.MULTILINE)
        if len(matches) == 0 or len(matches) > 1:
            logger.warning(
                "Checkbox Toggle",
                f"Found {len(matches)} checkboxes in the input string:",
                mdit.element.code_block(checkbox, language="markdown", caption="Input String"),
            )
            return checkbox
        return re.sub(pattern, replacer, checkbox, count=1, flags=re.MULTILINE)
