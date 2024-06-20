import actionman as _actionman
import github_contexts as _github_contexts
from loggerman import logger as _logger

from proman.datatype import TemplateType as _TemplateType
from proman import exception as _exception, handler as _handler


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
    template_type = _get_template_type(input_template_type=inputs.pop("TEMPLATE_TYPE"))
    context_manager = _github_contexts.GitHubContext(context=inputs.pop("GITHUB_CONTEXT"))
    event_handler_class = _get_event_handler(event=context_manager.event_name)
    event_handler = event_handler_class(
        template_type=template_type,
        context_manager=context_manager,
        admin_token=inputs["ADMIN_TOKEN"] or "",
        path_repo_base=inputs["PATH_REPO_BASE"],
        path_repo_head=inputs["PATH_REPO_HEAD"],
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
        ("Template Type", "TEMPLATE_TYPE", str,),
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


@_logger.sectioner("Verify Template Type")
def _get_template_type(input_template_type: str) -> _TemplateType:
    """Parse and verify the input template type."""
    try:
        template_type = _TemplateType(input_template_type)
    except ValueError:
        supported_templates = ", ".join([f"'{enum.value}'" for enum in _TemplateType])
        raise _exception.ProManInputError(
            "Template type verification failed; "
            f"the 'template' input argument of ProMan action must be one of {supported_templates},"
            f"but got '{input_template_type}'. "
        )
    _logger.info("Template type", template_type.value)
    return template_type


@_logger.sectioner("Verify Triggering Event")
def _get_event_handler(event: _github_contexts.github.enums.EventType):
    _logger.info("Triggering event", event.value)
    event_type = _github_contexts.github.enums.EventType
    event_to_handler = {
        event_type.ISSUES: _handler.event.IssuesEventHandler,
        event_type.ISSUE_COMMENT: _handler.event.IssueCommentEventHandler,
        event_type.PULL_REQUEST: _handler.event.PullRequestEventHandler,
        event_type.PULL_REQUEST_TARGET: _handler.event.PullRequestTargetEventHandler,
        event_type.PUSH: _handler.event.PushEventHandler,
        event_type.SCHEDULE: _handler.event.ScheduleEventHandler,
        event_type.WORKFLOW_DISPATCH: _handler.event.WorkflowDispatchEventHandler,
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
