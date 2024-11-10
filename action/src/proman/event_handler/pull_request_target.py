from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from github_contexts import github as _gh_context

from loggerman import logger
from proman.dtype import BranchType


from proman.main import EventHandler

if TYPE_CHECKING:
    from github_contexts.github.payload import PullRequestPayload
    from github_contexts.github.payload.object import PullRequest
    from proman.dstruct import Branch


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

        self.payload: PullRequestPayload = self.gh_context.event
        self.pull: PullRequest = self.payload.pull_request
        self.pull_author = self.manager.user.from_issue_author(self.pull)
        pull = copy.deepcopy(self.pull.as_dict)
        pull["user"] = self.pull_author
        self.jinja_env_vars["pull_request"] = pull

        self._branch_base = self.manager.branch.from_name(self.gh_context.base_ref)
        self._branch_head = self.manager.branch.from_name(self.gh_context.head_ref)

        self.protocol_manager.protocol = self.pull.body
        self.protocol_manager.env_vars |= {
            "workflow_url": self._gh_link.workflow_run(run_id=self.gh_context.run_id),
            "head": make_branch_env_vars(self._branch_head),
            "base": make_branch_env_vars(self._branch_base),
        }

        {
            "actor": self.payload.sender,
            "payload": self.payload,
            "pull": self.pull,
        }

        logger.info("Base Branch Resolution", str(self._branch_base))
        logger.info("Head Branch Resolution", str(self._branch_head))
        return

    def run(self):
        if self.payload.internal:
            return
        action = self.payload.action
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
