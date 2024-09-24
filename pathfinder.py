#!/usr/bin/env python3

import sys

import controlman
import actionman
from loggerman import logger
from proman.logger import initialize as proman_logger_initialize


def get_local_dir_paths():
    needs_sec = {"base": False, "head": False, "pre-commit": False}
    for repo_path, repo_name in (("repo_base", "base"), ("repo_head", "head")):
        try:
            meta = controlman.from_json_file(repo_path=repo_path)
        except controlman.exception.ControlManException:
            logger.warning(
                "Cache Load: Missing Project Metadata File",
                f"Could not find the metadata file for the {repo_name} repository.",
                logger.traceback(),
            )
        else:
            has_set = set_local_dir_path(metadata=meta, repo_path=repo_path, repo_name=repo_name)
            needs_sec[repo_name] = has_set
            if repo_name == "head":
                has_set = set_pre_commit_config_path(metadata=meta, repo_path=repo_path, repo_name=repo_name)
                needs_sec["pre-commit"] = has_set
    curr_section_num = 1
    first_sec = None
    for key in ("base", "head", "pre-commit"):
        if needs_sec[key]:
            actionman.step_output.write(f"secnum_cache_{key}", curr_section_num)
            if not first_sec:
                first_sec = key
            curr_section_num += 1
    return first_sec


def set_pre_commit_config_path(metadata, repo_path: str, repo_name: str) -> bool:
    key = "tool.pre-commit.config.file.path"
    pre_commit_config_path = metadata[key]
    if not pre_commit_config_path:
        logger.warning(
            "Cache Load: Missing Pre-Commit Config File Path",
            f"Could not find the key `{key}` in the metadata file of the {repo_name} repository. "
            "The pre-commit cache will not be restored.",
        )
        return False
    logger.info(
        "Cache Load: Pre-Commit Config Path",
        f"Located the pre-commit configuration filepath for the {repo_name} repository at `{pre_commit_config_path}`.",
    )
    actionman.step_output.write(f"pre_commit_config_path_{repo_name}", f"{repo_path}/{pre_commit_config_path}")
    return True


def set_local_dir_path(metadata, repo_path: str, repo_name: str) -> bool:
    local_dir_path = metadata["local.path"]
    if not local_dir_path:
        logger.warning(
            "Cache Load: Missing Local Directory Path"
            f"Could not find the key `local.path` in the metadata file of the {repo_name} repository. "
            "The local cache will not be restored."
        )
        return False
    logger.info(
        "Cache Load: Local Directory Path",
        f"Located the local directory path for the {repo_name} repository at `{local_dir_path}`.",
    )
    actionman.step_output.write(f"local_dirpath_{repo_name}", f"{repo_path}/{local_dir_path}")
    return True


if __name__ == "__main__":
    proman_logger_initialize()
    logger.section("Cache Load")
    first_sub_sec_key = get_local_dir_paths()
    if first_sub_sec_key:
        log_title = {
            "base": "Base Cache",
            "head": "Head Cache",
            "pre-commit": "Pre-Commit Cache",
        }
        logger.section(log_title[first_sub_sec_key])
    sys.exit(0)
