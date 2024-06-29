from typing import Literal
from pathlib import Path
import re

from loggerman import logger
import pyserials
from markitup import html, md, sgr
import gittidy
import pyshellman as _pyshellman

from proman import exception as _exception


@logger.sectioner("Run Hooks")
def run(
    git: gittidy.Git,
    ref_range: tuple[str, str] = None,
    action: Literal["report", "amend", "commit"] = "amend",
    commit_message: str = "",
    config: dict | str | Path = Path(".github/.pre-commit-config.yaml"),
):
    if isinstance(config, Path):
        config = Path(config).resolve()
        if not config.is_file():
            logger.critical(f"pre-commit config file not found at '{config}'.")
    if action not in ["report", "amend", "commit"]:
        logger.critical(
            f"Argument 'action' must be one of 'report', 'amend', or 'commit', but got '{action}'."
        )
    if action == "commit" and not commit_message:
        logger.critical("Argument 'commit_message' must be specified if action is 'commit'.")
    if ref_range and (
        not isinstance(ref_range, (tuple, list))
        or len(ref_range) != 2
        or not all(isinstance(ref, str) for ref in ref_range)
    ):
        logger.critical(
            f"Argument 'ref_range' must be a list or tuple of two strings, but got {ref_range}."
        )
    version_result = _pyshellman.run(
        command=["pre-commit", "--version"],
        raise_execution=False,
        raise_exit_code=False,
        raise_stderr=False,
        text_output=True,
    )
    if not version_result.succeeded:
        logger.critical("pre-commit is not installed.")
    else:
        logger.info("pre-commit version", version_result.output)

    hook_runner = PreCommitHooks(
        git=git,
        config=config,
        action=action,
        commit_message=commit_message,
        ref_range=ref_range,
    )
    try:
        output = hook_runner.run()
    except Exception as e:
        hook_runner.remove_temp_config_file()
        raise e
    hook_runner.remove_temp_config_file()
    return output


class PreCommitHooks:
    def __init__(
        self,
        git: gittidy.Git,
        config: dict | str | Path,
        action: Literal["report", "amend", "commit"] = "report",
        commit_message: str = "",
        ref_range: tuple[str, str] = None,
    ):
        self._git = git
        self._action = action
        self._commit_message = commit_message
        self._path_root = git.repo_path
        self._config_filepath, self._config_file_is_temp = self._process_config(config)
        if ref_range:
            self._from_ref, self._to_ref = ref_range
            scope = ["--from-ref", self._from_ref, "--to-ref", self._to_ref]
        else:
            self._from_ref = self._to_ref = None
            scope = ["--all-files"]
        self._command = [
            "pre-commit",
            "run",
            *scope,
            "--hook-stage",
            "manual",
            "--show-diff-on-failure",
            "--color=always",
            "--verbose",
            "--config",
            str(self._config_filepath),
        ]
        self._emoji = {"Passed": "✅", "Failed": "❌", "Skipped": "⏭️", "Modified": "✏️️"}
        self._commit_hash: str = ""
        return

    def _process_config(self, config: dict | str | Path) -> tuple[Path, bool]:
        if isinstance(config, Path):
            temp = False
            path = config
            return path, temp
        temp = True
        path = self._path_root.parent / ".__temporary_pre_commit_config__.yaml"
        config = (
            config if isinstance(config, str)
            else pyserials.write.to_yaml_string(data=config, end_of_file_newline=True)
        )
        with open(path, "w") as f:
            f.write(config)
        logger.info(code_title="Create temporary config file", code=path)
        logger.debug(code_title="Config file content", code=config)
        return path, temp

    def remove_temp_config_file(self):
        if self._config_file_is_temp:
            self._config_filepath.unlink(missing_ok=True)
            logger.info("Remove temporary pre-commit config file")
        return

    def run(self) -> dict:
        output, summary = self._run_check() if self._action == "report" else self._run_fix()
        output["summary"] = summary
        return output

    def _run_check(self):
        logger.info("Run mode", "Validation only")
        self._git.stash(include="all")
        output, result_line, details = self._run_hooks(validation_run=True)
        summary = self._create_summary(
            output=output,
            run_summary=[result_line],
            details=details,
        )
        self._git.discard_changes()
        self._git.stash_pop()
        return output, summary

    def _run_fix(self):
        logger.info("Run mode", "Fix and validation")
        logger.section("Fix Run")
        outputs_fix, summary_line_fix, details_fix = self._run_hooks(validation_run=False)
        if outputs_fix["passed"] or not outputs_fix["modified"]:
            summary = self._create_summary(
                output=outputs_fix, run_summary=[summary_line_fix], details=details_fix
            )
            logger.section_end()
            return outputs_fix, summary
        # There were fixes
        self._commit_hash = self._git.commit(
            message=self._commit_message,
            stage="all",
            amend=self._action == "amend",
            allow_empty=self._action == "amend",
        )
        logger.section_end()
        logger.section("Validation Run")
        outputs_validate, summary_line_validate, details_validate = self._run_hooks(validation_run=True)
        outputs_validate["modified"] = outputs_validate["modified"] or outputs_fix["modified"]
        outputs_validate["commit_hash"] = self._commit_hash
        run_summary = [summary_line_fix, summary_line_validate]
        details = html.ElementCollection([details_fix, details_validate])
        summary = self._create_summary(outputs_validate, run_summary, details)
        logger.section_end()
        return outputs_validate, summary

    def _run_hooks(self, validation_run: bool):
        shell_output = self._run_shell()
        if validation_run:
            self.remove_temp_config_file()
        results = _process_shell_output(shell_output)
        output, result_line, details = self._process_results(results, validation_run=validation_run)
        return output, result_line, details

    @logger.sectioner("Run Pre-Commit")
    def _run_shell(self) -> str:
        result = _pyshellman.run(command=self._command, cwd=self._path_root, raise_exit_code=False)
        error_intro = "An unexpected error occurred while running pre-commit hooks: "
        if result.error:
            self.remove_temp_config_file()
            raise _exception.ProManException(
                f"{error_intro}{result.error}"
            )
        out_plain = sgr.remove_format(result.output)
        for line in out_plain.splitlines():
            for prefix in ("An error has occurred", "An unexpected error has occurred", "[ERROR]"):
                if line.startswith(prefix):
                    self.remove_temp_config_file()
                    raise _exception.ProManException(f"{error_intro}{line}")
        return out_plain

    @logger.sectioner("Process Results")
    def _process_results(self, results: dict[str, dict], validation_run: bool):
        details_list = []
        count = {"Passed": 0, "Modified": 0, "Skipped": 0, "Failed": 0}
        for hook_id, result in results.items():
            if result["result"] == "Failed" and result["modified"]:
                result["result"] = "Modified"
            count[result["result"]] += 1
            summary = f"{self._emoji[result['result']]} {hook_id}"
            detail_list = html.ul(
                [
                    f"Description: {result['description']}",
                    f"Result: {result['result']} {result['message']}",
                    f"Modified Files: {result['modified']}",
                    f"Exit Code: {result['exit_code']}",
                    f"Duration: {result['duration']} s",
                ]
            )
            detail = html.ElementCollection([detail_list])
            if result["details"]:
                detail.append(md.code_block(result["details"]))
            details_block = html.details(content=detail, summary=summary)
            details_list.append(details_block)
        passed = count["Failed"] == 0 and count["Modified"] == 0
        modified = count["Modified"] != 0
        summary_title = "Validation Run" if validation_run else "Fix Run"
        summary_details = ", ".join([f"{count[key]} {key}" for key in count])
        summary_result = f'{self._emoji["Passed" if passed else "Failed"]} {"Pass" if passed else "Fail"}'
        result_line = f"{summary_title}: {summary_result} ({summary_details})"
        details = html.ElementCollection([html.h(4, summary_title), html.ul(details_list)])
        outputs = {"passed": passed, "modified": modified, "count": count}
        return outputs, result_line, details

    @logger.sectioner("Create Summary")
    def _create_summary(self, output: dict, run_summary: list, details):
        passed = output["passed"]
        modified = output["modified"]
        result_emoji = self._emoji["Passed" if passed else "Failed"]
        result_keyword = "Pass" if passed else "Fail"
        summary_result = f"{result_emoji} {result_keyword}"
        if modified:
            summary_result += " (modified files)"
        action_emoji = {"report": "📄", "commit": "💾", "amend": "📌"}[self._action]
        action_title = {"report": "Validate & Report", "commit": "Fix & Commit", "amend": "Fix & Amend"}[
            self._action
        ]
        scope = f"From ref. '{self._from_ref}' to ref. '{self._to_ref}'" if self._from_ref else "All files"
        summary_list = [
            f"Result: {summary_result}",
            f"Action: {action_emoji} {action_title}",
            f"Scope: {scope}",
            f"Runs: ",
            html.ul(run_summary),
        ]
        html_summary = html.ElementCollection(
            [
                html.h(2, "Hooks"),
                html.h(3, "Summary"),
                html.ul(summary_list),
                html.h(3, "Details"),
                details,
            ]
        )
        logger.info("Create HTML summary for pre-commit hooks run")
        logger.debug(code_title="Summary", code=html_summary)
        return html_summary


@logger.sectioner("Process Shell Output")
def _process_shell_output(output: str):
    pattern = re.compile(
        r"""
            ^(?P<description>[^\n]+?)
            \.{3,}
            (?P<message>[^\n]*(?=\(Passed|Failed|Skipped\))?)?
            (?P<result>Passed|Failed|Skipped)\n
            -\s*hook\s*id:\s*(?P<hook_id>[^\n]+)\n
            (-\s*duration:\s*(?P<duration>\d+\.\d+)s\n)?
            (-\s*exit\s*code:\s*(?P<exit_code>\d+)\n)?
            (-\s*files\s*were\s*modified\s*by\s*this\s*hook(?P<modified>\n))?
            (?P<details>(?:^(?![^\n]+?\.{3,}.*?(Passed|Failed|Skipped)).*\n)*)
        """,
        re.VERBOSE | re.MULTILINE,
    )
    matches = list(pattern.finditer(output))
    results = {}
    for match in matches:
        data = match.groupdict()
        data["duration"] = data["duration"] or "0"
        data["exit_code"] = data["exit_code"] or "0"
        data["modified"] = bool(match.group("modified"))
        data["details"] = data["details"].strip()
        if data["hook_id"] in results:
            logger.critical(f"Duplicate hook ID '{data['hook_id']}' found.")
        results[data["hook_id"]] = data
    logger.info("Extract results from pre-commit output")
    logger.debug(code_title="Results", code=results)
    return results
