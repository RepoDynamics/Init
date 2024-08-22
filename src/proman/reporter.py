from pathlib import Path
from typing import Literal
from loggerman import logger
from github_contexts import github as _gh_context
from markitup import html, md, doc


class Reporter:

    def __init__(self, github_context: _gh_context.GitHubContext):
        self._context = github_context

        self._event_description: str = ""
        self._info = {
            "main": {"name": "Main"},
            "file_change": {"name": "File Change"},
        }

        self._report = doc.from_contents(
            heading="Workflow Report",

        )
        self._summary_oneliners: list[str] = []
        self._summary_sections: list[str | html.Element] = []

        return

    def set_event(self, description: str):
        self._event_description = description
        return




    def save_to_file(self, dir_path: str | Path):
        """Finalize the report.

        This saves the full HTML versions of the report and log to a given file,
        and returns the short GitHub Actions version of the report as a string.
        """
        filename = (
            f"{self._context.repository_name}-workflow-run"
            f"-{self._context.run_id}-{self._context.run_attempt}.{{}}.html"
        )
        dir_path = Path(dir_path)
        with open(dir_path / filename.format("report"), "w") as f:
            f.write(str(summaries))
        return

    @property
    def gha_summary(self) -> str:
        """GitHub Actions summary of the workflow run."""
        return ""

    def add_summary(
        self,
        name: str,
        status: Literal["pass", "fail", "skip", "warning"] | None = None,
        description: str | None = None,
        details: str | html.Element | None = None,
    ):

        self._summary_oneliners.append(f"{Emoji[status]}&nbsp;<b>{name}</b>: {description}")
        if details:
            self._summary_sections.append(f"<h2>{name}</h2>\n\n{details}\n\n")
        return

    def _add_event_details(self):
        logger.info("Event Type", self._context.event_name)
        if hasattr(self._context.event, "action"):
            logger.info("Action Type", self._context.event.action.value)
        logger.info("Ref Type", self._context.ref_type.value)
        logger.info("Ref", self._context.ref)
        logger.info("SHA", self._context.sha)
        logger.info("Actor", self._context.actor)
        logger.info("Triggering Actor", self._context.triggering_actor)
        logger.info("Run ID", self._context.run_id)
        logger.info("Run Number", self._context.run_number)
        logger.info("Run Attempt", self._context.run_attempt)
        logger.info("Workflow Ref", self._context.workflow_ref)


    def assemble_summary(self) -> str:
        github_context, event_payload = (
            html.details(content=md.code_block(str(data), lang="yaml"), summary=summary)
            for data, summary in (
                (self._context, "🎬 GitHub Context"),
                (self._context.event, "📥 Event Payload"),
            )
        )
        intro = [
            f"<b>Status</b>: {Emoji.FAIL if self._failed else Emoji.PASS}",
            f"<b>Event</b>: {self._event_description}",
            f"<b>Summary</b>: {html.ul(self._summary_oneliners)}",
            f"<b>Data</b>: {html.ul([github_context, event_payload])}",
        ]
        summary = html.ElementCollection([html.h(1, "Workflow Report"), html.ul(intro)])
        logs = html.ElementCollection(
            [
                html.h(2, "🪵 Logs"),
                html.details(logger.html_log, "Log"),
            ]
        )
        summaries = html.ElementCollection(self._summary_sections)
        path = Path("./proman_artifacts")
        path.mkdir(exist_ok=True)
