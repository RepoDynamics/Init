from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from loggerman import logger

from proman.manager.release.asset import create_releaseman_intput

if _TYPE_CHECKING:
    from typing import Literal
    from proman.manager import Manager
    from proman.dstruct import VersionTag


class GitHubReleaseManager:
    
    def __init__(self, manager: Manager):
        self._manager = manager
        return

    def get_or_make_draft(
        self,
        tag: VersionTag | str,
        name: str | None = None,
        body: str | None = None,
        prerelease: bool = False,
        discussion_category_name: str | None = None,
        make_latest: Literal['true', 'false', 'legacy'] = 'true'
    ) -> dict[str, str | int]:
        release = self._manager.changelog.get_release("github")
        if release:
            return release
        response = self._manager.gh_api_actions.release_create(
            tag_name=str(tag),
            name=name,
            body=body,
            draft=True,
            prerelease=prerelease,
            discussion_category_name=discussion_category_name,
            make_latest=make_latest,
        )
        logger.success(
            "GitHub Release Draft",
            "Created new release draft:",
            str(response)
        )
        out = {k: v for k, v in response.items() if k in ("id", "node_id")}
        self._manager.changelog.update_release_github(**out)
        return out

    def update_draft(
        self,
        tag: VersionTag,
        on_main: bool,
        publish: bool = False,
        release_id: int | None = None
    ) -> dict[str, str | int]:
        if not release_id:
            draft = self._manager.changelog.get_release("github")
            release_id = draft["id"]
        config = self._manager.data["release.github"]
        is_prerelease = bool(tag.version.pre)
        if publish:
            if is_prerelease:
                make_latest = "false"
            elif config["order"] == "date":
                make_latest = "true"
            else:
                make_latest = "true" if on_main else "false"
        else:
            make_latest = None
        jinja_env_vars = {"version": tag.version, "changelog": self._manager.changelog.current}
        update_response = self._manager.gh_api_actions.release_update(
            release_id=release_id,
            tag_name=str(tag),
            name=self._manager.fill_jinja_template(config["name"], env_vars=jinja_env_vars),
            body=self._manager.fill_jinja_template(config["body"], env_vars=jinja_env_vars),
            prerelease=is_prerelease,
            discussion_category_name=self._manager.fill_jinja_template(
                config["discussion_category_name"], env_vars=jinja_env_vars
            ),
            make_latest=make_latest,
        )
        logger.success(
            "GitHub Release Update",
            str(update_response)
        )
        output = self._make_output(
            release_id=release_id,
            publish=publish and not config["draft"],
            asset_config=self._manager.fill_jinja_templates(config["asset"], env_vars={"version": tag.version}),
        )
        return output

    def delete_draft(self, release_id: int | None = None):
        if not release_id:
            draft = self._manager.changelog.get_release("github")
            release_id = draft["id"]
        self._manager.gh_api_actions.release_delete(release_id=release_id)
        logger.success(
            "GitHub Release Draft Deletion",
            f"Deleted draft for release ID {release_id}"
        )
        return

    @staticmethod
    def _make_output(release_id: int, publish: bool, asset_config: dict):
        return {
            "release_id": release_id,
            "draft": not publish,
            "delete_assets": "all",
            "assets": create_releaseman_intput(asset_config=asset_config, target="github")
        }
