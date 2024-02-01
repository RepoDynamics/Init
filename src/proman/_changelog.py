from pathlib import Path
import re
import datetime

from repodynamics.logger import Logger


class ChangelogManager:
    def __init__(
        self,
        path_root: str,
        changelog_metadata: dict,
        ver_dist: str,
        commit_type: str,
        commit_title: str,
        parent_commit_hash: str,
        parent_commit_url: str,
        logger: Logger = None,
    ):
        self._meta = changelog_metadata
        self._vars = {
            "ver_dist": ver_dist,
            "date": datetime.date.today().strftime("%Y.%m.%d"),
            "commit_type": commit_type,
            "commit_title": commit_title,
            "parent_commit_hash": parent_commit_hash,
            "parent_commit_url": parent_commit_url,
        }
        self._path_root = Path(path_root).resolve()
        self._logger = logger or Logger("github")
        self._name_to_id = {v["name"]: k for k, v in self._meta.items()}
        self._changes = {}
        return

    def add_change(self, changelog_id: str, section_id: str, change_title: str, change_details: str):
        if changelog_id not in self._meta:
            self._logger.error(f"Invalid changelog ID: {changelog_id}")
        changelog_dict = self._changes.setdefault(changelog_id, {})
        if not isinstance(changelog_dict, dict):
            self._logger.error(
                f"Changelog {changelog_id} is already updated with an entry; cannot add individual changes."
            )
        for section_idx, section in enumerate(
            self._meta[
                changelog_id if changelog_id != "package_public_prerelease" else "package_public"
            ]["sections"]
        ):
            if section["id"] == section_id:
                section_dict = changelog_dict.setdefault(
                    section_idx, {"title": section["title"], "changes": []}
                )
                section_dict["changes"].append({"title": change_title, "details": change_details})
                break
        else:
            self._logger.error(f"Invalid section ID: {section_id}")
        return

    def add_entry(self, changelog_id: str, sections: str):
        if changelog_id not in self._meta:
            self._logger.error(f"Invalid changelog ID: {changelog_id}")
        if changelog_id in self._changes:
            self._logger.error(
                f"Changelog {changelog_id} is already updated with an entry; cannot add new entry."
            )
        self._changes[changelog_id] = sections
        return

    def add_from_commit_body(self, body: str):
        heading_pattern = r"^#\s+(.*?)\n"
        sections = re.split(heading_pattern, body, flags=re.MULTILINE)
        for i in range(1, len(sections), 2):
            heading = sections[i]
            content = sections[i + 1]
            if not heading.startswith("Changelog: "):
                continue
            changelog_name = heading.removeprefix("Changelog: ").strip()
            changelog_id = self._name_to_id.get(changelog_name)
            if not changelog_id:
                self._logger.error(f"Invalid changelog name: {changelog_name}")
            self.add_entry(changelog_id, content)
        return

    def write_all_changelogs(self):
        for changelog_id in self._changes:
            self.write_changelog(changelog_id)
        return

    def write_changelog(self, changelog_id: str):
        if changelog_id not in self._changes:
            return
        changelog = self.get_changelog(changelog_id)
        with open(self._path_root / self._meta[changelog_id]["path"], "w") as f:
            f.write(changelog)
        return

    def get_changelog(self, changelog_id: str) -> str:
        if changelog_id not in self._changes:
            return ""
        path = self._path_root / self._meta[changelog_id]["path"]
        if not path.exists():
            title = f"# {self._meta[changelog_id]['title']}"
            intro = self._meta[changelog_id]["intro"].strip()
            text_before = f"{title}\n\n{intro}"
            text_after = ""
        else:
            with open(path) as f:
                text = f.read()
            parts = re.split(r"^## ", text, maxsplit=1, flags=re.MULTILINE)
            if len(parts) == 2:
                text_before, text_after = parts[0].strip(), f"## {parts[1].strip()}"
            else:
                text_before, text_after = text.strip(), ""
        entry, _ = self.get_entry(changelog_id)
        changelog = f"{text_before}\n\n{entry.strip()}\n\n{text_after}".strip() + "\n"
        return changelog

    def get_all_entries(self) -> list[tuple[str, str]]:
        return [self.get_entry(changelog_id) for changelog_id in self.open_changelogs]

    def get_entry(self, changelog_id: str) -> tuple[str, str]:
        if changelog_id not in self._changes:
            return "", ""
        entry_sections, needs_intro = self.get_sections(changelog_id)
        if needs_intro:
            entry_title = self._meta[changelog_id]["entry"]["title"].format(**self._vars).strip()
            entry_intro = self._meta[changelog_id]["entry"]["intro"].format(**self._vars).strip()
            entry = f"## {entry_title}\n\n{entry_intro}\n\n{entry_sections}"
        else:
            entry = entry_sections
        changelog_name = self._meta[changelog_id]["name"]
        return entry, changelog_name

    def get_sections(self, changelog_id: str) -> tuple[str, bool]:
        if changelog_id not in self._changes:
            return "", False
        if isinstance(self._changes[changelog_id], str):
            return self._changes[changelog_id], False
        changelog_dict = self._changes[changelog_id]
        sorted_sections = [value for key, value in sorted(changelog_dict.items())]
        sections_str = ""
        for section in sorted_sections:
            sections_str += f"### {section['title']}\n\n"
            for change in section["changes"]:
                sections_str += f"#### {change['title']}\n\n{change['details']}\n\n"
        return sections_str.strip() + "\n", True

    @property
    def open_changelogs(self) -> tuple[str]:
        return tuple(self._changes.keys())
