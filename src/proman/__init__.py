import actionman as _actionman
import github_contexts as _github_contexts
from loggerman import logger as _logger

from proman.datatype import TemplateType as _TemplateType
from proman import exception as _exception, event_handler as _handler


@_logger.sectioner(catch_exception_set=Exception)
def run():
    try:
        event_handler = _init_handler()
        outputs, summary = event_handler.run()
        _write_outputs_and_summary(outputs, summary)
    except Exception as e:
        raise _exception.ProManInternalError() from e
    return


@_logger.sectioner("Initialize", group=False)
def _init_handler():
    inputs = _read_inputs()
    context_manager = _github_contexts.github.create(context=inputs["github_context"])
    event_handler_class = _get_event_handler(event=context_manager.event_name)
    event_handler = event_handler_class(
        github_context=context_manager,
        admin_token=inputs["admin_token"],
        path_repo_base=inputs["path_repo_base"],
        path_repo_head=inputs["path_repo_head"],
    )
    return event_handler


@_logger.sectioner("Read Inputs")
def _read_inputs():
    parsed_inputs = {}

    _logger.section("GitHub Context")
    env_var_name = "RD_PROMAN__GITHUB_CONTEXT"
    context = _actionman.environment_variable.read(name=env_var_name, typ=dict)
    _logger.info("Success", f"Read GitHub context from environment variable '{env_var_name}'.")
    parsed_inputs["github_context"] = context
    context_redacted = {k: v for k, v in context.items() if k != "token"}
    _logger.debug(code_title="Context", code=context_redacted)
    _logger.section_end()

    _logger.section("Admin Token")
    env_var_name = "RD_PROMAN__ADMIN_TOKEN"
    admin_token = _actionman.environment_variable.read(name=env_var_name, typ=str)
    _logger.info(
        "Success",
        f"Read admin token from environment variable '{env_var_name}'. "
        f"Token was{' not ' if not admin_token else ' '}provided."
    )
    parsed_inputs["admin_token"] = admin_token
    _logger.section_end()

    for section_title, env_var_name, env_var_type in (
        ("Base Repository Path", "PATH_REPO_BASE", str),
        ("Head Repository Path", "PATH_REPO_HEAD", str),
    ):
        _logger.section(section_title)
        value = _actionman.environment_variable.read(name=f"RD_PROMAN__{env_var_name}", typ=env_var_type)
        _logger.info(
            "Success",
            f"Read {section_title.lower()} from environment variable '{env_var_name}'.",
        )
        _logger.debug(code_title="Value", code=value)
        parsed_inputs[env_var_name.lower()] = value
        _logger.section_end()
    return parsed_inputs


@_logger.sectioner("Verify Triggering Event")
def _get_event_handler(event: _github_contexts.github.enum.EventType):
    _logger.info("Triggering event", event.value)
    event_type = _github_contexts.github.enum.EventType
    event_to_handler = {
        event_type.ISSUES: _handler.IssuesEventHandler,
        event_type.ISSUE_COMMENT: _handler.IssueCommentEventHandler,
        event_type.PULL_REQUEST: _handler.PullRequestEventHandler,
        event_type.PULL_REQUEST_TARGET: _handler.PullRequestTargetEventHandler,
        event_type.PUSH: _handler.PushEventHandler,
        event_type.SCHEDULE: _handler.ScheduleEventHandler,
        event_type.WORKFLOW_DISPATCH: _handler.WorkflowDispatchEventHandler,
    }
    handler = event_to_handler.get(event)
    if not handler:
        supported_events = ", ".join([f"'{enum.value}'" for enum in event_to_handler.keys()])
        raise _exception.ProManInputError(
            "Unsupported workflow triggering event; "
            f"the ProMan action works with events of types {supported_events}, but got '{event.value}'."
        )
    _logger.info("Event handler", handler.__name__)
    return handler


@_logger.sectioner("Write Outputs and Summary", group=False)
def _write_outputs_and_summary(outputs: dict, summary: str) -> None:
    _write_step_outputs(kwargs=outputs)
    _write_step_summary(content=summary)
    return


@_logger.sectioner("Write Step Outputs")
def _write_step_outputs(kwargs: dict) -> None:
    for name, value in kwargs.items():
        output_name = name.lower().replace("_", "-")
        _logger.section(f"{output_name} [{type(value).__name__}]")
        _logger.debug(code_title="Value", code=value)
        written_output = _actionman.step_output.write(name=output_name, value=value)
        _logger.info("Success", f"Wrote step output variable '{output_name}'.")
        _logger.debug(code_title="Output", code=written_output)
        _logger.section_end()
    return


@_logger.sectioner("Write Step Summary")
def _write_step_summary(content: str) -> None:
    _logger.debug(code_title="Content", code=content)
    _actionman.step_summary.write(content)
    _logger.info("Success", f"Wrote step summary ({len(content)} characters).")
    return
