from typing import Optional, get_type_hints
import os
import json
import sys

from markitup import html, md


def workflow_context(github: dict) -> tuple[None, str]:
    _ = github.pop("token")
    payload_data = github.pop("event")
    context_details = html.details(
        content=md.code_block(json.dumps(dict(sorted(github.items())), indent=4), "json"),
        summary="🖥 GitHub Context",
    )
    payload_details = html.details(
        content=md.code_block(json.dumps(dict(sorted(payload_data.items())), indent=4), "json"),
        summary="🖥 Event Payload",
    )
    return None, f"{context_details}\n{payload_details}"


if __name__ == "__main__":

    def read_input(job_id: str) -> dict:
        """
        Parse inputs from environment variables.
        """
        params = get_type_hints(globals()[job_id])
        args = {}
        if not params:
            return args
        params.pop("return", None)
        for name, typ in params.items():
            param_env_name = f"RD__{job_id.upper()}__{name.upper()}"
            val = os.environ.get(param_env_name)
            if val is None:
                print(f"ERROR: Missing input: {param_env_name}")
                sys.exit(1)
            if typ is str:
                args[name] = val
            elif typ is bool:
                args[name] = val.lower() == "true"
            elif typ is dict:
                args[name] = json.loads(val, strict=False)
            else:
                print(f"ERROR: Unknown input type: {typ}")
                sys.exit(1)
        return args

    def write_output(values: dict) -> Optional[dict]:
        print("OUTPUTS:")
        print("--------")
        print(values)
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            for name, value in values.items():
                print(f"{name.replace('_', '-')}={value}", file=fh)
        return

    def write_summary(content: str) -> None:
        print("SUMMARY:")
        print("--------")
        print(content)
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            print(content, file=fh)
        return

    kwargs = read_input(job_id="workflow_context")
    try:
        outputs, summary = workflow_context(**kwargs)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    if outputs:
        write_output(values=outputs)
    if summary:
        write_summary(content=summary)
