import actionman
import github_contexts
from github_contexts.github.enums import EventType
from loggerman import logger
from repodynamics.datatype import TemplateType
from repodynamics.exception import RepoDynamicsError

from proman.exception import ProManInternalError
from proman.events.issue_comment import IssueCommentEventHandler
from proman.events.issues import IssuesEventHandler
from proman.events.pull_request import PullRequestEventHandler
from proman.events.pull_request_target import PullRequestTargetEventHandler
from proman.events.push import PushEventHandler
from proman.events.schedule import ScheduleEventHandler
from proman.events.workflow_dispatch import WorkflowDispatchEventHandler


def run():
    try:
        event_handler = _init_handler()
        outputs, summary = event_handler.run()
        _write_outputs_and_summary(outputs, summary)
    except Exception:
        logger.critical(f"An unexpected error occurred")
    return


@logger.sectioner("Initialize", group=False)
def _init_handler():
    inputs = _read_inputs()
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


@logger.sectioner("Read Inputs")
def _read_inputs():
    parsed_inputs = {}

    logger.section("GitHub Context")
    env_var_name = "RD_PROMAN__GITHUB_CONTEXT"
    context = actionman.environment_variable.read(name=env_var_name, typ=dict)
    logger.info("Success", f"Read GitHub context from environment variable '{env_var_name}'.")
    parsed_inputs["github_context"] = context
    context_redacted = {k: v for k, v in context.items() if k != "token"}
    logger.debug(code_title="Context", code=context_redacted)
    logger.section_end()

    logger.section("Admin Token")
    env_var_name = "RD_PROMAN__ADMIN_TOKEN"
    admin_token = actionman.environment_variable.read(name=env_var_name, typ=str)
    logger.info(
        "Success",
        f"Read admin token from environment variable '{env_var_name}'. "
        f"Token was{' not ' if not admin_token else ' '}provided."
    )
    parsed_inputs["admin_token"] = admin_token
    logger.section_end()

    for section_title, env_var_name, env_var_type in (
        ("Template Type", "TEMPLATE_TYPE", str,),
        ("Base Repository Path", "PATH_REPO_BASE", str),
        ("Head Repository Path", "PATH_REPO_HEAD", str),
    ):
        logger.section(section_title)
        value = actionman.environment_variable.read(name=f"RD_PROMAN__{env_var_name}", typ=env_var_type)
        logger.info(
            "Success",
            f"Read {section_title.lower()} from environment variable '{env_var_name}'.",
        )
        logger.debug(code_title="Value", code=value)
        parsed_inputs[env_var_name.lower()] = value
        logger.section_end()
    return parsed_inputs


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


@logger.sectioner("Write Outputs and Summary", group=False)
def _write_outputs_and_summary(outputs: dict, summary: str) -> None:
    _write_step_outputs(kwargs=outputs)
    _write_step_summary(content=summary)
    return


@logger.sectioner("Write Step Outputs")
def _write_step_outputs(kwargs: dict) -> None:
    for name, value in kwargs.items():
        output_name = name.lower().replace("_", "-")
        logger.section(f"{output_name} [{type(value).__name__}]")
        logger.debug(code_title="Value", code=value)
        try:
            written_output = actionman.step_output.write(name=output_name, value=value)
        except (
            actionman.exception.ActionManOutputVariableTypeError,
            actionman.exception.ActionManOutputVariableSerializationError,
        ) as e:
            raise ProManInternalError(
                f"Failed to write step output variable '{output_name}'."
            ) from e
        logger.info("Success", f"Wrote step output variable '{output_name}'.")
        logger.debug(code_title="Output", code=written_output)
        logger.section_end()
    return


@logger.sectioner("Write Step Summary")
def _write_step_summary(content: str) -> None:
    logger.debug(code_title="Content", code=content)
    actionman.step_summary.write(content)
    logger.info("Success", f"Wrote step summary ({len(content)} characters).")
    return
