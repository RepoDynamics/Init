import re

from controlman import const

from proman.data_manager import DataManager
from proman.datatype import FileChangeType, RepoFileType


def detect(data: DataManager, changes: dict):
    change_type_map = {
        "added": FileChangeType.ADDED,
        "deleted": FileChangeType.REMOVED,
        "modified": FileChangeType.MODIFIED,
        "unmerged": FileChangeType.UNMERGED,
        "unknown": FileChangeType.UNKNOWN,
        "broken": FileChangeType.BROKEN,
        "copied_to": FileChangeType.ADDED,
        "renamed_from": FileChangeType.REMOVED,
        "renamed_to": FileChangeType.ADDED,
        "copied_modified_to": FileChangeType.ADDED,
        "renamed_modified_from": FileChangeType.REMOVED,
        "renamed_modified_to": FileChangeType.ADDED,
    }
    full_info: list = []
    paths_abs, paths_start, paths_regex = _make_filetype_patterns(data)
    dynamic_files = _get_dynamic_file_paths(data)
    for change_type, changed_paths in changes.items():
        if change_type.startswith("copied") and change_type.endswith("from"):
            continue
        for path in changed_paths:
            typ, subtype = _determine_filetype(path, paths_abs, paths_start, paths_regex)
            is_dynamic = path in dynamic_files
            full_info.append((typ, subtype, change_type_map[change_type], is_dynamic, path))
    return full_info


def _make_filetype_patterns(data: DataManager):
    paths_abs = [
        (RepoFileType.CONFIG, "Metadata", const.FILEPATH_METADATA),
        (RepoFileType.CONFIG, "Git Ignore", const.FILEPATH_GITIGNORE),
        (RepoFileType.CONFIG, "Git Attributes", const.FILEPATH_GIT_ATTRIBUTES),
        (RepoFileType.CONFIG, "Citation", const.FILEPATH_CITATION_CONFIG),
    ]
    paths_start = [
        (RepoFileType.CC, "Custom Hook", f"{data["control.path"]}/{const.DIRNAME_CC_HOOK}/"),
    ]
    for key in ("pkg", "test"):
        key_data = data[key]
        if not key_data:
            continue
        path_root = key_data["path"]["root"]
        path_import = key_data["path"]["import"]
        filetype = RepoFileType.PKG_CONFIG if key == "pkg" else RepoFileType.TEST_CONFIG
        paths_abs.extend(
            [
                (filetype, "Typing Marker", f"{path_import}/{const.FILENAME_PACKAGE_TYPING_MARKER}"),
                (filetype, "Manifest", f"{path_root}/{const.FILENAME_PACKAGE_MANIFEST}"),
                (filetype, "PyProject", f"{path_root}/{const.FILENAME_PKG_PYPROJECT}"),
            ]
        )
    for key in ("pkg", "test", "web"):
        key_data = data[key]
        if not key_data:
            continue
        path_root = key_data["path"]["root"]
        path_source = key_data["path"]["source"]
        # Order matters; first add the subdirectories and then the root directory
        paths_start.extend(
            [
                (RepoFileType[f"{key.upper()}_SOURCE"], None, f"{path_root}/{path_source}/"),
                (RepoFileType[f"{key.upper()}_CONFIG"], None, f"{path_root}/"),
            ]
        )
    if data["theme.path"]:
        paths_start.append((RepoFileType.THEME, "–", f'{data["theme.path"]}/'))
    paths_regex = [
        (RepoFileType.CC, "Source", re.compile(rf"^{re.escape(data["control.path"])}/[^/]+\.(?i:y?aml)$")),
        (RepoFileType.ISSUE_FORM, None, re.compile(r"^\.github/ISSUE_TEMPLATE/(?!config\.ya?ml$)[^/]+\.(?i:y?aml)$")),
        (RepoFileType.ISSUE_TEMPLATE, None, re.compile(r"^\.github/ISSUE_TEMPLATE/[^/]+\.(?i:md)$")),
        (RepoFileType.CONFIG, "Issue Template Chooser", re.compile(r"^\.github/ISSUE_TEMPLATE/config\.(?i:y?aml)$")),
        (RepoFileType.PULL_TEMPLATE, None,
         re.compile(r"^(?:|\.github/|docs/)PULL_REQUEST_TEMPLATE/[^/]+(?:\.(txt|md|rst))?$")),
        (RepoFileType.PULL_TEMPLATE, "default",
         re.compile(r"^(?:|\.github/|docs/)pull_request_template(?:\.(txt|md|rst))?$")),
        (RepoFileType.DISCUSSION_FORM, None, re.compile(r"^\.github/DISCUSSION_TEMPLATE/[^/]+\.(?i:y?aml)$")),
        (RepoFileType.CONFIG, "Code Owners", re.compile(r"^(?:|\.github/|docs/)CODEOWNERS$")),
        (RepoFileType.CONFIG, "License", re.compile(r"^LICENSE(?:\.(txt|md|rst))?$")),
        (RepoFileType.CONFIG, "Funding", re.compile(r"^\.github/FUNDING\.(?i:y?aml)$")),
        (RepoFileType.README, "main", re.compile(r"^(?:|\.github/|docs/)README(?:\.(txt|md|rst|html))?$")),
        (RepoFileType.README, "–", re.compile(r"/README(?i:\.(txt|md|rst|html))?$")),
        (RepoFileType.HEALTH, None, re.compile(
            r"^(?:|\.github/|docs/)(?:(?i:CONTRIBUTING)|GOVERNANCE|SECURITY|SUPPORT|CODE_OF_CONDUCT)(?i:\.(txt|md|rst))?$")),
        (RepoFileType.WORKFLOW, None, re.compile(r"^\.github/workflows/[^/]+\.(?i:y?aml)$")),

    ]
    return paths_abs, paths_start, paths_regex


def _determine_filetype(
    path: str,
    paths_abs: list[tuple[RepoFileType, str, str]],
    paths_start: list[tuple[RepoFileType, str, str]],
    paths_regex: list[tuple[RepoFileType, str, re.Pattern]]
) -> tuple[RepoFileType, str | None]:
    for filetype, subtype, abs_path in paths_abs:
        if path == abs_path:
            return filetype, subtype
    for filetype, subtype, pattern in paths_regex:
        if pattern.search(path):
            return filetype, subtype
    for filetype, subtype, start_path in paths_start:
        if path.startswith(start_path):
            return filetype, subtype
    return RepoFileType.OTHER, "–"


def _get_dynamic_file_paths(data: DataManager) -> list[str]:
    dynamic_files = []
    for file_group in data.get("project.file", {}).values():
        dynamic_files.extend(list(file_group.values()))
    return dynamic_files


