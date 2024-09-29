from typing import Literal
from pathlib import Path
import re

from loggerman import logger
import pyserials
import mdit
import gittidy
import pyshellman as _pyshellman
import ansi_sgr as sgr

from proman import exception as _exception


def run(
    git: gittidy.Git,
    config: dict,
    ref_range: tuple[str, str] = None,
    action: Literal["report", "amend", "commit"] = "amend",
    commit_message: str = "",
):
    assert action in ["report", "amend", "commit"], f"Invalid action '{action}'."
    if action == "commit":
        assert bool(commit_message), "Argument 'commit_message' must be specified if action is 'commit'."
    if ref_range:
        assert (
            isinstance(ref_range, (tuple, list))
            and len(ref_range) != 2
            and all(isinstance(ref, str) for ref in ref_range)
        ), f"Argument 'ref_range' must be a list or tuple of two strings, but got {ref_range}."
    version_result = _pyshellman.run(
        command=["pre-commit", "--version"],
        raise_execution=False,
        raise_exit_code=False,
        raise_stderr=False,
        text_output=True,
    )
    logger.log(
        "success" if version_result.succeeded else "critical",
        "Pre-Commit: Check Version",
        version_result.report(),
    )
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
        config: dict,
        action: Literal["report", "amend", "commit"] = "report",
        commit_message: str = "",
        ref_range: tuple[str, str] = None,
    ):
        self._git = git
        self._action = action
        self._commit_message = commit_message
        self._path_root = git.repo_path
        self._config_filepath = self._process_config(config)
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
        self._shell_runner = _pyshellman.Runner(
            pre_command=self._command,
            cwd=self._path_root,
            raise_exit_code=False,
            logger=logger,
            stack_up=1,
        )
        self._emoji = {"Passed": "✅", "Failed": "❌", "Skipped": "⏭️", "Modified": "✏️️"}
        self._dropdown_color = {"Passed": "success", "Failed": "danger", "Skipped": "muted", "Modified": "warning"}
        self._commit_hash: str = ""
        return

    def _process_config(self, config: dict | str | Path) -> Path:
        path = self._path_root.parent / ".__temporary_pre_commit_config__.yaml"
        config = pyserials.write.to_yaml_string(data=config)
        with open(path, "w") as f:
            f.write(config)
        return path

    def remove_temp_config_file(self):
        self._config_filepath.unlink(missing_ok=True)
        return

    def run(self) -> dict:
        return self._run_check() if self._action == "report" else self._run_fix()

    def _run_check(self):
        logger.info("Run Mode", "Validation only")
        self._git.stash(include="all")
        raw_output = self._run_hooks(validation_run=True)
        output = self._create_summary(output_validation=raw_output)
        self._git.discard_changes()
        self._git.stash_pop()
        return output

    def _run_fix(self):
        logger.info("Run Mode", "Fix and validation")
        output_fix = self._run_hooks(validation_run=False)
        if output_fix["passed"] or not output_fix["modified"]:
            output = self._create_summary(output_fix=output_fix)
            return output
        # There were fixes
        self._commit_hash = self._git.commit(
            message=self._commit_message,
            stage="all",
            amend=self._action == "amend",
            allow_empty=self._action == "amend",
        )
        output_validate = self._run_hooks(validation_run=True)
        output_validate["commit_hash"] = self._commit_hash
        output = self._create_summary(output_validation=output_validate, output_fix=output_fix)
        return output

    def _run_hooks(self, validation_run: bool) -> dict:
        result = self._shell_runner.run(
            command=[],
            log_title=f"{"Validation" if validation_run else "Fix"} Run",
            log_level_exit_code="error" if validation_run else "notice",
        )
        error_intro = "Unexpected Pre-Commit Error"
        if result.err:
            self.remove_temp_config_file()
            logger.critical(error_intro, result.err)
            raise _exception.ProManException(error_intro, sgr.remove_sequence(result.err))
        out_plain = sgr.remove_sequence(result.out)
        for line in out_plain.splitlines():
            for prefix in ("An error has occurred", "An unexpected error has occurred", "[ERROR]"):
                if line.startswith(prefix):
                    self.remove_temp_config_file()
                    logger.critical(error_intro, out_plain)
                    raise _exception.ProManException(error_intro, out_plain)
        if validation_run:
            self.remove_temp_config_file()
        results = _process_shell_output(out_plain)
        return self._process_results(results, validation_run=validation_run)

    def _process_results(self, results: tuple[dict[str, dict], str], validation_run: bool) -> dict:
        hook_details = []
        count = {"Failed": 0, "Modified": 0, "Skipped": 0, "Passed": 0}
        for hook_id, result in results[0].items():
            if result["result"] == "Failed" and result["modified"]:
                result["result"] = "Modified"
            count[result["result"]] += 1
            result_str = f"{result['result']} {result['message']}" if result["message"] else result["result"]
            detail_list = mdit.element.field_list(
                [
                    ("Result", result_str),
                    ("Modified Files", str(result['modified'])),
                    ("Exit Code", result['exit_code']),
                    ("Duration", result['duration']),
                    ("Description", result['description']),
                ]
            )
            dropdown_elements = mdit.block_container(detail_list)
            if result["details"]:
                dropdown_elements.append(mdit.element.code_block(result["details"], caption="Details"), conditions=["full"])
            dropdown = mdit.element.dropdown(
                title=hook_id,
                body=dropdown_elements,
                color=self._dropdown_color[result["result"]],
                icon=self._emoji[result["result"]],
                opened=result["result"] == "Failed",
            )
            hook_details.append(dropdown)
        passed = count["Failed"] == 0 and count["Modified"] == 0
        summary_details = ", ".join([f"{count[key]} {key}" for key in count])
        doc = mdit.document(
            heading="Validation Run" if validation_run else "Fix Run",
            body=[f"{self._emoji["Passed" if passed else "Failed"]} {summary_details}"] + hook_details,
        )
        if results[1]:
            git_diff = mdit.element.code_block(results[1], language="diff")
            admo = mdit.element.admonition(title="Git Diff", body=git_diff, type="note", dropdown=True)
            doc.body.append(mdit.element.thematic_break(), conditions=["full"])
            doc.body.append(admo, conditions=["full"])
        output = {
            "passed": passed,
            "modified": count["Modified"] != 0,
            "count": count,
            "report": doc,
        }
        return output

    def _create_summary(self, output_validation: dict = None, output_fix: dict = None) -> dict:
        if output_validation and not output_fix:
            output = output_validation
            outputs = [output_validation]
        elif output_fix and not output_validation:
            output = output_fix
            outputs = [output_fix]
        else:
            output = output_validation
            output["modified"] = output["modified"] or output_fix["modified"]
            output["count"]["Modified (2nd Run)"] = output["count"]["Modified"]
            output["count"]["Modified"] = output_fix["count"]["Modified"]
            outputs = [output_fix, output_validation]

        summary_parts = []
        for mode, mode_count in output["count"].items():
            if mode_count:
                summary_parts.append(f"{mode_count} {mode}")
        summary = f"{", ".join(summary_parts)}."

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
        scope = f"From ref. <code>{self._from_ref}</code> to ref. <code>{self._to_ref}</code>" if self._from_ref else "All files"
        body = mdit.element.field_list(
            [
                ("Result", summary_result),
                ("Action", f"{action_emoji} {action_title}"),
                ("Scope", scope),
            ]
        )
        final_output = {
            "passed": passed,
            "modified": modified,
            "summary": summary,
            "body": body,
            "section": [output["report"] for output in outputs],
        }
        return final_output


def _process_shell_output(output: str) -> tuple[dict[str, dict[str, str | bool]], str]:

    def process_last_entry(details: str) -> tuple[str, str]:
        """Process the last entry in the hook output.

        The last entry in the output does not have a trailing separator,
        and pre-commit adds extra details at the end. These details are
        not part of the last hook's details, so they are separated out.

        References
        ----------
        - [Pre-commit run source code](https://github.com/pre-commit/pre-commit/blob/de8590064e181c0ad45d318a0c80db605bf62a60/pre_commit/commands/run.py#L303-L319)
        """
        info_text = (
            '\npre-commit hook(s) made changes.\n'
            'If you are seeing this message in CI, '
            'reproduce locally with: `pre-commit run --all-files`.\n'
            'To run `pre-commit` as part of git workflow, use '
            '`pre-commit install`.'
        )
        details.replace(info_text, "")
        parts = details.split("\nAll changes made by hooks:\n")
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""

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
    git_diff = ""
    for idx, match in enumerate(matches):
        data = match.groupdict()
        data["duration"] = data["duration"] or "0"
        data["exit_code"] = data["exit_code"] or "0"
        data["modified"] = bool(match.group("modified"))
        if idx + 1 != len(matches):
            data["details"] = data["details"].strip()
        else:
            data["details"], git_diff = process_last_entry(data["details"])
        if data["hook_id"] in results:
            logger.critical(f"Duplicate hook ID '{data['hook_id']}' found.")
        results[data["hook_id"]] = data
    logger.debug("Results", logger.pretty(results))
    return results, git_diff
