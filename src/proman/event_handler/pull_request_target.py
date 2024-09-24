from github_contexts import github as _gh_context

from loggerman import logger
from proman.datatype import (
    BranchType,
)

from proman.datatype import TemplateType
from proman.main import EventHandler


class PullRequestTargetEventHandler(EventHandler):

    @logger.sectioner("Initialize Event Handler")
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._payload: _gh_context.payload.PullRequestPayload = self._context.event
        self._pull = self._payload.pull_request
        self._branch_base = self.resolve_branch(self._context.base_ref)
        logger.info(
            "Resolve base branch",
            self._branch_base.type.value,
            code_title="Branch details",
            code=self._branch_base,
        )
        self._branch_head = self.resolve_branch(self._context.head_ref)
        logger.info(
            "Resolve head branch",
            self._branch_head.type.value,
            code_title="Branch details",
            code=self._branch_head,
        )
        return

    @logger.sectioner("Execute Event Handler")
    def _run_event(self):
        if self._payload.internal:
            return
        action = self._payload.action
        if action != _gh_context.enum.ActionType.OPENED:
            self.error_unsupported_triggering_action()
            return
        if self._branch_head.type is BranchType.DEV:
            return self.opened_head_dev()

        return

    def opened_head_dev(self):
        if self._branch_head.name != self._branch_base.name:
            # Update base branch to corresponding dev branch and inform user
            pass

        return
