from __future__ import annotations as _annotations

import os
from typing import TYPE_CHECKING as _TYPE_CHECKING
from pathlib import Path

from loggerman import logger
import mdit
import pylinks as pl

from proman.manager.release.asset import create_releaseman_intput

if _TYPE_CHECKING:
    from proman.manager import Manager
    from proman.dstruct import VersionTag


class BinderReleaseManager:
    
    def __init__(self, manager: Manager):
        self._manager = manager
        return

    def trigger_build(self, ref_name: str):
        "https://gke.mybinder.org/build/gh/$GITHUB_REPOSITORY/$INPUT_MYBINDERORG_TAG"
