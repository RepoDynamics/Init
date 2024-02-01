from github_contexts import GitHubContext
from github_contexts.github.payloads.pull_request import PullRequestPayload
from github_contexts.github.enums import ActionType

from repodynamics.action.events._base import EventHandler
from repodynamics.logger import Logger
from repodynamics.datatype import TemplateType


class PullRequestTargetEventHandler(EventHandler):

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
        self._payload: PullRequestPayload = self._context.event
        return

    def _run_event(self):
        action = self._payload.action
        if action == ActionType.OPENED:
            self.event_pull_target_opened()
        elif action == ActionType.REOPENED:
            self.event_pull_target_reopened()
        elif action == ActionType.SYNCHRONIZE:
            self.event_pull_target_synchronize()
        else:
            self.error_unsupported_triggering_action()

    def event_pull_target_opened(self):
        return

    def event_pull_target_reopened(self):
        return

    def event_pull_target_synchronize(self):
        return

    def event_pull_request_target(self):
        self.set_job_run("website_rtd_preview")
        return
