from __future__ import annotations as _annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from loggerman import logger

from proman.manager.release.asset import create_releaseman_intput

if _TYPE_CHECKING:
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
        release_id: int | None = None,
        body: str | None = None,
    ) -> dict[str, str | int]:
        if not release_id:
            draft = self._manager.changelog.get_release("github")
            release_id = draft["id"]
        config = self._manager.data["workflow.publish.github"]
        is_prerelease = bool(tag.version.pre)
        jinja_env_vars = {"version": tag.version, "changelog": self._manager.changelog.current}
        update_response = self._manager.gh_api_actions.release_update(
            release_id=release_id,
            tag_name=str(tag),
            name=self._manager.fill_jinja_template(config["release"]["name"], env_vars=jinja_env_vars),
            body=body or self._manager.fill_jinja_template(config["release"]["body"], env_vars=jinja_env_vars),
            prerelease=is_prerelease,
        )
        logger.success(
            "GitHub Release Update",
            str(update_response)
        )
        if publish:
            if is_prerelease:
                make_latest = False
            elif config["release"]["order"] == "date":
                make_latest = True
            else:
                make_latest = on_main
        else:
            make_latest = None

        output = self._make_output(
            release_id=release_id,
            publish=publish and not config["release"]["draft"],
            asset_config=self._manager.fill_jinja_templates(
                config["asset"], env_vars={"version": tag.version}
            ) if "asset" in config else None,
            make_latest=make_latest,
            discussion_category_name=config["release"].get("discussion_category_name"),
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
    def _make_output(
        release_id: int,
        publish: bool,
        asset_config: dict | None = None,
        make_latest: bool | None = None,
        discussion_category_name: str | None = None,
    ):
        out = {
            "release_id": release_id,
            "draft": not publish,
            "delete_assets": "all",
            "assets": create_releaseman_intput(asset_config=asset_config, target="github") if asset_config else None,
            "discussion_category_name": discussion_category_name,
        }
        if make_latest is None:
            return out
        return out | {"make_latest": "true" if make_latest else "false"}
