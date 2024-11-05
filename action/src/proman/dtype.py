from __future__ import annotations as _annotations

from enum import Enum as _Enum
from typing import NamedTuple as _NamedTuple


class RepoDynamicsBotCommand(_Enum):
    CREATE_DEV_BRANCH = "create_dev_branch"


class _TitledEmoji(_NamedTuple):
    title: str
    emoji: str


class FileChangeType(_Enum):
    REMOVED = _TitledEmoji("Removed", "🔴")
    MODIFIED = _TitledEmoji("Modified", "🟣")
    BROKEN = _TitledEmoji("Broken", "🟠")
    ADDED = _TitledEmoji("Added", "🟢")
    UNMERGED = _TitledEmoji("Unmerged", "⚪️")
    UNKNOWN = _TitledEmoji("Unknown", "⚫")


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

