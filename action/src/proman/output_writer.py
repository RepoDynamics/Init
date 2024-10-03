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
        package_artifact_name = f"Distribution Package (v{version})" if version else "Distribution Package"
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
                "job-suffix": "",
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
                "os-name": [data_branch[f"pkg.os.{key}.name"] for key in ("linux", "macos", "windows")],
                "os": [
                    {
                        "name": data_branch[f"pkg.os.{key}.name"],
                        "runner": data_branch[f"pkg.os.{key}.runner"],
                    } for key in ("linux", "macos", "windows")
                ],
                "pkg": data_branch["pkg"],
                "python-ver-max": data_branch["pkg"]["python"]["version"]["minors"][-1],
                "tool": data_branch["tool"],
                "job-suffix": "",
            }
        )
        return

    def set_package_test(
        self,
        data_branch: DataManager,
        ref: str | None = None,
        source: Literal["github", "pypi", "testpypi"] = "github",
        version: str | None = None,
        retries: int = 40,
        retry_sleep_seconds: int = 15,
    ):
        self._output_test.append(
            {
                "config": self._create_output_package_test(
                    ccm_branch=data_branch,
                    ref=ref,
                    source=source,
                    version=version,
                    retry_sleep_seconds=retry_sleep_seconds,
                    retries=retries,
                ),
                "job-suffix": "",
            }
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

        def cibw_platforms():
            platforms = []
            for os_key in ("linux", "macos", "windows"):
                os = data_branch["pkg.os"].get(os_key, {})
                ci_build = os.get("cibuild")
                if not ci_build:
                    continue
                for cibw_platform in ci_build:
                    platforms.append({"runner": os["runner"], "cibw_platform": cibw_platform})
            return platforms

        self._output_build.append(
            {
                "repository": self._repository,
                "ref": ref or self.ref,
                "artifact-name": artifact_name,
                "pure-python": data_branch["pkg.python.pure"],
                "path-pkg": data_branch["pkg.path.root"],
                "path-readme": data_branch["pkg.readme.path"] or "",
                "path-license": data_branch["license.path"] or "",
                "cibw-platforms": cibw_platforms(),
                "cibw-pythons": [
                    f"cp{ver.replace('.', '')}" for ver in data_branch["pkg.python.version.minors"]
                ] if not data_branch["pkg.python.pure"] else [],
                "job-suffix": "",
            }
        )
        if publish_testpypi or publish_pypi:
            self.set_lint(
                data_branch=data_branch,
                ref=ref,
                ref_before=ref_before,
            )
            self._output_test.append(
                {
                    "config": self._create_output_package_test(
                        ccm_branch=data_branch,
                        ref=ref,
                        source="github",
                    ),
                    "job-suffix": "",
                }
            )
            self._output_publish_testpypi = {
                "platform": "TestPyPI",
                "upload-url": "https://test.pypi.org/legacy/",
                "download-url": f'https://test.pypi.org/project/{data_branch["pkg"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_testpypi = self._create_output_package_test(
                ccm_branch=data_branch,
                ref=ref,
                source="testpypi",
                version=version,
            )
        if publish_pypi:
            self._output_publish_pypi = {
                "platform": "PyPI",
                "upload-url": "https://upload.pypi.org/legacy/",
                "download-url": f'https://pypi.org/project/{data_branch["pkg"]["name"]}/{version}',
                "artifact-name": artifact_name,
            }
            self._output_test_pypi = self._create_output_package_test(
                ccm_branch=data_branch,
                ref=ref,
                source="pypi",
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
        ref_name: str | None = None,
        source: Literal["github", "pypi", "testpypi"] = "github",
        version: str | None = None,
        retries: int = 40,
        retry_sleep_seconds: int = 15,
        pyargs: list[str] | None = None,
        args: list[str] | None = None,
        overrides: dict[str, str] | None = None,
        report_artifact_name: str = "Test-Suite Report",
    ) -> list[dict]:
        common = {
            "repository": self._repository,
            "ref": ref or self.ref,
            "tests-path": ccm_branch["test.path.root"],
            "tests-name": ccm_branch["test.import_name"],
            "pkg-src": source.lower(),
            "pkg-path": ccm_branch["pkg.path.root"],
            "pkg-name": ccm_branch["pkg.name"],
            "pkg-version": version or "",
            "pkg-req-path": ccm_branch["pkg.dependency.env.pip.path"] if source.lower() == "testpypi" else "",
            "retries": str(retries),
            "retry-sleep-seconds": str(retry_sleep_seconds),
            "pyargs": ps.write.to_json_string(pyargs) if pyargs else "",
            "args": ps.write.to_json_string(args) if args else "",
            "overrides": ps.write.to_json_string(overrides) if overrides else "",
            "codecov-yml-path": ccm_branch["tool.codecov.config.file.path"],
        }
        out = []
        if source == "github":
            artifact_name_part_source = "GitHub"
            artifact_name_part_ref = ref_name or self._context.ref_name
        elif source == "pypi":
            artifact_name_part_source = "PyPI"
            artifact_name_part_ref = version
        else:
            artifact_name_part_source = "TestPyPI"
            artifact_name_part_ref = version
        for os_key in ("linux", "macos", "windows"):
            os = ccm_branch["pkg.os"].get(os_key)
            if not os:
                continue
            for python_version in ccm_branch["pkg"]["python"]["version"]["minors"]:
                out.append(
                    {
                        **common,
                        "runner": os["runner"],
                        "os-name": os["name"],
                        "python-version": python_version,
                        "report-artifact-name": f"{report_artifact_name} ({artifact_name_part_source} {artifact_name_part_ref} {os['name']} py{python_version})",
                    }
                )
        return out
