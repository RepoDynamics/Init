from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime
import copy

from loggerman import logger
import pylinks as pl
import pyserials as ps

import controlman

from proman.dstruct import Version, VersionTag

from proman.dtype import IssueStatus, ReleaseAction

if _TYPE_CHECKING:
    from typing import Literal
    from pathlib import Path
    from gittidy import Git
    from versionman.pep440_semver import PEP440SemVer
    from proman.manager.user import User
    from proman.manager import Manager
    from proman.dstruct import Branch


class BareVariableManager(ps.PropertyDict):

    def __init__(self, repo_path: Path):
        self._var_filepath = repo_path / controlman.const.FILEPATH_VARIABLES
        if self._var_filepath.exists():
            var = ps.read.json_from_file(self._var_filepath)
        else:
            var = {}
        super().__init__(var)
        self._read_var = copy.deepcopy(var)
        return

    def write_file(self) -> bool:
        if self.as_dict == self._read_var:
            return False
        self._var_filepath.write_text(
            ps.write.to_json_string(self.as_dict, sort_keys=True, indent=4).strip() + "\n",
            newline="\n"
        )
        return True


class VariableManager(BareVariableManager):

    def __init__(self, manager: Manager):
        self._manager = manager
        super().__init__(self._manager.git.repo_path)
        return

    def commit_changes(self) -> str:
        written = self.write_file()
        if not written:
            return ""
        commit = self._manager.commit.create_auto(id="vars_sync")
        return self._manager.git.commit(message=str(commit.conv_msg))
