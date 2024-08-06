from enum import Enum as _Enum
from typing import NamedTuple as _NamedTuple

import conventional_commits as _conventional_commits
from versionman import PEP440SemVer as _PEP440SemVer


class TemplateType(_Enum):
    PYPACKIT = "PyPackIT"
    SPHINXIT = "SphinxIT"


class RepoDynamicsBotCommand(_Enum):
    CREATE_DEV_BRANCH = "create_dev_branch"


class _FileStatus(_NamedTuple):
    title: str
    emoji: str


class FileChangeType(_Enum):
    REMOVED = _FileStatus("Removed", "🔴")
    MODIFIED = _FileStatus("Modified", "🟣")
    BROKEN = _FileStatus("Broken", "🟠")
    ADDED = _FileStatus("Added", "🟢")
    UNMERGED = _FileStatus("Unmerged", "⚪️")
    UNKNOWN = _FileStatus("Unknown", "⚫")


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













class EventType(_Enum):
    PUSH_MAIN = "push_main"
    PUSH_RELEASE = "push_release"
    PUSH_DEV = "push_dev"
    PUSH_CI_PULL = "push_ci_pull"
    PUSH_OTHER = "push_other"
    PULL_MAIN = "pull_main"
    PULL_RELEASE = "pull_release"
    PULL_DEV = "pull_dev"
    PULL_OTHER = "pull_other"
    SCHEDULE = "schedule"
    DISPATCH = "dispatch"





class Branch(_NamedTuple):
    type: BranchType
    name: str
    prefix: str | None = None
    suffix: str | int | _PEP440SemVer | tuple[int, str] | tuple[int, str, int] | None = None


class CommitGroup(_Enum):
    PRIMARY_ACTION = "primary_action"
    PRIMARY_CUSTOM = "primary_custom"
    SECONDARY_ACTION = "secondary_action"
    SECONDARY_CUSTOM = "secondary_custom"
    NON_CONV = "non_conventional"


class PrimaryActionCommitType(_Enum):
    RELEASE_MAJOR = "release_major"
    RELEASE_MINOR = "release_minor"
    RELEASE_PATCH = "release_patch"
    RELEASE_POST = "release_post"
    WEBSITE = "website"
    META = "meta"


class SecondaryActionCommitType(_Enum):
    AUTO_UPDATE = "auto-update"
    META_SYNC = "meta_sync"
    REVERT = "revert"
    HOOK_FIX = "hook_fix"


class GroupedCommit:
    def __init__(self, group: CommitGroup):
        self._group = group
        return

    @property
    def group(self) -> CommitGroup:
        return self._group


class PrimaryActionCommit(GroupedCommit):
    def __init__(
        self,
        action: PrimaryActionCommitType,
        conv_type: str,
    ):
        super().__init__(CommitGroup.PRIMARY_ACTION)
        self._action = action
        self._conv_type = conv_type
        return

    @property
    def action(self) -> PrimaryActionCommitType:
        return self._action

    @property
    def conv_type(self) -> str:
        return self._conv_type

    def __repr__(self):
        return f"PrimaryActionCommit(action={self.action}, conv_type={self.conv_type})"


class PrimaryCustomCommit(GroupedCommit):
    def __init__(self, group_id: str, conv_type: str):
        super().__init__(CommitGroup.PRIMARY_CUSTOM)
        self._conv_type = conv_type
        self._id = group_id
        return

    @property
    def id(self) -> str:
        return self._id

    @property
    def conv_type(self) -> str:
        return self._conv_type

    def __repr__(self):
        return f"PrimaryCustomCommit(id={self.id}, conv_type={self.conv_type})"


class SecondaryActionCommit(GroupedCommit):
    def __init__(self, action: SecondaryActionCommitType, conv_type: str):
        super().__init__(CommitGroup.SECONDARY_ACTION)
        self._action = action
        self._conv_type = conv_type
        return

    @property
    def action(self) -> SecondaryActionCommitType:
        return self._action

    @property
    def conv_type(self) -> str:
        return self._conv_type

    def __repr__(self):
        return f"SecondaryActionCommit(action={self.action}, conv_type={self.conv_type})"


class SecondaryCustomCommit(GroupedCommit):
    def __init__(self, conv_type: str, changelog_id: str, changelog_section_id: str):
        super().__init__(CommitGroup.SECONDARY_CUSTOM)
        self._conv_type = conv_type
        self._changelog_id = changelog_id
        self._changelog_section_id = changelog_section_id
        return

    @property
    def conv_type(self) -> str:
        return self._conv_type

    @property
    def changelog_id(self) -> str:
        return self._changelog_id

    @property
    def changelog_section_id(self) -> str:
        return self._changelog_section_id

    def __repr__(self):
        return (
            f"SecondaryCustomCommit("
            f"conv_type={self.conv_type}, changelog_id={self.changelog_id}, "
            f"changelog_section_id={self.changelog_section_id})"
        )


class NonConventionalCommit(GroupedCommit):
    def __init__(self):
        super().__init__(CommitGroup.NON_CONV)
        return

    def __repr__(self):
        return "NonConventionalCommit()"


class Commit(_NamedTuple):
    hash: str
    author: str
    date: str
    files: list[str]
    msg: str | _conventional_commits.message.ConventionalCommitMessage
    group_data: (
        PrimaryActionCommit
        | PrimaryCustomCommit
        | SecondaryActionCommit
        | SecondaryCustomCommit
        | NonConventionalCommit
    )


class Issue(_NamedTuple):
    group_data: PrimaryActionCommit | PrimaryCustomCommit
    type_labels: list[str]
    form: dict


class InitCheckAction(_Enum):
    NONE = "none"
    FAIL = "fail"
    REPORT = "report"
    PULL = "pull"
    COMMIT = "commit"
    AMEND = "amend"


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


class LabelType(_Enum):
    VERSION = "version"
    BRANCH = "branch"
    TYPE = "primary_type"
    SUBTYPE = "subtype"
    STATUS = "status"
    CUSTOM_GROUP = "custom_group"
    SINGLE = "single"
    UNKNOWN = "unknown"


class Label(_NamedTuple):
    category: LabelType
    name: str
    prefix: str = ""
    type: PrimaryActionCommitType | IssueStatus | str = None

    @property
    def suffix(self) -> str:
        return self.name.removeprefix(self.prefix)


class Emoji:
    """Enum of emojis used in the bot."""

    _db = {
        "PASS": "✅",
        "SKIP": "❎",
        "FAIL": "❌",
        "WARNING": "⚠️",
        "PLAY": "▶️",
    }

    def __init__(self):
        for name, emoji in self._db.items():
            setattr(self, name, emoji)
        return

    def __getitem__(self, item: str):
        return self._db[item.upper()]


Emoji = Emoji()
