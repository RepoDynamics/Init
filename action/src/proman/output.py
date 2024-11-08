from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

from loggerman import logger
import pyserials as ps
import mdit
import versioningit


if TYPE_CHECKING:
    from typing import Literal
    from proman.manager import Manager


class OutputManager:

    def __init__(self):
        self._main_manager: Manager = None
        self._branch_manager: Manager = None
        self._repository: str = ""
        self._ref: str = ""
        self._ref_name: str = ""
        self._ref_before: str = ""
        self._version: str = ""
        self._jinja_env_vars = {}

        self._out_web_build: list[dict] = []
        self._out_web_deploy: dict = {}
        self._out_lint: list[dict] = []
        self._out_test: list[dict] = []
        self._out_build: list[dict] = []
        self._out_publish_testpypi: list[dict] = []
        self._out_test_testpypi: list[dict] = []
        self._out_publish_pypi: list[dict] = []
        self._out_test_pypi: list[dict] = []
        self._out_release: dict = {}
        return

    def set(
        self,
        main_manager: Manager,
        branch_manager: Manager,
        version: str,
        repository: str | None = None,
        ref: str | None = None,
        ref_name: str | None = None,
        ref_before: str | None = None,
        website_build: bool = False,
        website_deploy: bool = False,
        package_lint: bool = False,
        test_lint: bool = False,
        package_test: bool = False,
        package_test_source: Literal["github", "pypi", "testpypi"] = "github",
        package_build: bool = False,
        package_publish_testpypi: bool = False,
        package_publish_pypi: bool = False,
        package_release: bool = False,
    ):
        logger.info(
            "Output Set",
            logger.pretty(locals())
        )
        self._main_manager = main_manager
        self._branch_manager = branch_manager
        self._version = version
        self._repository = repository or self._branch_manager.gh_context.target_repo_fullname
        self._ref = ref or self._branch_manager.git.commit_hash_normal()
        self._ref_name = ref_name or self._branch_manager.git.current_branch_name()
        self._ref_before = ref_before or self._branch_manager.gh_context.hash_before
        self._jinja_env_vars = {
            "version": version,
            "branch": self._ref_name,
            "commit": self._ref,
        }
        if website_build or website_deploy:
            self._set_web(deploy=website_deploy)
        if package_lint:
            self._set_lint("pkg")
        if test_lint:
            self._set_lint("test")
        if package_test:
            self._out_test.append(self._create_output_package_test(source=package_test_source))
        if package_build or package_publish_testpypi or package_publish_pypi:
            self.set_package_build_and_publish(
                publish_testpypi=package_publish_testpypi,
                publish_pypi=package_publish_pypi,
            )
        if package_release:
            self.set_release(
                name=release_name,
                tag=release_tag,
                body=release_body,
                prerelease=release_prerelease,
                make_latest=release_make_latest,
                discussion_category_name=release_discussion_category_name,
                package_artifact_name=package_artifact_name,
            )
        return

    def generate(self, failed: bool) -> dict:
        if failed:
            # Just to be safe, disable publish/deploy/release jobs if fail is True
            self._out_web_deploy = {}
            self._out_publish_testpypi = {}
            self._out_test_testpypi = []
            self._out_publish_pypi = {}
            self._out_test_pypi = []
            if self._out_release.get("release"):
                self._out_release["release"] = False
        output = {
            "fail": failed,
            "run": {
                "web-build": bool(self._out_web_build),
                "web-deploy": bool(self._out_web_deploy),
                "lint": bool(self._out_lint),
                "test": bool(self._out_test),
                "build": bool(self._out_build),
                "publish-testpypi": bool(self._out_publish_testpypi),
                "test-testpypi": bool(self._out_test_testpypi),
                "publish-pypi": bool(self._out_publish_pypi),
                "test-pypi": bool(self._out_test_pypi),
                "release": bool(self._out_release),
            },
            "web-build": self._out_web_build,
            "web-deploy": self._out_web_deploy,
            "lint": self._out_lint,
            "test": self._out_test,
            "build": self._out_build,
            "publish-testpypi": self._out_publish_testpypi,
            "test-testpypi": self._out_test_testpypi,
            "publish-pypi": self._out_publish_pypi,
            "test-pypi": self._out_test_pypi,
            "release": self._out_release,
        }
        output_yaml = ps.write.to_yaml_string(output)
        logger.info(
            "Action Outputs",
            mdit.element.code_block(output_yaml, language="yaml"),
        )
        return output

    def _set_web(self, deploy: bool):
        if "web" not in self._branch_manager.data:
            return
        build = {
            "repository": self._repository,
            "ref": self._ref,
            "path-env": self._branch_manager.data["web.env.file.conda.path"],
            "path-web": self._branch_manager.data["web.path.root"],
            "path-pkg": self._branch_manager.data.get(
                "pkg.path.root", ""
            ) if self._branch_manager.data["web.sphinx.needs_package"] else "",
            "job-name": self._branch_manager.fill_jinja_template(
                self._main_manager.data["workflow.job.web_build.name"],
                env_vars=self._jinja_env_vars,
            ),
            "build-artifact-name": self._branch_manager.fill_jinja_template(
                self._main_manager.data["workflow.job.web_build.artifact.build.name"],
                env_vars=self._jinja_env_vars,
            ),
            "pages-artifact-name": self._branch_manager.fill_jinja_template(
                self._main_manager.data["workflow.job.web_build.artifact.pages.name"],
                env_vars=self._jinja_env_vars,
            ),
        }
        self._out_web_build.append(build)
        if deploy:
            self._out_web_deploy = {
                "job-name": self._branch_manager.fill_jinja_template(
                    self._main_manager.data["workflow.job.web_deploy.name"],
                    env_vars=self._jinja_env_vars,
                ),
                "env-name": self._main_manager.data["workflow.job.web_deploy.env.name"],
                "env-url": self._main_manager.data["workflow.job.web_deploy.env.url"],
                "pages-artifact-name": build["pages-artifact-name"]
            }
        return

    def _set_lint(self, component: Literal["pkg", "test"]):
        if component not in self._branch_manager.data:
            return
        out = {
            "repository": self._repository,
            "ref-name": self._ref_name,
            "ref": self._ref,
            "ref-before": self._ref_before,
            "os-name": [self._branch_manager.data[f"{component}.os.{key}.name"] for key in ("linux", "macos", "windows")],
            "os": [
                {
                    "name": self._branch_manager.data[f"{component}.os.{key}.name"],
                    "runner": self._branch_manager.data[f"{component}.os.{key}.runner"],
                } for key in ("linux", "macos", "windows")
            ],
            "pkg": self._branch_manager.data[component],
            "pkg2": self._branch_manager.data["pkg" if component == "test" else "test"],
            "python-ver-max": self._branch_manager.data[f"{component}.python.version.minors"][-1],
            "tool": self._branch_manager.data["tool"],
            "job-name": self._branch_manager.fill_jinja_template(
                self._main_manager.data[f"workflow.job.{component}_lint.name"],
                env_vars=self._jinja_env_vars,
            ),
            "type": component,
        }
        self._out_lint.append(out)
        return

    def set_package_build_and_publish(
        self,
        publish_testpypi: bool = False,
        publish_pypi: bool = False,
    ):

        def cibw_platforms(typ: Literal["pkg", "test"]) -> list[dict]:
            platforms = []
            for os_key in ("linux", "macos", "windows"):
                os = self._branch_manager.data[f"{typ}.os"].get(os_key, {})
                ci_build = os.get("cibuild")
                if not ci_build:
                    continue
                for cibw_platform in ci_build:
                    for py_ver in self._branch_manager.data[f"{typ}.python.version.minors"]:
                        cibw_py_ver = f"cp{py_ver.replace('.', '')}"
                        platforms.append(
                            {
                                "runner": os["runner"],
                                "platform": cibw_platform,
                                "python_version": cibw_py_ver,
                                "wheel-artifact-name": self._branch_manager.fill_jinja_template(
                                    self._main_manager.data["workflow.job.pkg_build.artifact.wheel.name"],
                                    env_vars=self._jinja_env_vars | {
                                        "platform": cibw_platform,
                                        "python": cibw_py_ver,
                                    },
                                ),
                            }
                        )
            return platforms

        for typ in ("pkg", "test"):
            build = {
                "repository": self._repository,
                "ref": self._ref_name,
                "pure-python": self._branch_manager.data[f"{typ}.python.pure"],
                "path-pkg": self._branch_manager.data[f"{typ}.path.root"],
                "path-readme": self._branch_manager.data[f"{typ}.readme.path"] or "",
                "cibw": cibw_platforms(typ),
                "job-name": self._branch_manager.fill_jinja_template(
                    self._main_manager.data[f"workflow.job.{typ}_build.name"],
                    env_vars=self._jinja_env_vars,
                ),
                "sdist-artifact-name": self._branch_manager.fill_jinja_template(
                    self._main_manager.data[f"workflow.job.{typ}_build.artifact.sdist.name"],
                    env_vars=self._jinja_env_vars,
                ),
            }
            self._out_build.append(build)
            for target, publish, publish_out, in (
                ("testpypi", publish_testpypi, self._out_publish_testpypi),
                ("pypi", publish_pypi, self._out_publish_pypi),
            ):
                if not publish:
                    continue
                publish_out.append(
                    {
                        "job-name": self._branch_manager.fill_jinja_template(
                            self._main_manager.data[f"workflow.job.{typ}_publish_{target}.name"],
                            env_vars=self._jinja_env_vars,
                        ),
                        "env-name": self._main_manager.data[f"workflow.job.{typ}_publish_{target}.env.name"],
                        "env-url": self._branch_manager.fill_jinja_template(
                            self._main_manager.data[f"workflow.job.{typ}_publish_{target}.env.url"],
                            env_vars=self._jinja_env_vars,
                        ),
                        "index-url": self._branch_manager.fill_jinja_template(
                            self._main_manager.data[f"workflow.job.{typ}_publish_{target}.index.url"],
                            env_vars=self._jinja_env_vars,
                        ),
                        "build-artifact-names": [build["sdist-artifact-name"]] + [
                            build["wheel-artifact-name"] for build in build["cibw"]
                        ],
                    }
                )
                if typ == "pkg":
                    setattr(self, f"_out_test_{target}", self._create_output_package_test(source=target))
        return

    def set_release(
        self,
        name: str,
        tag: str,
        body: str | None = None,
        prerelease: bool | None = None,
        make_latest: Literal["legacy", "latest", "none"] | None = None,
        discussion_category_name: str | None = None,
        website_artifact_name: str = "Documentation",
        package_artifact_name: str = "Package",
    ):
        self._out_release["github"] = {
            "name": name,
            "tag-name": tag,
            "body": body,
            "prerelease": prerelease,
            "make-latest": make_latest,
            "discussion_category_name": discussion_category_name,
            "website-artifact-name": website_artifact_name,
            "package-artifact-name": package_artifact_name
        }
        return

    def _create_output_package_test(
        self,
        source: Literal["github", "pypi", "testpypi"] = "github",
        pyargs: list[str] | None = None,
        args: list[str] | None = None,
        overrides: dict[str, str] | None = None,
    ) -> list[dict]:
        env_vars = {
            "source": {"github": "GitHub", "pypi": "PyPI", "testpypi": "TestPyPI"}[source]
        }
        common = {
            "repository": self._repository,
            "ref": self._ref_name,
            "test-src": source.lower(),
            "test-path": self._branch_manager.data["test.path.root"],
            "test-name": self._branch_manager.data["test.import_name"],
            "test-version": self._version,
            "test-req-path": self._branch_manager.data["test.dependency.env.pip.path"] if source == "testpypi" else "",
            "pkg-src": source.lower(),
            "pkg-path": self._branch_manager.data["pkg.path.root"],
            "pkg-name": self._branch_manager.data["pkg.name"],
            "pkg-version": self._version,
            "pkg-req-path": self._branch_manager.data["pkg.dependency.env.pip.path"] if source == "testpypi" else "",
            "pyargs": ps.write.to_json_string(pyargs) if pyargs else "",
            "args": ps.write.to_json_string(args) if args else "",
            "overrides": ps.write.to_json_string(overrides) if overrides else "",
            "codecov-yml-path": self._branch_manager.data["tool.codecov.config.file.path"],
            "job-name": self._branch_manager.fill_jinja_template(
                self._main_manager.data[f"workflow.job.pkg_test.name"],
                env_vars=self._jinja_env_vars | env_vars,
            ),
        }
        out = []
        for os_key in ("linux", "macos", "windows"):
            os = self._branch_manager.data["pkg.os"].get(os_key)
            if not os:
                continue
            for python_version in self._branch_manager.data["pkg.python.version.minors"]:
                out.append(
                    {
                        **common,
                        "runner": os["runner"],
                        "os-name": os["name"],
                        "python-version": python_version,
                        "report-artifact-name": self._branch_manager.fill_jinja_template(
                            self._main_manager.data["workflow.job.pkg_test.artifact.report.name"],
                            env_vars=self._jinja_env_vars | env_vars | {
                                "os": os["name"],
                                "python": python_version,
                            },
                        ),
                    }
                )
        return out
