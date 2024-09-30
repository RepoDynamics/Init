from typing import Literal

from loggerman import logger
from github_contexts import GitHubContext
import pyserials as ps
import mdit

from proman.data_manager import DataManager


class OutputWriter:

    def __init__(self, github_context: GitHubContext):
        self._context = github_context
        self._repository = self._context.target_repo_fullname

        self.ref = self._context.ref_name
        self.ref_before = self._context.hash_before

        self._output_website: list[dict] = []
        self._output_lint: list[dict] = []
        self._output_test: list[dict] = []
        self._output_build: list[dict] = []
        self._output_publish_testpypi: dict = {}
        self._output_test_testpypi: list[dict] = []
        self._output_publish_pypi: dict = {}
        self._output_test_pypi: list[dict] = []
        self._output_finalize: dict = {}
        return

    def set(
        self,
        data_branch: DataManager,
        ref: str = "",
        ref_before: str = "",
        version: str = "",
        release_name: str = "",
        release_tag: str = "",
        release_body: str = "",
        release_prerelease: bool = False,
        release_make_latest: Literal["legacy", "latest", "none"] = "legacy",
        release_discussion_category_name: str = "",
        website_url: str | None = None,
        website_build: bool = False,
        website_artifact_name: str = "Website",
        website_deploy: bool = False,
        package_lint: bool = False,
        package_test: bool = False,
        package_test_source: Literal["GitHub", "PyPI", "TestPyPI"] = "GitHub",
        package_build: bool = False,
        package_publish_testpypi: bool = False,
        package_publish_pypi: bool = False,
        package_release: bool = False,
    ):
        package_artifact_name = f"Package ({version})" if version else "Package"
        if website_build or website_deploy:
            self.set_website(
                data_branch=data_branch,
                url=website_url,
                ref=ref,
                deploy=website_deploy,
                artifact_name=website_artifact_name,
            )
        if package_lint and not (package_publish_testpypi or package_publish_pypi):
            self.set_lint(
                data_branch=data_branch,
                ref=ref,
                ref_before=ref_before,
            )
        if package_test and not (package_publish_testpypi or package_publish_pypi):
            self.set_package_test(
                data_branch=data_branch,
                ref=ref,
                source=package_test_source,
                version=version,
            )
        if package_build or package_publish_testpypi or package_publish_pypi:
            self.set_package_build_and_publish(
                data_branch=data_branch,
                version=version,
                ref=ref,
                publish_testpypi=package_publish_testpypi,
                publish_pypi=package_publish_pypi,
                artifact_name=package_artifact_name,
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
            if self._output_website:
                for web_output in self._output_website:
                    web_output["deploy"] = False
            self._output_publish_testpypi = {}
            self._output_test_testpypi = []
            self._output_publish_pypi = {}
            self._output_test_pypi = []
            if self._output_finalize.get("release"):
                self._output_finalize["release"] = False
        output = {
            "fail": failed,
            "run": {
                "website": bool(self._output_website),
                "lint": bool(self._output_lint),
                "test": bool(self._output_test),
                "build": bool(self._output_build),
                "publish-testpypi": bool(self._output_publish_testpypi),
                "test-testpypi": bool(self._output_test_testpypi),
                "publish-pypi": bool(self._output_publish_pypi),
                "test-pypi": bool(self._output_test_pypi),
                "finalize": bool(self._output_finalize),
            },
            "website": self._output_website,
            "lint": self._output_lint,
            "test": self._output_test,
            "build": self._output_build,
            "publish-testpypi": self._output_publish_testpypi,
            "test-testpypi": self._output_test_testpypi,
            "publish-pypi": self._output_publish_pypi,
            "test-pypi": self._output_test_pypi,
            "finalize": self._output_finalize,
        }
        output_yaml = ps.write.to_yaml_string(output)
        logger.info(
            "Action Outputs",
            mdit.element.code_block(output_yaml, language="yaml"),
        )
        return output

    def set_website(
        self,
        data_branch: DataManager,
        url: str | None = None,
        ref: str | None = None,
        deploy: bool = False,
        artifact_name: str = "Website",
    ):
        if "web" not in data_branch:
            return
        if not url:
            raise RuntimeError("No URL provided for setting website job output")
        path_pkg = data_branch.get("pkg.path.root", "") if data_branch["web.sphinx.needs_package"] else ""
        self._output_website.append(
            {
                "url": url or data_branch["web.url.base"],
                "repository": self._repository,
                "ref": ref or self.ref,
                "path-env": data_branch["web.env.file.conda.path"],
                "path-web": data_branch["web.path.root"],
                "path-pkg": path_pkg,
                "artifact-name": artifact_name,
                "deploy": deploy,
            }
        )
        return

    def set_lint(
        self,
        data_branch: DataManager,
        ref: str | None = None,
        ref_before: str | None = None,
        ref_name: str | None = None,
    ):
        if "pkg" not in data_branch:
            return
        self._output_lint.append(
            {
                "repository": self._repository,
                "ref": ref or self.ref,
                "ref-before": ref_before or self.ref_before,
                "ref-name": ref_name or self._context.ref_name,
                "os": [
                    {"name": os["name"], "runner": os["runner"]} for os in data_branch["pkg"]["os"].values()
                ],
                "pkg": data_branch["pkg"],
                "python-ver-max": data_branch["pkg"]["python"]["version"]["micros"][-1],
                "tool": data_branch["tool"],
            }
        )
        return

    def set_package_test(
        self,
        data_branch: DataManager,
        ref: str | None = None,
        source: Literal["GitHub", "PyPI", "TestPyPI"] = "GitHub",
        version: str | None = None,
        retry_sleep_seconds: str = "30",
        retry_sleep_seconds_total: str = "900",
    ):
        self._output_test.extend(
            self._create_output_package_test(
                ccm_branch=data_branch,
                ref=ref,
                source=source,
                version=version,
                retry_sleep_seconds=retry_sleep_seconds,
                retry_sleep_seconds_total=retry_sleep_seconds_total,
            )
        )
        return

    def set_package_build_and_publish(
        self,
        data_branch: DataManager,
        version: str,
        ref: str | None = None,
        ref_before: str | None = None,
        publish_testpypi: bool = False,
        publish_pypi: bool = False,
        artifact_name: str = "Package"
    ):

        def _package_operating_systems(self):
            output = {
                "os_titles": [],
                "os_independent": True,
                "pure_python": True,
                "github_runners": [],
                "cibw_matrix_platform": [],
                "cibw_matrix_python": [],
            }
            os_title = {
                "linux": "Linux",
                "macos": "macOS",
                "windows": "Windows",
            }
            data_os = self._data["pkg.os"]

            if not self._data["package"].get("operating_systems"):
                _logger.info("No operating systems provided; package is platform independent.")
                output["github_runners"].extend(["ubuntu-latest", "macos-latest", "windows-latest"])
                output["os_titles"].extend(list(os_title.values()))
                _logger.section_end()
                return output
            output["os_independent"] = False
            for os_name, specs in self._data["package"]["operating_systems"].items():
                output["os_titles"].append(os_title[os_name])
                default_runner = f"{os_name if os_name != 'linux' else 'ubuntu'}-latest"
                if not specs:
                    _logger.info(f"No specifications provided for operating system '{os_name}'.")
                    output["github_runners"].append(default_runner)
                    continue
                runner = default_runner if not specs.get("runner") else specs["runner"]
                output["github_runners"].append(runner)
                if specs.get("cibw_build"):
                    for cibw_platform in specs["cibw_build"]:
                        output["cibw_matrix_platform"].append(
                            {"runner": runner, "cibw_platform": cibw_platform})
            if output["cibw_matrix_platform"]:
                output["pure_python"] = False
                output["cibw_matrix_python"].extend(
                    [f"cp{ver.replace('.', '')}" for ver in self._data["package"]["python_versions"]]
                )
            _logger.debug("Generated data:", code=str(output))
            return output


        self._output_build.append(
            {
                "repository": self._repository,
                "ref": ref or self.ref,
                "artifact-name": artifact_name,
                "pure-python": data_branch["package"]["pure_python"],
                "cibw-matrix-platform": data_branch["package"]["cibw_matrix_platform"],
                "cibw-matrix-python": data_branch["package"]["cibw_matrix_python"],
                "path-readme": data_branch["path"]["file"]["readme_pypi"],
            }
        )
        if publish_testpypi or publish_pypi:
            self.set_lint(
                data_branch=data_branch,
                ref=ref,
                ref_before=ref_before,
            )
            self._output_test.extend(
                self._create_output_package_test(
                    ccm_branch=data_branch,
                    ref=ref,
                    source="GitHub",
                )
            )
            self._output_publish_testpypi = {
                "platform": "TestPyPI",
                "upload-url": "https://test.pypi.org/legacy/",
                "download-url": f'https://test.pypi.org/project/{data_branch["package"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_testpypi = self._create_output_package_test(
                ccm_branch=data_branch,
                ref=ref,
                source="TestPyPI",
                version=version,
            )
        if publish_pypi:
            self._output_publish_pypi = {
                "platform": "PyPI",
                "upload-url": "https://upload.pypi.org/legacy/",
                "download-url": f'https://pypi.org/project/{data_branch["package"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_pypi = self._create_output_package_test(
                ccm_branch=data_branch,
                ref=ref,
                source="PyPI",
                version=version,
            )
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
        self._output_finalize["release"] = {
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
        ccm_branch: DataManager,
        ref: str | None = None,
        source: Literal["GitHub", "PyPI", "TestPyPI"] = "GitHub",
        version: str | None = None,
        retry_sleep_seconds_total: str = "900",
        retry_sleep_seconds: str = "30",
    ) -> list[dict]:
        common = {
            "repository": self._repository,
            "ref": ref or self.ref,
            "path-setup-testsuite": ccm_branch["test.path.root"],
            "path-setup-package": ccm_branch["pkg.path.root"],
            "testsuite-import-name": ccm_branch["test.import_name"],
            "package-source": source,
            "package-name": ccm_branch["pkg.name"],
            "package-version": version or "",
            "path-requirements-package": ccm_branch["pkg.dependency.env.pip.path"],
            "path-report-pytest": ccm_branch["path"]["dir"]["local"]["report"]["pytest"],
            "path-report-coverage": ccm_branch["path"]["dir"]["local"]["report"]["coverage"],
            "path-cache-pytest": ccm_branch["path"]["dir"]["local"]["cache"]["pytest"],
            "path-cache-coverage": ccm_branch["path"]["dir"]["local"]["cache"]["coverage"],
            "retry-sleep-seconds": retry_sleep_seconds,
            "retry-sleep-seconds-total": retry_sleep_seconds_total,
        }
        out = []
        for github_runner, os in zip(
            ccm_branch["package"]["github_runners"],
            ccm_branch["package"]["os_titles"]
        ):
            for python_version in ccm_branch["package"]["python_versions"]:
                out.append(
                    {
                        **common,
                        "runner": github_runner,
                        "os": os,
                        "python-version": python_version,
                    }
                )
        return out
