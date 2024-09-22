#!/usr/bin/env python3

import sys

import controlman
import actionman


def get_local_dir_paths():
    for repo_path in ("repo_base", "repo_head"):
        meta = controlman.from_json_file(repo_path=repo_path)
        local_dir_path = meta["local"]["path"]
        actionman.step_output.write(f"local_dirpath_{repo_path}", local_dir_path)
    return

if __name__ == "__main__":
    get_local_dir_paths()
    sys.exit(0)
