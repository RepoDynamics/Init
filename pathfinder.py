#!/usr/bin/env python3
import json
from pathlib import Path
import sys
import shutil
import os


def get_local_dir():
    path_pathfile = Path(".path.json").resolve()
    msg = "setting local path to '.local'."
    if not path_pathfile.exists():
        print(f"Paths definition file does not exist at '{path_pathfile}'; {msg}")
        return ".local"
    paths = json.loads(path_pathfile.read_text())
    if not isinstance(paths, dict):
        print(f"Paths definition file '{path_pathfile}' is not a dictionary; {msg}")
        return ".local"
    if "dir" not in paths:
        print(f"Paths definition file '{path_pathfile}' does not contain 'dir' key; {msg}")
        return ".local"
    if not isinstance(paths["dir"], dict):
        print(f"Paths definition file's '{path_pathfile}' 'dir' key is not a dictionary; {msg}")
        return ".local"
    if "local" not in paths["dir"]:
        print(f"Paths definition file's '{path_pathfile}' 'dir' key does not contain 'local' key; {msg}")
        return ".local"
    if not isinstance(paths["dir"]["local"], str):
        print(f"Paths definition file's '{path_pathfile}' 'dir' key's 'local' key is not a string; {msg}")
        return ".local"
    print(f"Setting local path to '{paths['dir']['local']}'.")
    return paths["dir"]["local"]


def copy_requirements_file(action_path: str, local_path: str) -> str:
    source = Path(action_path) / "requirements.txt"
    destination = Path(local_path) / "repodynamics" / "requirements.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, destination)
    return str(destination)



if __name__ == "__main__":
    # Check if the necessary arguments are provided
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <option 1-4> 'Your string here'")
        sys.exit(1)
    requirements_path = copy_requirements_file(sys.argv[1], get_local_dir())
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        print(f"path_requirements={requirements_path}", file=fh)

