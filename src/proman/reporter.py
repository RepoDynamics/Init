from pathlib import Path
import re
from typing import Literal
from loggerman import logger
from github_contexts import github as _gh_context
from markitup import html, md, doc

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
            "file_change": {"name": "File Changes"},
            "cca": {"name": "CCA"},
            "hooks": {"name": "Hooks"},
        }
        for val in self._info.values():
            val["status"] = None
            val["summary"] = None
            val["details_full"] = []
            val["details_short"] = []
        self._context_summary = self._generate_context_summary()
        return

    def event(self, description: str):
        self._event_description = re.sub(r'`([^`]*)`', r'<code>\1</code>', description)
        logger.info("Event", description.replace("`", "'"))
        return

    def add(
        self,
        name: str,
        status: Literal["pass", "fail", "skip", "warning"] | None = None,
        summary: str | None = None,
        details_full: str | html.Element | list = None,
        details_short: str | html.Element | list = None,
    ):
        data = self._info[name]
        data["status"] = status
        data["summary"] = summary
        for detail_type, detail in (("full", details_full), ("short", details_short)):
            if not detail:
                continue
            details_list = data[f"details_{detail_type}"]
            if isinstance(detail, (list, tuple)):
                details_list.extend(detail)
            else:
                details_list.append(detail)
        return

    def generate(self) -> tuple[str, str]:
        summary = self._generate_summary()
        content_list_gha = [summary, self._context_summary]
        content_list_full = content_list_gha + self._generate_context()
        sections_gha, sections_full = self._generate_sections()
        report_gha = doc.from_contents(
            heading="Workflow Summary",
            content=html.elem.ul([html.elem.li(content) for content in content_list_gha]),
            section=sections_gha,
        )
        report_full = doc.from_contents(
            heading="Workflow Report",
            content=html.elem.ul([html.elem.li(content) for content in content_list_full]),
            section=sections_full,
        )
        report_full.add_highlight(languages=["yaml"])
        return report_gha.syntax_md(), report_full.syntax_html()

    def _generate_summary(self) -> html.elem.Details:
        failed = False
        skipped = False
        table_rows = []
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
                (status_emoji.emoji, {"title": status_emoji.title}),
                html.elem.td(pipeline["summary"]),
            ]
            table_rows.append(row)
        table = html.elem.table_from_rows(
            rows_body=table_rows,
            rows_head=[["Pipeline", "Status", "Summary"]],
        )
        if failed:
            workflow_status = "fail"
        elif skipped:
            workflow_status = "skip"
        else:
            workflow_status = "pass"
        workflow_status_emoji = EMOJI[workflow_status]
        summary = html.elem.summary(
            f"{workflow_status_emoji.emoji} {self._event_description}",
            title=workflow_status_emoji.title,
        )
        return html.elem.details([summary, table], open=True)

    def _generate_context(self) -> list[html.elem.Details]:
        output = []
        for data, summary in (
            (self._context, "🎬 GitHub Context"),
            (self._context.event, "📥 Event Payload"),
        ):
            code = html.elem.code(str(data), {"class": "language-yaml"})
            pre = html.elem.pre(code)
            details = html.elem.details(summary, pre)
            output.append(details)
        return output

    def _generate_context_summary(self) -> html.elem.Details:
        rows = []
        event_type = self._context.event_name
        if hasattr(self._context.event, "action"):
            event_type += f" ({self._context.event.action.value})"
        for name, val in (
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
        ):
            logger.info(name, val)
            rows.append([name, val])
        table = html.elem.table_from_rows(rows_body=rows, rows_head=[["Property", "Value"]])
        summary = html.elem.summary("🎬 Context Summary")
        return html.elem.details([summary, table], open=True)

    def _generate_sections(self) -> tuple[list[doc.Document], list[doc.Document]]:
        sections_gha = []
        sections_full = []
        for data in self._info.values():
            if not data["details_full"]:
                continue
            section_full = doc.from_contents(
                heading=data['name'],
                content=data["details_full"],
            )
            sections_full.append(section_full)
            if not data["details_short"]:
                section_gha = section_full
            else:
                section_gha = doc.from_contents(
                    heading=data['name'],
                    content=data["details_short"],
                )
            sections_gha.append(section_gha)
        return sections_gha, sections_full
