from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
import datetime

from loggerman import logger
import pylinks as pl
import pyserials as ps

from proman.dstruct import Version, VersionTag

from proman.dtype import IssueStatus, ReleaseAction

if _TYPE_CHECKING:
    from typing import Literal
    from gittidy import Git
    from versionman.pep440_semver import PEP440SemVer
    from proman.manager.user import User
    from proman.manager import Manager
    from proman.dstruct import Branch


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

    def calculate_next_version(
        self,
        issue_num: int | str,
        deploy_type: IssueStatus,
        action: ReleaseAction | None,
        git_base: Git | None = None,
    ) -> Version:
        ver_base = self.latest_version(git=git_base)
        if not action:
            # Internal changes; return next local version
            return Version(
                public=ver_base.public,
                local=(ver_base.local[0] + 1, )
            )
        if ver_base.public.pre:
            ver_base_pre_phase = ver_base.public.pre[0]
            if ver_base_pre_phase == "rc":
                # Can only be the next post
                return Version(public=ver_base.public.next_post)
            if (
                deploy_type is IssueStatus.DEPLOY_FINAL
                or deploy_type.prerelease_type > ver_base_pre_phase
            ):
                # Next pre-release phase
                new_pre_phase = self._next_prerelease_phase(ver_base_pre_phase)
                new_ver = f"{ver_base.public.base}{new_pre_phase}{ver_base.public.pre[1]}"
                return Version(public=PEP440SemVer(new_ver))
            return Version(public=ver_base.public.next_post)
        next_final_ver = self.next_version(version=ver_base.public, action=action)
        if action is ReleaseAction.POST or deploy_type is IssueStatus.DEPLOY_FINAL:
            return Version(next_final_ver)
        version = f"{next_final_ver.base}{deploy_type.prerelease_type}{issue_num}"
        return Version(PEP440SemVer(version))

    def next_dev_version(
        self,
        issue_num: int | str,
        git_base: Git,
        git_head: Git,
        action: ReleaseAction,
    ) -> VersionTag:
        ver_base = self.latest_version(git=git_base, dev_only=False)
        ver_head = self.latest_version(git=git_head, dev_only=True)
        ver_last_base = ver_base.public
        if ver_last_base.pre:
            # The base branch is a pre-release branch
            next_ver = ver_last_base.next_post
            if (
                ver_head
                and ver_head.public.release == next_ver.release
                and ver_head.public.pre == next_ver.pre
                and ver_head.public.dev is not None
            ):
                dev = ver_head.public.dev + 1
            else:
                dev = 0
            next_ver_str = f"{next_ver}.dev{dev}"
        else:
            next_ver = self.next_version(ver_last_base, action)
            next_ver_str = str(next_ver)
            if action is not ReleaseAction.POST:
                next_ver_str += f".a{issue_num}"
            if not ver_head:
                dev = 0
            elif action is ReleaseAction.POST:
                if ver_head.public.post is not None and ver_head.public.post == next_ver.post:
                    dev = ver_head.public.dev + 1
                else:
                    dev = 0
            elif ver_head.public.pre is not None and ver_head.public.pre == ("a", int(issue_num)):
                dev = ver_head.public.dev + 1
            else:
                dev = 0
            next_ver_str += f".dev{dev}"
        return self.tag_version(PEP440SemVer(next_ver_str), git=git_head)

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

        metadata = self._manager.data["release.zenodo"]
        metadata["creators"] = [
            create_person(entity=entity) for entity in self._manager.user.citation_authors(self._data)
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

    @staticmethod
    def _next_prerelease_phase(current_phase: Literal["a", "b", "rc"]) -> Literal["a", "b", "rc"]:
        return {
            "rc": "rc",
            "b": "rc",
            "a": "b",
        }[current_phase]

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