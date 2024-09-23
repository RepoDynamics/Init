from pathlib import Path
import re
from typing import Literal
from loggerman import logger
from github_contexts import github as _gh_context
import mdit
import htmp

from proman.datatype import TitledEmoji

EMOJI = {
        "pass": TitledEmoji("Passed", "✅"),
        "skip": TitledEmoji("Skipped", "⏭️"),
        "fail": TitledEmoji("Failed", "❌"),
        "warning": TitledEmoji("Passed with Warning", "⚠️"),
    }


class Reporter:

    def __init__(self, github_context: _gh_context.GitHubContext):
        self._context = github_context
        self._event_description: str = ""
        self._info = {
            "main": {"name": "Main"},
            "event": {"name": "Event"},
            "file_change": {"name": "File Changes"},
            "cca": {"name": "CCA"},
            "hooks": {"name": "Hooks"},
        }
        for val in self._info.values():
            val["status"] = None
            val["summary"] = None
            val["body"] = mdit.block_container()
            val["section"] = mdit.section_container()
        self._context_summary = self._generate_context_summary()
        return

    def event(self, description):
        self._event_description = description
        logger.info("Event Description", description)
        return

    def add(
        self,
        name: str,
        status: Literal["pass", "fail", "skip", "warning"] | None = None,
        summary= None,
        body=None,
        section=None,
        section_is_container=False,
    ):
        data = self._info[name]
        if status:
            data["status"] = status
        if summary:
            data["summary"] = summary
        if body:
            data["body"].extend(body)
        if section:
            if section_is_container:
                for content, conditions in section.values():
                    data["section"].append(content, conditions=conditions)
            else:
                data["section"].extend(section)
        return

    def generate(self) -> tuple[str, str, bool]:
        status_badge, summary_table, failed = self._generate_summary()
        body = mdit.block_container(status_badge)
        if self._event_description:
            body.append(mdit.element.field_list_item("Event Description", self._event_description))
        body.extend(summary_table, self._context_summary)
        section = self._generate_sections()
        report = mdit.document(
            heading="Workflow Summary",
            body=body,
            section=section,
        )
        gha_summary = report.source(target="github", filters=["short, github"], separate_sections=False)
        full_summary = report.render(target="sphinx", filters=["full"], separate_sections=False)
        return gha_summary, full_summary, failed

    def _generate_summary(self) -> tuple[mdit.element.InlineImage, mdit.element.Table, bool]:
        failed = False
        skipped = False
        table_rows = [["Pipeline", "Status", "Summary"]]
        for pipeline in self._info.values():
            status = pipeline["status"]
            if not status:
                continue
            if status == "fail":
                failed = True
            elif status == "skip":
                skipped = True
            status_emoji = EMOJI[status]
            row = [
                pipeline["name"],
                htmp.element.span(status_emoji.emoji, title=status_emoji.title),
                pipeline["summary"],
            ]
            table_rows.append(row)
        table = mdit.element.table(
            rows=table_rows,
            caption="Pipeline Summary",
            num_rows_header=1,
            align_table="center",
        )
        if failed:
            workflow_status = "fail"
            color="rgb(200, 0, 0)"
        elif skipped:
            workflow_status = "skip"
            color = "rgb(0, 0, 200)"
        else:
            workflow_status = "pass"
            color = "rgb(0, 200, 0)"
        workflow_status_emoji = EMOJI[workflow_status]
        status_badge = mdit.element.badge(
            service="static",
            args={"message": workflow_status_emoji.title},
            label="Status",
            style="for-the-badge",
            color=color,
        )
        return status_badge, table, failed

    def _generate_context(self) -> list[mdit.element.DropDown]:
        output = []
        for data, summary, icon in (
            (self._context, "GitHub Context", "🎬"),
            (self._context.event, "Event Payload", "📥"),
        ):
            code = mdit.element.code_block(str(data), language="yaml")
            dropdown = mdit.element.dropdown(
                title=summary,
                body=code,
                color="info",
                icon=icon,
            )
            output.append(dropdown)
        return output

    def _generate_context_summary(self) -> mdit.element.DropDown:
        event_type = self._context.event_name.value
        if hasattr(self._context.event, "action"):
            event_type += f" {self._context.event.action.value}"
        field_list = mdit.element.field_list(
            [
                ("Event Type", event_type),
                ("Ref Type", self._context.ref_type.value),
                ("Ref", self._context.ref),
                ("SHA", self._context.sha),
                ("Actor", self._context.actor),
                ("Triggering Actor", self._context.triggering_actor),
                ("Run ID", self._context.run_id),
                ("Run Number", self._context.run_number),
                ("Run Attempt", self._context.run_attempt),
                ("Workflow Ref", self._context.workflow_ref),
            ]
        )
        logger.info("Context Summary", field_list)
        dropdown = mdit.element.dropdown(
            title="Context Summary",
            body=field_list,
            color="info",
            icon="🎬",
            opened=True,
        )
        return dropdown

    def _generate_sections(self) -> dict[str, mdit.Document]:
        sections = {}
        for section_id, data in self._info.items():
            if section_id == "event":
                for context_dropdown in self._generate_context():
                    data["body"].append(context_dropdown, conditions=["full"])
            if not (data["body"] or data["section"]):
                continue
            section_full = mdit.document(
                heading=data['name'],
                body=data["body"],
                section=data["section"],
            )
            sections[section_id] = section_full
        return sections
