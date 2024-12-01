from __future__ import annotations

from typing import TYPE_CHECKING
import re

from loggerman import logger
import mdit
import pyserials as ps

from proman.dtype import IssueStatus
from proman.dstruct import Tasklist, MainTasklistEntry, SubTasklistEntry
from proman.exception import ProManException

if TYPE_CHECKING:
    from typing import Sequence
    from github_contexts.github.payload.object.issue import Issue
    from github_contexts.github.payload.object.pull_request import PullRequest
    from proman.manager import Manager
    from proman.dstruct import IssueForm


class ProtocolManager:

    def __init__(self, manager: Manager):
        self._manager = manager
        self._protocol = ""
        self._protocol_comment_id = None
        self._protocol_issue_nr = None
        self._protocol_pull_nr = None
        self._config: dict = {}
        self._protocol_data: dict[str, str] = {}
        self._protocol_config: dict = {}
        self._issue_inputs: dict = {}
        self._env_vars: dict = {}
        return

    @property
    def protocol(self) -> str:
        return self._protocol

    @protocol.setter
    def protocol(self, value: str):
        self._protocol = value
        return

    def initialize_issue(self, issue: Issue, issue_form: IssueForm) -> tuple[dict, str]:
        self._config = self._manager.data["issue.protocol"]
        self._issue_inputs = self._extract_issue_ticket_inputs(issue.body, issue_form.body)


        self._env_vars = {
            "form": issue_form,
            "input": self._issue_inputs,
        }
        body_template = issue_form.post_process.get("body")
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

    def _generate(self, template: str, issue_form: IssueForm, issue_inputs: dict, issue_body: str) -> str:
        data = {}
        env_vars = {
            "data": data,
            "form": issue_form,
            "input": issue_inputs,
            "issue_body": issue_body,
        }
        for template_key in ("tasklist", "timeline", "references", "pr_list", "pr_title"):
            template_data = self._manager.data.get(f"doc.protocol.{template_key}")
            if template_data:
                env_vars[template_key] = self.create_data(id=template_key, spec=template_data, env_vars=env_vars)
        for data_id, data_value in self._manager.data["doc.protocol.data"].items():
            data[data_id] = self.create_data(id=data_id, spec=data_value, env_vars=env_vars)
        protocol = self._manager.fill_jinja_template(
            template=template,
            env_vars=env_vars,
        )
        return protocol

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

    def _generate_output(self) -> str:

        def make_config():
            config_str = ps.write.to_yaml_string(self._protocol_config).strip()
            marker_start, marker_end = self._make_text_marker(id="config")
            return f"{marker_start}\n{config_str}\n{marker_end}"

        def make_inputs():
            if not self._issue_inputs:
                return ""
            inputs = ps.write.to_yaml_string(self._issue_inputs).strip()
            marker_start, marker_end = self._make_text_marker(id="input")
            return f"\n\n{marker_start}\n{inputs}\n{marker_end}"

        output = self._manager.fill_jinja_templates(
            templates=self._config["template"],
            env_vars=self._env_vars,
        )
        if not isinstance(output, str):
            output = mdit.generate(output).source(target="github")
        output = f"{output.strip()}\n\n{make_config()}{make_inputs()}"
        return output

    def create_data(self, id: str, spec: dict, env_vars: dict) -> str:
        marker_start, marker_end = self._make_text_marker(id=id, data=spec)
        data_filled = self._manager.fill_jinja_template(template=spec["value"], env_vars=env_vars)
        return f"{marker_start}{data_filled}{marker_end}"

    def get_data(self, id: str, spec: dict) -> str:
        marker_start, marker_end = self._make_text_marker(id=id, data=spec)
        pattern = rf"{re.escape(marker_start)}(.*?){re.escape(marker_end)}"
        match = re.search(pattern, self.protocol, flags=re.DOTALL)
        return match.group(1) if match else ""

    def get_all_data(self) -> dict[str, str]:
        return {
            data_id: self.get_data(id=data_id, spec=data_config)
            for data_id, data_config in self._manager.data.get("doc.protocol.data", {}).items()
        }

    def add_data(
        self,
        id: str,
        spec: dict,
        data: str,
        replace: bool = False
    ) -> str:
        marker_start, marker_end = self._make_text_marker(id=id, data=spec)
        pattern = rf"({re.escape(marker_start)})(.*?)({re.escape(marker_end)})"
        replacement = r"\1" + data + r"\3" if replace else r"\1\2" + data + r"\3"
        self.protocol = re.sub(pattern, replacement, self.protocol, flags=re.DOTALL)
        return self.protocol

    def get_tasklist(self) -> Tasklist | None:
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

        def extract(tasklist_string: str, level: int = 0) -> list[MainTasklistEntry] | list[SubTasklistEntry]:
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
                    sublist and all(subtask.complete for subtask in sublist)
                )
                if level == 0:
                    conv_msg = self._manager.commit.create_from_msg(summary)
                    tasklist_entries.append(
                        MainTasklistEntry(
                            commit=conv_msg,
                            body=body.rstrip(),
                            complete=task_is_complete,
                            subtasks=tuple(sublist),
                        )
                    )
                else:
                    tasklist_entries.append(
                        SubTasklistEntry(
                            description=summary.strip(),
                            body=body.rstrip(),
                            complete=task_is_complete,
                            subtasks=tuple(sublist),
                        )
                    )
            return tasklist_entries

        tasklist_str = self.get_data(id="tasklist", spec=self._manager.data["doc.protocol.tasklist"]).strip()
        body_md = mdit.element.code_block(self.protocol, language="markdown", caption="Protocol")
        if not tasklist_str:
            logger.warning(
                log_title,
                "No tasklist found in the protocol.",
                body_md,
            )
            return
        tasklist = Tasklist(extract(tasklist_str))
        logger.success(
            log_title,
            "Extracted tasklist from the document.",
            mdit.element.code_block(
                ps.write.to_yaml_string(tasklist.as_list),
                language="yaml",
                caption="Tasklist"
            ),
            body_md,
        )
        return tasklist

    def write_tasklist(self, tasklist: Tasklist) -> str:
        """Write an implementation tasklist as Markdown string
        and update it in the protocol.

        Parameters
        ----------
        tasklist
            A list of dictionaries, each representing a tasklist entry.
            The format of each dictionary is the same as that returned by
            `_extract_tasklist_entries`.
        """
        string = []

        def write(entry_list: Sequence[MainTasklistEntry | SubTasklistEntry], level=0):
            for entry in entry_list:
                check = 'X' if entry.complete else ' '
                string.append(f"{' ' * level * 2}- [{check}] {entry.summary.strip()}")
                if entry.body:
                    for line in entry.body.splitlines():
                        string.append(f"{' ' * (level + 1) * 2}{line}")
                write(entry.subtasks, level + 1)

        write(tasklist.tasks)
        tasklist = "\n".join(string).strip()
        return self.add_data(
            id="tasklist",
            spec=self._manager.data["doc.protocol.tasklist"],
            data=f"\n{tasklist}\n",
            replace=True
        )

    def _make_text_marker(self, id: str, data: dict | None = None) -> tuple[str, str]:
        data = data or {}
        return tuple(
            data[pos] if pos in data else self._config["marker"][pos].format(id)
            for pos in ("start", "end")
        )

    @staticmethod
    def _extract_issue_ticket_inputs(body: str, body_elems: list[dict]) -> dict[str, str | list]:

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

        def extract_value(raw_value: str, elem_settings):
            raw_value = raw_value.strip()
            elem_type = elem_settings["type"]
            if elem_type == "textarea":
                render = elem_settings.get("attributes", {}).get("render")
                if render:
                    return raw_value.removeprefix(f"```{render}").removesuffix("```")
                return raw_value
            if elem_type == "dropdown":
                multiple = elem_settings.get("attributes", {}).get("multiple")
                if multiple:
                    return raw_value.split(", ")
                return raw_value
            if elem_type == "checkboxes":
                out = []
                for line in raw_value.splitlines():
                    if line.startswith("- [X] "):
                        out.append(True)
                    elif line.startswith("- [ ] "):
                        out.append(False)
                return out
            return raw_value

        parts = []
        settings = {}
        for elem in body_elems:
            if elem["type"] == "markdown" or not elem.get("active", True):
                continue
            elem_id = elem.get("id")
            parts.append({"id": elem_id, "title": elem["attributes"]["label"], "optional": optional})
            if elem_id:
                settings[elem_id] = elem
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
            section_id: extract_value(content, settings[section_id]) if content else None
            for section_id, content in match.groupdict().items()
        }
        logger.debug("Matched sections", str(sections))
        return sections

    # @staticmethod
    # def _toggle_checkbox(checkbox: str, check: bool) -> str:
    #     """Toggle the checkbox in a markdown tasklist entry."""
    #
    #     def replacer(match):
    #         checkmark = "X" if check else " "
    #         return f"{match.group(1)}{checkmark}{match.group(3)}"
    #
    #     pattern = re.compile(r"(^[\s\n]*-\s*\[)([ ]|X)(]\s*)", re.MULTILINE)
    #     matches = re.findall(pattern, checkbox)
    #     if len(matches) == 0 or len(matches) > 1:
    #         logger.warning(
    #             "Checkbox Toggle",
    #             f"Found {len(matches)} checkboxes in the input string:",
    #             mdit.element.code_block(checkbox, language="markdown", caption="Input String"),
    #         )
    #         return checkbox
    #     return re.sub(pattern, replacer, checkbox, count=1)
