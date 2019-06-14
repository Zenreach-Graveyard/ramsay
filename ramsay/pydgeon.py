#!/usr/bin/env python2
import argparse
import jinja2
import json
import logging
import pypi_get
import re
import semantic_version
import sys
from requirements import parse as parse_requirements
from ruamel.yaml import YAML

"""
Pydgeon generates pypi_rules.bzl files for rules_pyz.
"""
BOOTSTRAP_PLATFORMS = [ {
    "name": "linux",
    "os_names": ["manylinux1"],
    "architectures": ["x86_64"],
    "bdist": {
        "python_versions": ["py2.py3", "py2", "cp27mu", "cp27m", "cp27"],
        "package_types": ["bdist_wheel"],
    },
    "source": {
        "python_versions": ["source"],
        "package_types": ["sdist"],
    },
}, {
    "name": "osx",
    "os_names": ["macosx"],
    "architectures": ["x86_64"],
    "bdist": {
        "python_versions": ["py2.py3", "py2", "cp27mu", "cp27m", "cp27"],
        "package_types": ["bdist_wheel"],
    },
    "source": {
        "python_versions": ["source"],
        "package_types": ["sdist"],
    },
} ]

yaml = YAML(typ="safe")


def main(argv):
    args = parse_args(argv)
    init_logging(args.enable_debug)
    config = Config.from_args(args)
    pydgeon = Pydgeon(
            config.requirements,
            config.requirements_state,
            config.platforms)
    pydgeon.transform()


def parse_args(argv):
    # type: (list) -> argparse.Namespace
    parser = argparse.ArgumentParser(description="Generates pypi_rules.bzl files from a requirements.txt file.")
    parser.add_argument("--requirements",
                        metavar="FILE",
                        type=str,
                        default="requirements.txt",
                        required=False,
                        help="sets the requirements.txt file to process")
    parser.add_argument("--requirements-state",
                        metavar="STATE",
                        type=str,
                        default="requirements.json",
                        required=False,
                        help="sets the requirements state file")
    parser.add_argument("--rules-pyz-repo",
                        metavar="RULES_PYZ_REPO",
                        type=str,
                        default="com_bluecore_rules_pyz", 
                        required=False,
                        help="sets the repository name of the rules_pyz rules set")
    parser.add_argument("--rules",
                        metavar="RULES",
                        type=str,
                        default="3rdparty/pypi/pypi_rules.bzl",
                        required=False,
                        help="sets the relative path to the pypi_rules.bzl file")
    parser.add_argument("--wheels",
                        metavar="WHEELS",
                        type=str,
                        default="3rdparty/pypi/wheels",
                        required=False,
                        help="sets the relative path to the wheels directory")
    parser.add_argument("--debug",
                        dest="enable_debug",
                        action="store_true",
                        default=False,
                        help="logs debug information to stderr")
    return parser.parse_args(argv[1:])


def init_logging(enable_debug):
    # type: (bool) -> None
    logging.basicConfig(
        format="%(asctime)-15s: %(name)s: %(message)s",
        stream=sys.stderr,
        level=logging.DEBUG if enable_debug else logging.ERROR)


class Config:
    DEFAULT = {
        "requirements": "requirements.txt",
        "requirements_state": "requirements.json",
        "platforms": BOOTSTRAP_PLATFORMS[:],
        "enable_debug": False,
    }

    _logger = logging.getLogger(__name__)

    def __init__(self, requirements, requirements_state, platforms, enable_debug):
        self.requirements = requirements
        self.requirements_state = requirements_state
        self.platforms = platforms
        self.enable_debug = enable_debug

    @classmethod
    def from_args(cls, args):
        # type: (argparse.Namespace) -> Config
        args_as_dict = vars(args)
        args_as_dict.update({})
        cascaded_config = Config._cascade_configs(Config.DEFAULT.copy(), args_as_dict)
        cls._logger.debug("initial configuration:")
        for key in sorted(cascaded_config):
            cls._logger.debug("  %s:%s", key, cascaded_config[key])
        return Config(
                cascaded_config["requirements"],
                cascaded_config["requirements_state"],
                cascaded_config["platforms"],
                cascaded_config["enable_debug"])

    @classmethod
    def _cascade_configs(cls, dest, src):
        dest["requirements"] = src.get("requirements", dest["requirements"])
        dest["requirements_state"] = src.get("requirements_state", dest["requirements_state"])
        dest["platforms"] = src.get("platforms", dest["platforms"])
        dest["enable_debug"] = src.get("enable_debug", dest["enable_debug"])
        return dest


class Pydgeon:
    
    def __init__(self, requirements_fp, requirements_state_fp, platforms):
        self.requirements = []
        self.dependencies = {}
        self.requirements_fp = requirements_fp
        self.requirements_state_fp = requirements_state_fp
        self.platforms = platforms
        self.template = PypiRulesTemplate()

    def transform(self):
        requirements = []
        with open(self.requirements_fp, "r") as requirements_fd:
            requirements.extend(parse_requirements(requirements_fd))
        while len(requirements) > 0:
            requirement = requirements.pop(0)
            platform_packages, requires = None, []
            if requirement.editable:
                platform_packages = self._handle_editable_requirement(requirement)
            elif requirement.local_file:
                platform_packages = self._handle_local_file_requirement(requirement)
            else:
                (platform_packages, requires) = self._handle_requirement(requirement)
            dependencies = []
            for require in requires:
                dependency = list(parse_requirements(require))[0]
                requirements.append(dependency)
                dependencies.append(dependency.name)
            self._add_library(requirement, platform_packages, dependencies)
        print(str(self.template))


    def _handle_editable_requirement(self, requirement):
        # doesn't have a spec
        if not requirement.specs:
            print(requirement)

    def _handle_local_file_requirement(self, requirement):
        raise NotImplementedError()

    def _handle_requirement(self, requirement):
        (operator, version) = (None, None)
        if requirement.specs:
            (version_operator, version) = requirement.specs[0]
        pypi_info = pypi_get.get(requirement.name, version)
        (has_release, release, matched_version) = self._lookup_release(pypi_info, version, version_operator)
        if not has_release:
            raise ReleaseNotFound(requirement.name, version)
        platform_packages = {}
        platform_binary_package, platform_source_package = (None, None)
        for platform in self.platforms:
            platform_binary_package = self._select_platform_binary_package(release, matched_version, platform)
            platform_source_package = self._select_platform_source_package(release, matched_version, platform)
            platform_packages[platform["name"]] = {
                "bdist": None,
                "source": None,
            }
            if platform_binary_package is not None:
                platform_packages[platform["name"]]["bdist"] = platform_binary_package
                continue
            if platform_source_package is not None:
                platform_packages[platform["name"]]["source"] = platform_source_package
                continue
            raise PackageNotFound(requirement.name, matched_version, platform)
        # print(json.dumps(platform_packages, indent=4))
        dependencies = pypi_info["info"]["requires_dist"] or []
        return (platform_packages, dependencies)

    def _lookup_release(self, pypi_info, version, version_operator):
        version_spec = semantic_version.Spec("{}{}".format(version_operator, version))
        candidates = []
        for release in pypi_info["releases"]:
            if semantic_version.Version.coerce(release, partial=True) in version_spec:
                for rel in pypi_info["releases"][release]:
                    rel["version"] = release
                candidates.extend(pypi_info["releases"][release])
        if len(candidates) == 0:
            return (False, None)
        def semver_cmp(lhs, rhs):
            semver_lhs, semver_rhs = semantic_version.Version.coerce(lhs["version"]), semantic_version.Version.coerce(rhs["version"])
            if semver_lhs < semver_rhs:
                return -1
            elif semver_lhs == semver_rhs:
                return 0
            else:
                return 1
        candidates.sort(cmp=semver_cmp, reverse=True)
        return (True, candidates, candidates[0]["version"])

    def _select_platform_binary_package(self, release, version, platform):
        packages = []
        for package in release:
            matches_os_name = False
            for os_name in platform["os_names"]:
                if os_name in package["filename"]:
                    package["os_name"] = os_name
                    matches_os_name = True
                    break
            if not matches_os_name:
                continue
            matches_architecture = False
            for architecture in platform["architectures"]:
                if architecture in package["filename"]:
                    package["architecture"] = architecture
                    matches_architecture = True
            if not matches_architecture:
                continue
            if package["version"] != version:
                continue
            if package["packagetype"] not in platform["bdist"]["package_types"]:
                continue
            if package["python_version"] not in platform["bdist"]["python_versions"]:
                continue
            packages.append(package)
        if len(packages) > 0:
            def compare_package(lhs, rhs):
                lhs_python_build_prio = 0
                rhs_python_build_prio = 0
                for python_version in platform["bdist"]["python_versions"]:
                    if python_version not in rhs["filename"]:
                        rhs_python_build_prio += 1
                    if python_version not in lhs["filename"]:
                        lhs_python_build_prio += 1
                return lhs_python_build_prio - rhs_python_build_prio
            packages.sort(cmp=compare_package)
            return packages[0]
        else:
            return None

    def _select_platform_source_package(self, release, version, platform):
        packages = []
        for package in release:
            if package["packagetype"] not in platform["source"]["package_types"]:
                continue
            if package["python_version"] not in platform["source"]["python_versions"]:
                continue
            packages.append(package)
        if len(packages) > 0:
            return packages[0]
        else:
            return None

    def _add_library(self, requirement, platform_packages, dependencies):
        # print(json.dumps(vars(requirement), indent=4))
        # print(json.dumps(platform_packages, indent=4))
        # print(json.dumps(dependencies, indent=4))
        safe_requirement_name = self._to_safe_name(requirement.name)

        platform_deps = {}
        for os in platform_packages.keys():
            platform_deps["@com_bluecore_rules_pyz//rules_python_zip:{}".format(os)] = ["@pypi2_{}__{}//:lib".format(safe_requirement_name, os)]

        dependencies = [ self._to_safe_name(dependency) for dependency in dependencies]
        dependencies.sort()
        self.template.add_library(safe_requirement_name, dependencies, platform_deps)

        for os in platform_packages.keys():
            package = None
            if platform_packages[os]["bdist"] is not None:
                package = platform_packages[os]["bdist"]
            elif platform_packages[os]["source"] is not None:
                package = platform_packages[os]["source"]
            else:
                raise Exception("shouldn't be possible")
            self.template.add_repository(
                    "pypi2_{}__{}".format(safe_requirement_name, os),
                    package["url"],
                    package["digests"]["sha256"])

    def _to_safe_name(self, name):
        return re.sub(r"[^A-Za-z0-9]", "_", name)



class ReleaseNotFound(Exception):

    def __init__(self, module, version):
        super(ReleaseNotFound, self).__init__()
        self.module = module
        self.version = version

    def __str__(self):
        return "Failed to find v{} of {}".format(self.version, self.module)


class PackageNotFound(Exception):

    def __init__(self, module, version, platform):
        super(PackageNotFound, self).__init__()
        self.module = module
        self.version = version
        self.platform = platform

    def __str__(self):
        return "Failed to find os package {} for v{} of {}".format(self.platform["os_names"], self.version, self.module)


class PypiReleasePackage:

    def __init__(self, name, version, package_type, python_versions, url, sha256):
        self.name = name
        self.version = version
        self.package_type = package_type
        self.python_versions = python_versions
        self.url = url
        self.sha256 = sha256


class PypiRulesTemplate:
    _logger = logging.getLogger(__name__)

    def __init__(self):
        self.libraries = []
        self.repositories = []

    def add_library(self, name, deps, platform_deps):
        self.libraries.append({
            "name": name,
            "deps": deps,
            "platform_deps": platform_deps,
        })

    def add_repository(self, name, url, sha256):
        self.repositories.append({
            "name": name,
            "url": url,
            "sha256": sha256,
        })

    def __str__(self):
        self.libraries.sort(key=lambda x: x["name"])
        self.repositories.sort(key=lambda x: x["name"])
        loader = jinja2.DictLoader({
            "pypi_rules.bzl": """\
#
#   This file was auto-generated by pydgeon, the pypi_rules.bzl file generator for Python code.
#   DO NOT EDIT.
#
package(default_visibility=["//visibility:public"])

load("@com_bluecore_rules_pyz//rules_python_zip:rules_python_zip.bzl", "pyz_library")
load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

{% include "_BUILD_FILE_CONTENT" %}

{% include "pypi_libraries" %}

{% include "pypi_repositories" %}
""",
            "_BUILD_FILE_CONTENT": """\
_BUILD_FILE_CONTENT = '''
package(default_visibility=["//visibility:public"])

load("@com_bluecore_rules_pyz//rules_python_zip:rules_python_zip.bzl", "pyz_library")

pyz_library(
    name = "lib",
    srcs = glob(["**/*.py"]),
    data = glob(["**/*"], exclude = ["**/*.py", "BUILD", "WORKSPACE", "*.whl.zip"]),
    pythonroot = ".",
)
'''
""",
            "pypi_libraries": """\
def pypi_libraries():
{% for library in this.libraries %}
    {%- include "pypi_library" %}
{% endfor %}
""",
            "pypi_library": """\
    pyz_library(
        name = {{ library.name|tojson }},
        deps = {{ library.deps|tojson }}
        + select({{ library.platform_deps|tojson(4) }}),
        licenses = ["notice"],
    )
""",
            "pypi_repositories": """\
def pypi_repositories():
    existing_rules = native.existing_rules()
{% for repository in this.repositories %}
    {%- include "pypi_repository" %}
{% endfor %}
""",
            "pypi_repository": """\
    if {{ repository.name|tojson }} not in existing_rules:
        http_archive(
            name = {{ repository.name|tojson }},
            url = {{ repository.url|tojson }},
            sha256 = {{ repository.sha256|tojson }},
            build_file_content = _BUILD_FILE_CONTENT,
            type = "zip",
        )
""",
        })
        env = jinja2.Environment(loader=loader)
        template = env.get_template("pypi_rules.bzl")
        return template.render(this=self)


if __name__ == "__main__":
    main(sys.argv)
