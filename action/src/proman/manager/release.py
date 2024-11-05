from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime

import pylinks as pl
import pyserials as ps

if _TYPE_CHECKING:
    from pathlib import Path
    from proman.manager.data import DataManager
    from versionman.pep440_semver import PEP440SemVer
    from proman.manager.user import User, UserManager


class ReleaseManager:
    
    def __init__(
        self,
        root_path: Path,
        user_manager: UserManager,
        zenodo_token: str | None
    ):
        self._path_root = root_path
        self._zenodo_api = pl.api.zenodo(token=zenodo_token) if zenodo_token else None
        self._user_manager = user_manager
        self._data: DataManager = None
        return

    def run(
        self,
        data: DataManager,
        version: PEP440SemVer,
        contributors: list[User] | None = None,
        embargo_date: str | None = None,
    ):
        self._data = data
        zenodo_output = None
        if self._data["citation"]:
            doi = None
            if self._zenodo_api:
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
            self._prepare_citation_for_release(version=version, doi=doi)
        return zenodo_output
    
    def _create_zenodo_depo(self, version: PEP440SemVer, contributors: list[User], embargo_date: str | None = None):
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

        def create_related_identifiers(identifier: dict) -> list[dict]:
            return {
                "identifier": identifier["value"],
                "relation": identifier["relation"],
                "resource_type": identifier["resource_type"],
            }

        def create_subject(subject: dict) -> dict:
            return {
                "term": subject["term"],
                "identifier": subject["id"],
                "scheme": subject["scheme"],
            }

        metadata = {
            "upload_type": self._data["citation.type"],
            "title": self._data["citation.title"],
            "creators": [
                create_person(entity=entity) for entity in self._user_manager.citation_authors(self._data)
            ],
            "contributors": [],
            "description": self._data["citation.abstract"],
            "keywords": self._data["citation.keywords"],
            "access_right": self._data["citation.access_right"],
            "access_conditions": self._data["citation.access_conditions"],
            "embargo_date": embargo_date,
            "license": self._data["citation.license"][0],
            "notes": self._data["citation.notes"],
            "related_identifiers": [
                create_related_identifiers(identifier)
                for identifier in self._data.get("citation.related_identifiers", [])
            ],
            "communities": [
                {"identifier": identifier}
                for identifier in self._data.get("citation.zenodo_communities", [])
            ],
            "grants": [
                {"id": grant["id"]} for grant in self._data.get("citation.grants", [])
            ],
            "subjects": [create_subject(subject) for subject in self._data["citation.subjects"]],
            "references": [ref["apa"] for ref in self._data["citation.references"] if "apa" in ref],
            "version": str(version),
            "language": self._data["language"],
            "preserve_doi": True,
        }
        if self._data["citation.doi"]:
            metadata["related_identifiers"].append(
                {
                    "identifier": self._data["citation.doi"],
                    "relation": "isNewVersionOf",
                    "resource_type": self._data["citation.type"],
                }
            )
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
        zenodo_response = self._zenodo_api.deposition_create(metadata=metadata)
        return zenodo_response

    def _prepare_citation_for_release(
        self,
        version: PEP440SemVer,
        doi: str | None,
    ):
        cff = ps.read.yaml_from_file(path=self._path_root / "CITATION.cff")
        cff["date-released"] = datetime.datetime.now().strftime('%Y-%m-%d')
        cff.pop("commit", None)
        cff["version"] = str(version)
        if doi:
            cff["doi"] = doi
        ps.write.to_yaml_file(data=cff, path=self._path_root / "CITATION.cff")
        return
