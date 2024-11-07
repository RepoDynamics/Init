from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime

import pylinks as pl
import pyserials as ps

if _TYPE_CHECKING:
    from versionman.pep440_semver import PEP440SemVer
    from proman.manager.user import User
    from proman.manager import Manager


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

    def tag_version(self, ver: str | PEP440SemVer, base: bool, env_vars: dict | None = None) -> str:
        tag_data = self._manager.data["tag.version"]
        tag = f"{tag_data["prefix"]}{ver}"
        msg = self._manager.protocol.fill_jinja_template(
            tag_data["message"],
            {"version": ver} | (env_vars or {}),
        )
        git = self._git_base if base else self._git_head
        git.create_tag(tag=tag, message=msg)
        return tag

    @staticmethod
    def get_next_version(version: PEP440SemVer, action: ReleaseAction) -> PEP440SemVer:
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
