from __future__ import annotations as _annotations
from enum import Enum as _Enum
from typing import NamedTuple as _NamedTuple, TYPE_CHECKING as _TYPE_CHECKING
from dataclasses import dataclass as _dataclass

from versionman.pep440_semver import PEP440SemVer as _PEP440SemVer
import pycolorit as _pcit

if _TYPE_CHECKING:
    from proman.commit_manager import Commit
    from proman.user_manager import User


class RepoDynamicsBotCommand(_Enum):
    CREATE_DEV_BRANCH = "create_dev_branch"


class TitledEmoji(_NamedTuple):
    title: str
    emoji: str


class FileChangeType(_Enum):
    REMOVED = TitledEmoji("Removed", "🔴")
    MODIFIED = TitledEmoji("Modified", "🟣")
    BROKEN = TitledEmoji("Broken", "🟠")
    ADDED = TitledEmoji("Added", "🟢")
    UNMERGED = TitledEmoji("Unmerged", "⚪️")
    UNKNOWN = TitledEmoji("Unknown", "⚫")


class RepoFileType(_Enum):
    DYNAMIC = "Dynamic"
    CC = "Control Center"

    CONFIG = "Configuration"

    PKG_CONFIG = "Package Configuration"
    PKG_SOURCE = "Package Source"

    TEST_CONFIG = "Test Suite Configuration"
    TEST_SOURCE = "Test Suite Source"

    WEB_CONFIG = "Website Configuration"
    WEB_SOURCE = "Website Source"

    THEME = "Media"

    DISCUSSION_FORM = "Discussion Category Form"
    ISSUE_FORM = "Issue Form"
    ISSUE_TEMPLATE = "Issue Template"
    PULL_TEMPLATE = "Pull Request Template"

    README = "ReadMe"
    HEALTH = "Community Health"

    WORKFLOW = "Workflow"

    OTHER = "Other"


class BranchType(_Enum):
    MAIN = "main"
    RELEASE = "release"
    PRE = "pre"
    DEV = "dev"
    AUTO = "auto"
    OTHER = "other"


class InitCheckAction(_Enum):
    NONE = "none"
    FAIL = "fail"
    REPORT = "report"
    PULL = "pull"
    COMMIT = "commit"
    AMEND = "amend"


class Branch(_NamedTuple):
    type: BranchType
    name: str
    prefix: str | None = None
    suffix: str | int | _PEP440SemVer | tuple[int, str] | tuple[int, str, int] | None = None


class ReleaseAction(_Enum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    POST = "post"


class IssueStatus(_Enum):
    TRIAGE = "triage"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    PLANNING = "planning"
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    DEPLOY_ALPHA = "deploy_alpha"
    DEPLOY_BETA = "deploy_beta"
    DEPLOY_RC = "deploy_rc"
    DEPLOY_FINAL = "deploy_final"

    @property
    def level(self) -> int:
        level = {
            IssueStatus.TRIAGE: 0,
            IssueStatus.REJECTED: 1,
            IssueStatus.DUPLICATE: 1,
            IssueStatus.INVALID: 1,
            IssueStatus.PLANNING: 2,
            IssueStatus.REQUIREMENT_ANALYSIS: 3,
            IssueStatus.DESIGN: 4,
            IssueStatus.IMPLEMENTATION: 5,
            IssueStatus.TESTING: 6,
            IssueStatus.DEPLOY_ALPHA: 7,
            IssueStatus.DEPLOY_BETA: 8,
            IssueStatus.DEPLOY_RC: 9,
            IssueStatus.DEPLOY_FINAL: 10,
        }
        return level[self]


class LabelType(_Enum):
    STATUS = "status"
    VERSION = "version"
    BRANCH = "branch"
    CUSTOM_GROUP = "custom_group"
    CUSTOM_SINGLE = "custom_single"
    UNKNOWN = "unknown"


@_dataclass(frozen=True)
class Label:
    """GitHub Issues Label.

    Attributes
    ----------
    category : LabelType
        Label category.
    name : str
        Full name of the label.
    group_id : str
        Key of the custom group.
        Only available if `category` is `LabelType.CUSTOM_GROUP`.
    id : IssueStatus | str
        Key of the label.
        Only available if `category` is not `LabelType.BRANCH`, `LabelType.VERSION`, or `LabelType.UNKNOWN`.
        For `LabelType.STATUS`, it is a `IssueStatus` enum.
    """
    category: LabelType
    name: str
    group_id: str = ""
    id: IssueStatus | str = ""
    prefix: str = ""
    suffix: str = ""
    color: str = ""
    description: str = ""

    def __post_init__(self):
        if self.category == LabelType.STATUS and not isinstance(self.id, IssueStatus):
            object.__setattr__(self, "id", IssueStatus(self.id))
        if self.color:
            object.__setattr__(self, "color", _pcit.color.css(self.color).css_hex().removeprefix("#"))
        return


class IssueForm(_NamedTuple):
    id: str
    commit: Commit
    id_labels: list[Label]
    issue_assignees: list[User]
    pull_assignees: list[User]
    review_assignees: list[User]
    labels: list[Label]
    pre_process: dict
    post_process: dict
    name: str
    description: str
    projects: list[str]
    title: str
    body: list[dict]
