from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

from loggerman import logger
import pyserials as ps
import mdit
import versioningit

from proman.dstruct import VersionTag, Version
from proman import const

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
        self._version: VersionTag | Version | None = None
        self._jinja_env_vars = {}

        self._out_web: list[dict] = []
        self._out_lint: list[dict] = []
        self._out_test: list[dict] = []
        self._out_build: list[dict] = []
        self._out_docker: list[dict] = []
        self._out_publish_testpypi: dict = {}
        self._out_publish_anaconda: dict = {}
        self._out_publish_pypi: dict = {}
        self._out_release: dict = {}
        return

    def set(
        self,
        main_manager: Manager,
        branch_manager: Manager,
        version: VersionTag | Version,
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
        docker_build: bool = False,
        docker_deploy: bool = False,
        package_publish_testpypi: bool = False,
        package_publish_pypi: bool = False,
        package_publish_anaconda: bool = False,
        github_release_config: dict | None = None,
        zenodo_config: dict | None = None,
        zenodo_sandbox_config: dict | None = None,
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
            "version": version if isinstance(version, Version) else version.version,
            "branch": self._ref_name,
            "commit": self._ref,
        }
        if website_build or website_deploy:
            self._set_web(deploy=website_deploy)
        if package_lint:
            self._set_lint("pkg")
        if test_lint:
            self._set_lint("test")
        if package_test and self._branch_manager.data["test"]:
            self._out_test.append(self._create_output_package_test(source=package_test_source))
        if docker_build or docker_deploy:
            self._set_docker(deploy=docker_deploy)
        if package_build or package_publish_testpypi or package_publish_pypi or package_publish_anaconda:
            self.set_package_build_and_publish(
                publish_testpypi=package_publish_testpypi,
                publish_pypi=package_publish_pypi,
                publish_anaconda=package_publish_anaconda,
            )
        if github_release_config or zenodo_config or zenodo_sandbox_config:
            self.set_release(
                config_github=github_release_config,
                config_zenodo=zenodo_config,
                config_zenodo_sandbox=zenodo_sandbox_config,
            )
        return

    def generate(self, failed: bool) -> dict:
        if failed:
            # Just to be safe, disable publish/deploy/release jobs if fail is True
            for web_config in self._out_web:
                web_config["job"]["deploy"] = False
            self._out_publish_testpypi = False
            self._out_publish_anaconda = False
            self._out_publish_pypi = False
            self._out_release = False
        output = {
            "fail": failed,
            "web": self._out_web or False,
            "lint": self._out_lint or False,
            "test": self._out_test or False,
            "build": self._out_build or False,
            "docker": self._out_docker or False,
            "publish-testpypi": self._out_publish_testpypi or False,
            "publish-anaconda": self._out_publish_anaconda or False,
            "publish-pypi": self._out_publish_pypi or False,
            "release": self._out_release or False,
        }
        output_yaml = ps.write.to_yaml_string(output)
        logger.info(
            "Action Outputs",
            mdit.element.code_block(output_yaml, language="yaml"),
        )
        return output

    @property
    def version(self) -> str:
        if not self._version:
            return ""
        if isinstance(self._version, Version):
            return str(self._version)
        return str(self._version.version)


    def _set_web(self, deploy: bool):
        if "web" not in self._branch_manager.data:
            return
        job_config = self._main_manager.data["workflow.web"]
        if not job_config or (not deploy and job_config["action"]["build"] == "disabled"):
            return
        out = {
            "name": self._fill_jinja(job_config["name"]),
            "job": {
                "repository": self._repository,
                "ref": self._ref,
                "path-env": self._branch_manager.data["web.env.file.conda.path"],
                "path-web": self._branch_manager.data["web.path.root"],
                "path-pkg": self._branch_manager.data.get(
                    "pkg.path.root", ""
                ) if self._branch_manager.data["web.sphinx.needs_package"] else "",
                "artifact": self._create_workflow_artifact_config(job_config["artifact"]),
                "deploy": deploy,
                "env": job_config["env"],
            }
        }
        self._out_web.append(out)
        return

    def _set_lint(self, component: Literal["pkg", "test"]):
        if component not in self._branch_manager.data:
            return
        job_config = self._main_manager.data[f"workflow.lint"]
        if not job_config or job_config["action"] == "disabled":
            return
        out = {
            "job": {
                "repository": self._repository,
                "ref-name": self._ref_name,
                "ref": self._ref,
                "ref-before": self._ref_before,
                "os": list(self._branch_manager.data[f"{component}.os"].values()),
                "pkg": self._branch_manager.data[component],
                "pkg2": self._branch_manager.data["pkg" if component == "test" else "test"],
                "python-max": self._branch_manager.data[f"{component}.python.version.minors"][-1],
                "tool": self._branch_manager.data["tool"],
                "type": component,
                "version": self.version,
            }
        }
        out["name"] = self._fill_jinja(
            job_config["name"],
            env_vars=out["job"] | self._jinja_env_vars,
        )
        self._out_lint.append(out)
        return

    def _set_docker(self, deploy: bool):
        job_config = self._main_manager.data["workflow.docker"]
        if not job_config or (not deploy and job_config["action"]["build"] == "disabled") or (
            deploy and job_config["action"]["deploy"] == "disabled"
        ):
            return
        out = {
            "name": self._fill_jinja(job_config["name"]),
            "job": {
                "name": "Build & Deploy" if deploy else "Build",
                "repository": self._repository,
                "ref": self._ref,
                "artifact": self._create_workflow_artifact_config(job_config["artifact"]),
                "no-push": "false" if deploy else "true",
                "env": job_config["env"],
            }
        }
        self._out_docker.append(out)
        return

    def set_package_build_and_publish(
        self,
        publish_testpypi: bool = False,
        publish_pypi: bool = False,
        publish_anaconda: bool = False,
        anaconda_label: str = "test",
    ):

        def ci_builds(typ: Literal["pkg", "test"]) -> list[dict]:
            builds = []
            for os in self._branch_manager.data[f"{typ}.os"].values():
                ci_build = os.get("builds")
                if not ci_build:
                    continue
                for cibw_platform in ci_build:
                    for py_ver in self._branch_manager.data[f"{typ}.python.version.minors"]:
                        cibw_py_ver = f"cp{py_ver.replace('.', '')}"
                        out = {
                            "runner": os["runner"],
                            "platform": cibw_platform,
                            "python": cibw_py_ver,
                        }
                        out["artifact"] = {
                            "wheel": self._create_workflow_artifact_config_single(
                                self._main_manager.data["workflow.build.artifact.wheel"],
                                jinja_env_vars=out | os,
                            )
                        }
                        builds.append(out)
            return builds

        def conda_builds(typ: Literal["pkg", "test"]) -> list[dict]:

            def get_noarch_os():
                for runner_prefix in ("ubuntu", "macos", "windows"):
                    for os in pkg["os"].values():
                        if os["runner"].startswith(runner_prefix):
                            return os
                return

            pkg = self._branch_manager.data[typ]
            if pkg["python"]["pure"]:
                noarch_build = {
                    "os": get_noarch_os(),
                    "python": pkg["python"]["version"]["minors"][-1],
                }
                noarch_build["artifact"] = self._create_workflow_artifact_config_single(
                    self._main_manager.data["workflow.build.artifact.conda"],
                    jinja_env_vars=noarch_build | {"pkg": pkg, "platform": "any", "python": "3"},
                )
                return [noarch_build]
            builds = []
            for os in self._branch_manager.data[f"{typ}.os"].values():
                for python_ver in self._branch_manager.data[f"{typ}.python.version.minors"]:
                    out = {
                        "os": os,
                        "python": python_ver,
                    }
                    out["artifact"] = {
                        "conda": self._create_workflow_artifact_config_single(
                            self._main_manager.data["workflow.build.artifact.conda"],
                            jinja_env_vars=out | {"pkg": pkg},
                        )
                    }
                    builds.append(out)
            return builds

        def conda_channels(typ: Literal["pkg", "test"]) -> str:

            def update_channel_priority(requirement: str):
                parts = requirement.split("::")
                if len(parts) > 1:
                    channel = parts[0]
                    channel_priority[channel] = channel_priority.get(channel, 0) + 1
                return

            meta = self._branch_manager.data.get(f"{typ}.conda.recipe.meta.values", {})
            channel_priority = {}
            for key in ("host", "run", "run_constrained"):
                for req in meta.get("requirements", {}).get("values", {}).get(key, {}).get("values", []):
                    update_channel_priority(req["value"])
            for req in meta.get("test", {}).get("values", {}).get("requires", {}).get("values", []):
                update_channel_priority(req["value"])
            return ",".join(sorted(channel_priority, key=channel_priority.get, reverse=True))


        build_jobs = {}
        build_config = self._main_manager.data[f"workflow.build"]
        for typ in ("pkg", "test"):
            if not self._branch_manager.data[typ]:
                continue
            if not (publish_pypi or publish_testpypi or publish_anaconda) and build_config["action"] == "disabled":
                continue
            build_job = {
                "repository": self._repository,
                "ref": self._ref_name,
                "pkg": self._branch_manager.data[typ],
                "ci-builds": ci_builds(typ) or False,
                "conda-builds": conda_builds(typ),
                "conda-channels": conda_channels(typ),
                "conda-recipe-path": self._branch_manager.data[f"{typ}.conda.recipe.path.local"],
            }
            build_job["artifact"] = self._create_workflow_artifact_config(
                build_config["artifact"],
                jinja_env_vars=build_job | {"platform": "any", "python": "3"},
                include_merge=True,
            )
            out = {
                "name": self._fill_jinja(
                    build_config["name"],
                    env_vars=build_job,
                ),
                "job": build_job,
            }
            self._out_build.append(out)
            build_jobs[typ] = build_job

        for target, do_publish, in (
            ("testpypi", publish_testpypi), ("pypi", publish_pypi), ("anaconda", publish_anaconda)
        ):
            if not do_publish:
                continue
            job_config = self._main_manager.data[f"workflow.publish.{target}"]
            publish_out = {
                "name": self._fill_jinja(job_config["name"]),
                "job": {
                    "publish": [],
                    "test": self._create_output_package_test(source=target, flatten_name=True) if self._branch_manager.data["test"] else False,
                }
            }
            for typ, build in build_jobs.items():
                if job_config["action"][typ] == "disabled":
                    continue
                publish_job = {
                    "name": self._fill_jinja(
                        job_config["task_name"],
                        env_vars=build,
                    ),
                    "env": {
                        "name": self._fill_jinja(job_config["env"]["name"], env_vars=build),
                        "url": self._fill_jinja(
                            job_config["env"]["url"],
                            env_vars=build,
                        ),
                    },
                    "artifact": build["artifact"],
                }
                if target != "anaconda":
                    publish_job["index-url"] = self._branch_manager.fill_jinja_template(
                        job_config["index"]["url"]["upload"],
                        env_vars=self._jinja_env_vars,
                    )
                else:
                    channel = job_config["index"]["channel"]
                    publish_job["user"] = channel
                    pkg_name = self._branch_manager.data[f"{typ}.name"].lower()
                    publish_out["job"].setdefault("finalize", []).append(
                        {"label": anaconda_label, "spec": f"{channel}/{pkg_name}/{self.version or "0.0.0"}"}
                    )
                publish_out["job"]["publish"].append(publish_job)
            if publish_out["job"]["publish"]:
                setattr(self, f"_out_publish_{target}", publish_out)
        return

    def set_release(
        self,
        config_github: dict | None = None,
        config_zenodo: dict | None = None,
        config_zenodo_sandbox: dict | None = None,
    ):
        for config, key, has_token in (
            (config_github, "github", True),
            (config_zenodo, "zenodo", bool(self._branch_manager.zenodo_token)),
            (config_zenodo_sandbox, "zenodo_sandbox", bool(self._branch_manager.zenodo_sandbox_token))
        ):
            job_config = self._branch_manager.data[f"workflow.publish.{key}"]
            if not job_config or job_config["action"] == "disabled" or not has_token:
                continue
            out = self._out_release or {
                "name": job_config["name"],
                "job": {
                    "ref": self._ref,
                    "repo-path": const.OUTPUT_RELEASE_REPO_PATH,
                    "artifact-path": const.OUTPUT_RELEASE_ARTIFACT_PATH,
                    "tasks": []
                }
            }
            out["job"]["tasks"].append(
                {
                    "name": job_config["task_name"],
                    "env": job_config["env"],
                    "github": config if key == "github" else {},
                    "zenodo": config if key == "zenodo" else {},
                    "zenodo-sandbox": config if key == "zenodo_sandbox" else {},
                }
            )
            self._out_release = out
        return

    def _create_output_package_test(
        self,
        source: Literal["github", "pypi", "testpypi", "anaconda"] = "github",
        pyargs: list[str] | None = None,
        args: list[str] | None = None,
        overrides: dict[str, str] | None = None,
        flatten_name: bool = False,
    ) -> dict:
        source = source.lower()
        env_vars = {
            "source": {
                "github": "GitHub", "pypi": "PyPI", "testpypi": "TestPyPI", "anaconda": "Anaconda"
            }[source]
        }
        job_config = self._main_manager.data["workflow.test"]
        job_name = self._fill_jinja(job_config["name"], env_vars)
        out = {
            "name": job_name,
            "job": {
                "repository": self._repository,
                "ref": self._ref_name,
                "test-src": source,
                "test-path": self._branch_manager.data["test.path.root"],
                "test-name": self._branch_manager.data["test.import_name"],
                "test-version": self.version,
                "test-req-path": self._branch_manager.data["test.dependency.env.pip.path"] if source == "testpypi" else "",
                "pkg-src": source,
                "pkg-path": self._branch_manager.data["pkg.path.root"],
                "pkg-name": self._branch_manager.data["pkg.name"],
                "pkg-version": self.version,
                "pkg-req-path": self._branch_manager.data["pkg.dependency.env.pip.path"] if source == "testpypi" else "",
                "pyargs": ps.write.to_json_string(pyargs) if pyargs else "",
                "args": ps.write.to_json_string(args) if args else "",
                "overrides": ps.write.to_json_string(overrides) if overrides else "",
                "codecov-yml-path": self._branch_manager.data["tool.codecov.config.file.path"],
                "artifact": self._create_workflow_artifact_merge_config(job_config["artifact"], env_vars),
                "retries": "60",
                "retry-sleep-seconds": "15",
                "tasks": []
            }
        }
        for os in self._branch_manager.data["pkg.os"].values():
            for python_version in self._branch_manager.data["pkg.python.version.minors"]:
                task = {
                    "runner": os["runner"],
                    "python": python_version,
                }
                task_env_vars = env_vars | task | {"os": os["name"]}
                task_name = self._fill_jinja(job_config["task_name"], task_env_vars)
                if flatten_name:
                    task_name = f"{job_name}: {task_name}"
                task |= {
                    "name": task_name,
                    "artifact": self._create_workflow_artifact_config(job_config["artifact"], task_env_vars),
                }
                out["job"]["tasks"].append(task)
        return out


    def _create_workflow_artifact_config(
        self,
        artifact: dict,
        jinja_env_vars: dict | None = None,
        include_merge: bool = False,
    ) -> dict:
        return {
            k: self._create_workflow_artifact_config_single(v, jinja_env_vars, include_merge) for k, v in artifact.items()
        }

    def _create_workflow_artifact_merge_config(self, artifact: dict, jinja_env_vars: dict | None = None) -> dict | bool:
        return {
            k: self._create_workflow_artifact_merge_config_single(v, jinja_env_vars) for k, v in artifact.items()
        }

    def _create_workflow_artifact_config_single(
        self,
        artifact: dict,
        jinja_env_vars: dict | None = None,
        include_merge: bool = False
    ) -> dict:
        out = {
            "name": self._fill_jinja(artifact["name"], jinja_env_vars),
            "retention-days": str(artifact.get("retention_days", "")),
            "include-hidden": str(artifact.get("include_hidden", "false")),
        }
        if include_merge:
            out["merge"] = self._create_merge(artifact, jinja_env_vars)
        return out

    def _create_workflow_artifact_merge_config_single(self, artifact: dict, jinja_env_vars: dict) -> dict | bool:
        return {
            "merge": self._create_merge(artifact, jinja_env_vars),
            "include-hidden": str(artifact.get("include_hidden", "false")),
            "retention-days": str(artifact.get("retention_days", "")),
        }

    def _create_merge(self, artifact: dict, jinja_env_vars: dict) -> dict | bool:
        return {
            "name": self._fill_jinja(artifact["merge"]["name"], jinja_env_vars),
            "pattern": self._fill_jinja(artifact["merge"]["pattern"], jinja_env_vars),
        } if "merge" in artifact else False

    def _fill_jinja(self, template: str, env_vars: dict | None = None) -> str:
        return self._branch_manager.fill_jinja_template(template, env_vars= self._jinja_env_vars | (env_vars or {}))

