from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime

from loggerman import logger
import pylinks as pl
import pyserials as ps

from proman.dstruct import Version, VersionTag

if _TYPE_CHECKING:
    from gittidy import Git
    from versionman.pep440_semver import PEP440SemVer
    from proman.manager.user import User
    from proman.manager import Manager
    from proman.dstruct import Branch
    from proman.dtype import ReleaseAction


class ReleaseManager:
    
    def __init__(self, manager: Manager):
        self._manager = manager
        return

    def run(
        self,
        version: PEP440SemVer,
        contributors: list[User] | None = None,
        embargo_date: str | None = None,
    ):
        zenodo_output = None
        doi = None
        data = self._manager.data_branch
        if data["release.zenodo"] and self._manager.zenodo_token:
            zenodo_response = self._create_zenodo_depo(
                version=version,
                contributors=contributors,
                embargo_date=embargo_date,
            )
            doi = zenodo_response["metadata"]["prereserve_doi"]["doi"]
            zenodo_output = {
                "id": zenodo_response["id"],
                "created": zenodo_response["created"],
                "doi": doi,
                "links": zenodo_response["links"],
            }
        if data["citation"]:
            self._prepare_citation_for_release(version=version, doi=doi)
        return zenodo_output

    def latest_version(
        self,
        git: Git | None = None,
        branch: Branch | str | None = None,
        dev_only: bool = False,
    ) -> Version | None:

        def get_latest_version() -> PEP440SemVer | None:
            tags_lists = git.get_tags()
            if not tags_lists:
                return
            for tags_list in tags_lists:
                ver_tags = []
                for tag in tags_list:
                    if tag.startswith(ver_tag_prefix):
                        ver_tags.append(PEP440SemVer(tag.removeprefix(ver_tag_prefix)))
                if ver_tags:
                    if dev_only:
                        ver_tags = sorted(ver_tags, reverse=True)
                        for ver_tag in ver_tags:
                            if ver_tag.release_type == "dev":
                                return ver_tag
                    else:
                        return max(ver_tags)
            return

        git = git or self._manager.git
        ver_tag_prefix = self._manager.data["tag.version.prefix"]
        if branch:
            git.stash()
            curr_branch = git.current_branch_name()
            branch_name = branch if isinstance(branch, str) else branch.name
            git.checkout(branch=branch_name)
        latest_version = get_latest_version()
        distance = git.get_distance(
            ref_start=f"refs/tags/{ver_tag_prefix}{latest_version.input}"
        ) if latest_version else None
        if branch:
            git.checkout(branch=curr_branch)
            git.stash_pop()
        if not latest_version and not dev_only:
            logger.error(f"No matching version tags found with prefix '{ver_tag_prefix}'.")
        if not latest_version:
            return
        return Version(public=latest_version, local=(distance,) if distance else None)

    def tag_version(
        self,
        ver: str | PEP440SemVer,
        env_vars: dict | None = None,
        git: Git | None = None,
    ) -> VersionTag:
        tag_data = self._manager.data["tag.version"]
        prefix = tag_data["prefix"]
        tag = f"{prefix}{ver}"
        msg = self._manager.fill_jinja_template(
            tag_data["message"],
            {"version": ver} | (env_vars or {}),
        )
        git = git or self._manager.git
        git.create_tag(tag=tag, message=msg)
        return VersionTag(tag_prefix=prefix, version=ver)

    @staticmethod
    def next_version(version: PEP440SemVer, action: ReleaseAction) -> PEP440SemVer:
        if action is ReleaseAction.MAJOR:
            if version.major == 0:
                return version.next_minor
            return version.next_major
        if action == ReleaseAction.MINOR:
            if version.major == 0:
                return version.next_patch
            return version.next_minor
        if action == ReleaseAction.PATCH:
            return version.next_patch
        if action == ReleaseAction.POST:
            return version.next_post
        return version

    def _create_zenodo_depo(
        self, version: PEP440SemVer, contributors: list[User], embargo_date: str | None = None
    ):
        # https://developers.zenodo.org/#deposit-metadata
        def create_person(entity: User) -> dict:
            out = {"name": entity["name"]["full_inverted"]}
            if "affiliation" in entity:
                out["affiliation"] = entity["affiliation"]
            if "orcid" in entity:
                out["orcid"] = entity["orcid"]["id"]
            if "gnd" in entity:
                out["gnd"] = entity["gnd"]["id"]
            return out

        metadata = self._manager.data_branch["release.zenodo"]
        metadata["creators"] = [
            create_person(entity=entity) for entity in self._manager.user_manager.citation_authors(self._data)
        ]
        if "communities" in metadata:
            metadata["communities"] = [{"identifier": identifier} for identifier in metadata["communities"]]
        if "grants" in metadata:
            metadata["grants"] = [{"id": grant["id"]} for grant in metadata["grants"]]
        # Add contributors
        contributor_entries = []
        for contributor in self._user_manager.citation_contributors(self._data) + contributors:
            contributor_base = create_person(entity=contributor)
            for contributor_role_id in contributor["role"].keys():
                contributor_role_type = self._data["role"][contributor_role_id]["type"]
                contributor_entry = contributor_base | {"type": contributor_role_type}
                if contributor_entry not in contributor_entries:
                    contributor_entries.append(contributor_entry)
        metadata["contributors"] = contributor_entries
        metadata |= {
            "version": str(version),
            "preserve_doi": True,
        }
        api = pl.api.zenodo(token=self._manager.zenodo_token.get())
        response = api.deposition_create(metadata=metadata)
        return response

    def _prepare_citation_for_release(
        self,
        version: PEP440SemVer,
        doi: str | None,
    ):
        cff_filepath = self._manager.path_repo_target / "CITATION.cff"
        cff = ps.read.yaml_from_file(path=cff_filepath)
        cff["date-released"] = datetime.datetime.now().strftime('%Y-%m-%d')
        cff.pop("commit", None)
        cff["version"] = str(version)
        if doi:
            cff["doi"] = doi
        ps.write.to_yaml_file(data=cff, path=cff_filepath)
        return

    # def get_current_dirty_version(self):
    #     version = versioningit.get_version(
    #         project_dir=repo_path / data_branch["pkg.path.root"],
    #         config=data_branch["pkg.build.tool.versioningit"],
    #     )