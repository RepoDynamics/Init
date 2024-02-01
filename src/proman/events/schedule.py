import conventional_commits.message
from markitup import html, md
from github_contexts import GitHubContext
from github_contexts.github.payloads.schedule import SchedulePayload

from repodynamics.action.events._base import EventHandler
from repodynamics.datatype import TemplateType
from repodynamics import _util
from repodynamics.logger import Logger
from repodynamics.datatype import InitCheckAction
from repodynamics.control.manager import ControlCenterManager


class ScheduleEventHandler(EventHandler):

    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str | None = None,
        logger: Logger | None = None,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
            logger=logger
        )
        self._payload: SchedulePayload = self._context.event
        return

    def _run_event(self):
        cron = self._payload.schedule
        if cron == self._ccm_main.workflow__init__schedule__sync:
            self._run_sync()
        elif cron == self._ccm_main.workflow__init__schedule__test:
            self._run_test()
        else:
            self._logger.error(
                f"Unknown cron expression for scheduled workflow: {cron}",
                f"Valid cron expressions defined in 'workflow.init.schedule' metadata are:\n"
                f"{self._ccm_main.workflow__init__schedule}",
            )

    def _run_sync(self):
        commit_hash_announce = self._web_announcement_expiry_check()
        meta = ControlCenterManager(
            path_repo=self._path_root_base,
            github_token=self._context.token,
            logger=self._logger,
        )
        commit_hash_meta = self._action_meta(
            action=InitCheckAction.PULL,
            meta=meta,
            base=True,
            branch=self.resolve_branch()
        )
        if commit_hash_announce or commit_hash_meta:
            self._git_base.push()
        return

    def _run_test(self):
        return

    def _web_announcement_expiry_check(self) -> str | None:
        name = "Website Announcement Expiry Check"
        current_announcement = self._read_web_announcement_file(base=True, ccm=self._ccm_main)
        if current_announcement is None:
            self.add_summary(
                name=name,
                status="skip",
                oneliner="Announcement file does not exist❗",
                details=html.ul(
                    [
                        f"❎ No changes were made.",
                        f"🚫 The announcement file was not found.",
                    ]
                ),
            )
            return
        (commit_date_relative, commit_date_absolute, commit_date_epoch, commit_details) = (
            self._git_base.log(
                number=1,
                simplify_by_decoration=False,
                pretty=pretty,
                date=date,
                paths=self._ccm_main["path"]["file"]["website_announcement"],
            )
            for pretty, date in (
                ("format:%cd", "relative"),
                ("format:%cd", None),
                ("format:%cd", "unix"),
                (None, None),
            )
        )
        if not current_announcement:
            last_commit_details_html = html.details(
                content=md.code_block(commit_details),
                summary="📝 Removal Commit Details",
            )
            self.add_summary(
                name=name,
                status="skip",
                oneliner="📭 No announcement to check.",
                details=html.ul(
                    [
                        f"❎ No changes were made."
                        f"📭 The announcement file is empty.\n",
                        f"📅 The last announcement was removed {commit_date_relative} on {commit_date_absolute}.\n",
                        last_commit_details_html,
                    ]
                ),
            )
            return
        current_date_epoch = int(_util.shell.run_command(["date", "-u", "+%s"], logger=self._logger)[0])
        elapsed_seconds = current_date_epoch - int(commit_date_epoch)
        elapsed_days = elapsed_seconds / (24 * 60 * 60)
        retention_days = self._ccm_main.web["announcement_retention_days"]
        retention_seconds = retention_days * 24 * 60 * 60
        remaining_seconds = retention_seconds - elapsed_seconds
        remaining_days = retention_days - elapsed_days
        if remaining_seconds > 0:
            current_announcement_html = html.details(
                content=md.code_block(current_announcement, "html"),
                summary="📣 Current Announcement",
            )
            last_commit_details_html = html.details(
                content=md.code_block(commit_details),
                summary="📝 Current Announcement Commit Details",
            )
            self.add_summary(
                name=name,
                status="skip",
                oneliner=f"📬 Announcement is still valid for another {remaining_days:.2f} days.",
                details=html.ul(
                    [
                        "❎ No changes were made.",
                        "📬 Announcement is still valid.",
                        f"⏳️ Elapsed Time: {elapsed_days:.2f} days ({elapsed_seconds} seconds)",
                        f"⏳️ Retention Period: {retention_days} days ({retention_seconds} seconds)",
                        f"⏳️ Remaining Time: {remaining_days:.2f} days ({remaining_seconds} seconds)",
                        current_announcement_html,
                        last_commit_details_html,
                    ]
                ),
            )
            return
        # Remove the expired announcement
        removed_announcement_html = html.details(
            content=md.code_block(current_announcement, "html"),
            summary="📣 Removed Announcement",
        )
        last_commit_details_html = html.details(
            content=md.code_block(commit_details),
            summary="📝 Removed Announcement Commit Details",
        )
        self._write_web_announcement_file(announcement="", base=True, ccm=self._ccm_main)
        commit_msg = conventional_commits.message.create(
            typ=self._ccm_main["commit"]["secondary_action"]["auto-update"]["type"],
            description="Remove expired website announcement",
            body=(
                f"The following announcement made {commit_date_relative} on {commit_date_absolute} "
                f"was expired after {elapsed_days:.2f} days and thus automatically removed:\n\n"
                f"{current_announcement}"
            ),
            scope="web-announcement",
        )
        commit_hash = self._git_base.commit(message=str(commit_msg), stage="all")
        commit_link = str(self._gh_link.commit(commit_hash))
        self.add_summary(
            name=name,
            status="pass",
            oneliner="🗑 Announcement was expired and thus removed.",
            details=html.ul(
                [
                    f"✅ The announcement was removed (commit {html.a(commit_link, commit_hash)}).",
                    f"⌛ The announcement had expired {abs(remaining_days):.2f} days ({abs(remaining_seconds)} seconds) ago.",
                    f"⏳️ Elapsed Time: {elapsed_days:.2f} days ({elapsed_seconds} seconds)",
                    f"⏳️ Retention Period: {retention_days} days ({retention_seconds} seconds)",
                    removed_announcement_html,
                    last_commit_details_html,
                ]
            ),
        )
        return commit_hash
