from github_contexts import GitHubContext
from github_contexts.github.payloads.pull_request import PullRequestPayload
from github_contexts.github.enums import ActionType
from loggerman import logger
from controlman.datatype import (
    Label,
    PrimaryActionCommit,
    PrimaryCustomCommit,
    PrimaryActionCommitType,
    CommitGroup,
    BranchType,
    IssueStatus,
    RepoFileType,
    InitCheckAction,
    LabelType,
)

from proman.datatype import TemplateType
from proman.handler.main import EventHandler


class PullRequestTargetEventHandler(EventHandler):

    @logger.sectioner("Initialize Event Handler")
    def __init__(
        self,
        template_type: TemplateType,
        context_manager: GitHubContext,
        admin_token: str,
        path_repo_base: str,
        path_repo_head: str | None = None,
    ):
        super().__init__(
            template_type=template_type,
            context_manager=context_manager,
            admin_token=admin_token,
            path_repo_base=path_repo_base,
            path_repo_head=path_repo_head,
        )
        self._payload: PullRequestPayload = self._context.event
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

    @logger.sectioner("Execute Event Handler", group=False)
    def _run_event(self):
        if self._payload.internal:
            return
        action = self._payload.action
        if action != ActionType.OPENED:
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
