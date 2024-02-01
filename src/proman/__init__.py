import github_contexts
from github_contexts.github.enums import EventType
import actionman
from loggerman import logger

from repodynamics.datatype import TemplateType
from repodynamics.exception import RepoDynamicsError

from proman.exception import ProManError
from proman.events.issue_comment import IssueCommentEventHandler
from proman.events.issues import IssuesEventHandler
from proman.events.pull_request import PullRequestEventHandler
from proman.events.pull_request_target import PullRequestTargetEventHandler
from proman.events.push import PushEventHandler
from proman.events.schedule import ScheduleEventHandler
from proman.events.workflow_dispatch import WorkflowDispatchEventHandler


def run():

    try:
        outputs, env_vars, summary = event_handler.run()
    except Exception as e:
        logger.critical(title=f"An unexpected error occurred", message=str(e))
        raise e  # This will never be reached, but is required to satisfy the type checker and IDE
    logger.section("Write Outputs and Summary")
    if outputs:
        actionman.io.write_github_outputs(outputs, logger=logger)
    if env_vars:
        actionman.io.write_github_outputs(env_vars, to_env=True, logger=logger)
    if summary:
        actionman.io.write_github_summary(content=summary, logger=logger)
    return


@logger.sectioner("Initialize", group=False)
def _init():
    inputs = actionman.io.read_environment_variables(
        ("TEMPLATE_TYPE", str, True, False),
        ("GITHUB_CONTEXT", dict, True, False),
        ("PATH_REPO_BASE", str, True, False),
        ("PATH_REPO_HEAD", str, True, False),
        ("ADMIN_TOKEN", str, False, True),
        name_prefix="RD_PROMAN__",
        logger=logger,
        log_section_name="Read Inputs"
    )
    template_type = _get_template_type(input_template_type=inputs.pop("TEMPLATE_TYPE"))
    context_manager = github_contexts.context_github(context=inputs.pop("GITHUB_CONTEXT"))
    event_handler_class = _get_event_handler(event=context_manager.event_name)
    event_handler = event_handler_class(
        template_type=template_type,
        context_manager=context_manager,
        admin_token=inputs["ADMIN_TOKEN"] or "",
        path_repo_base=inputs["PATH_REPO_BASE"],
        path_repo_head=inputs["PATH_REPO_HEAD"],
    )
    return event_handler


@logger.sectioner("Verify Template Type")
def _get_template_type(input_template_type: str) -> TemplateType:
    """Parse and verify the input template type."""
    try:
        template_type = TemplateType(input_template_type)
    except ValueError:
        supported_templates = ", ".join([f"'{enum.value}'" for enum in TemplateType])
        raise RepoDynamicsError(
            "Template type verification failed; "
            f"expected one of {supported_templates}, but got '{input_template_type}'."
        )
    logger.info("Template type", template_type.value)
    return template_type


@logger.sectioner("Verify Triggering Event")
def _get_event_handler(event: EventType):
    logger.info("Triggering event", event.value)
    event_to_handler = {
        EventType.ISSUES: IssuesEventHandler,
        EventType.ISSUE_COMMENT: IssueCommentEventHandler,
        EventType.PULL_REQUEST: PullRequestEventHandler,
        EventType.PULL_REQUEST_TARGET: PullRequestTargetEventHandler,
        EventType.PUSH: PushEventHandler,
        EventType.SCHEDULE: ScheduleEventHandler,
        EventType.WORKFLOW_DISPATCH: WorkflowDispatchEventHandler,
    }
    handler = event_to_handler.get(event)
    if not handler:
        supported_events = ", ".join([f"'{enum.value}'" for enum in event_to_handler.keys()])
        raise RepoDynamicsError(
            "Unsupported workflow triggering event; "
            f"expected one of {supported_events}, but got '{event.value}'."
        )
    logger.info("Event handler", handler.__name__)
    return handler

