from typing import Generator

import conventional_commits.message
from github_contexts import GitHubContext
from github_contexts.github.payload.schedule import SchedulePayload
from loggerman import logger
import pyshellman
import controlman
from proman.dtype import BranchType, InitCheckAction, Branch

from proman.main import EventHandler


class ScheduleEventHandler(EventHandler):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: SchedulePayload = self._context.event
        return

    def _run_event(self):
        cron = self._payload.schedule
        if cron == self._data_main["workflow"]["schedule"]["sync"]["cron"]:
            return self._run_sync()
        if cron == self._data_main["workflow"]["schedule"]["test"]["cron"]:
            return self._run_test()
        logger.critical(
            f"Unknown cron expression for scheduled workflow: {cron}",
            f"Valid cron expressions defined in 'workflow.init.schedule' metadata are:\n"
            f"{self._data_main.workflow__init__schedule}",
        )
        return

    def _run_sync(self):
        cc_manager_generator = self._get_cc_manager_generator(
            branch_types=(BranchType.AUTOUPDATE, ), exclude_branch_types=True
        )
        for cc_manager, branch, _ in cc_manager_generator:
            self._sync(
                action=InitCheckAction(self._data_main["workflow"]["schedule"]["sync"]["branch"][branch.type.value]),
                cc_manager=cc_manager,
                base=False,
                branch=branch
            )
        self._git_head.checkout(branch=self._context.event.repository.default_branch)
        commit_hash_announce = self._web_announcement_expiry_check()
        if commit_hash_announce:
            self._git_head.push()
        return

    def _run_test(self):
        cc_manager_generator = self._get_cc_manager_generator(
            branch_types=(BranchType.MAIN, BranchType.RELEASE, BranchType.PRERELEASE)
        )
        for _, branch, sha in cc_manager_generator:
            latest_hash = self._action_hooks(
                action=InitCheckAction(
                    self._data_main["workflow"]["schedule"]["test"]["branch"][branch.type.value]),
                branch=branch,
                base=False,
                ref_range=None,
            )
            ccm_branch = controlman.read_from_json_file(path_repo=self._path_head)
            self._output.set(
                data_branch=ccm_branch,
                ref=latest_hash or sha,
                ref_before=sha,
                version=self._get_latest_version(base=False),
                website_url=self._data_main["url"]["website"]["base"],
                website_build=True,
                package_lint=True,
                package_test=True,
                package_test_source="PyPI",
                package_build=True,
            )
        return

    def _get_cc_manager_generator(
        self, branch_types: tuple[BranchType, ...], exclude_branch_types: bool = False
    ) -> Generator[tuple[controlman.CenterManager, Branch, str], None, None]:
        for branch_data in self._gh_api.branches:
            branch_name = branch_data["name"]
            branch = self.resolve_branch(branch_name=branch_name)
            if exclude_branch_types:
                if branch.type in branch_types:
                    continue
            elif branch.type not in branch_types:
                continue
            self._git_head.fetch_remote_branches_by_name(branch_names=branch_name)
            self._git_head.checkout(branch=branch_name)
            cc_manager = self.get_cc_manager()
            yield cc_manager, branch, branch_data["commit"]["sha"]

    def _web_announcement_expiry_check(self) -> str | None:
        name = "Website Announcement Expiry Check"
        current_announcement = self.read_announcement_file(base=False, data=self._data_main)
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
            self._git_head.log(
                number=1,
                simplify_by_decoration=False,
                pretty=pretty,
                date=date,
                paths=self._data_main["path"]["file"]["website_announcement"],
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
        current_date_epoch = int(pyshellman.run(["date", "-u", "+%s"]).output)
        elapsed_seconds = current_date_epoch - int(commit_date_epoch)
        elapsed_days = elapsed_seconds / (24 * 60 * 60)
        retention_days = self._data_main.web["announcement_retention_days"]
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
        self.write_announcement_file(announcement="", base=False, data=self._data_main)
        commit_msg = conventional_commits.message.create(
            typ=self._data_main["commit"]["secondary_action"]["auto-update"]["type"],
            description="Remove expired website announcement",
            body=(
                f"The following announcement made {commit_date_relative} on {commit_date_absolute} "
                f"was expired after {elapsed_days:.2f} days and thus automatically removed:\n\n"
                f"{current_announcement}"
            ),
            scope="web-announcement",
        )
        commit_hash = self._git_head.commit(message=str(commit_msg), stage="all")
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
