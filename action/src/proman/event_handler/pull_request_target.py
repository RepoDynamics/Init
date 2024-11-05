from __future__ import annotations

from typing import TYPE_CHECKING

from github_contexts import github as _gh_context

from loggerman import logger
from proman.datatype import (
    BranchType, Branch
)

from proman.main import EventHandler

if TYPE_CHECKING:
    from github_contexts.github.payload import PullRequestPayload
    from github_contexts.github.payload.object import PullRequest


class PullRequestTargetEventHandler(EventHandler):

    def __init__(self, **kwargs):

        def make_branch_env_vars(branch: Branch):
            return {
                "type": branch.type.value,
                "name": branch.name,
                "prefix": branch.prefix,
                "suffix": branch.suffix,
                "url": self._gh_link.branch(branch.name).homepage,
            }

        super().__init__(**kwargs)

        self._payload: PullRequestPayload = self._context.event
        self._pull: PullRequest = self._payload.pull_request
        self._pull_author =

        self._branch_base = self.resolve_branch(self._context.base_ref)
        self._branch_head = self.resolve_branch(self._context.head_ref)

        self._devdoc.protocol = self._pull.body
        self._devdoc.env_vars |= {
            "workflow_url": self._gh_link.workflow_run(run_id=self._context.run_id),
            "head": make_branch_env_vars(self._branch_head),
            "base": make_branch_env_vars(self._branch_base),
        }

        {
            "actor": self._payload.sender,
            "payload": self._payload,
            "pull": self._pull,
        }

        logger.info("Base Branch Resolution", str(self._branch_base))
        logger.info("Head Branch Resolution", str(self._branch_head))
        return

    def run(self):
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
