"""Packaging metadata checks for optional integration extras."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.shared]


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _minimum_dependency_version(dependencies: list[str], package_name: str) -> str:
    prefix = f"{package_name}>="
    for dependency in dependencies:
        if not dependency.startswith(prefix):
            continue
        return dependency[len(prefix) :].split(";", 1)[0].split(",", 1)[0].strip()
    raise AssertionError(f"{package_name} minimum not found in {dependencies!r}")


def _dependency_entry(dependencies: list[str], package_name: str) -> str:
    prefix = f"{package_name}>="
    for dependency in dependencies:
        if dependency.startswith(prefix):
            return dependency
    raise AssertionError(f"{package_name} dependency not found in {dependencies!r}")


def _normalized_dependency_name(dependency: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", dependency)
    if match is None:
        raise AssertionError(f"Invalid dependency entry: {dependency!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def test_django_channels_extras_are_explicit_opt_ins() -> None:
    """Channels should be available without becoming a base Django dependency."""

    root_pyproject = _load_pyproject(PROJECT_ROOT / "pyproject.toml")
    django_pyproject = _load_pyproject(
        PROJECT_ROOT / "integrations" / "weft_django" / "pyproject.toml"
    )

    root_extras = root_pyproject["project"]["optional-dependencies"]
    django_extras = django_pyproject["project"]["optional-dependencies"]

    assert django_extras["channels"] == ["channels>=4.1,<5"]
    assert django_extras["realtime"] == django_extras["channels"]
    assert any(
        dependency.startswith("weft-django[channels]")
        for dependency in root_extras["django-channels"]
    )
    assert all("[channels]" not in dependency for dependency in root_extras["django"])
    assert all("[channels]" not in dependency for dependency in root_extras["all"])


def test_typed_package_markers_are_included_in_builds() -> None:
    root_pyproject = _load_pyproject(PROJECT_ROOT / "pyproject.toml")
    django_root = PROJECT_ROOT / "integrations" / "weft_django"
    django_pyproject = _load_pyproject(django_root / "pyproject.toml")

    assert (PROJECT_ROOT / "weft" / "py.typed").is_file()
    assert (django_root / "weft_django" / "py.typed").is_file()
    assert "weft/py.typed" in root_pyproject["tool"]["hatch"]["build"]["include"]
    assert (
        "weft_django/py.typed" in django_pyproject["tool"]["hatch"]["build"]["include"]
    )


@pytest.mark.parametrize("package_name", ["httpx", "httpx2"])
def test_llm_transport_dependencies_are_transitive(package_name: str) -> None:
    """LLM 0.33 must own its HTTP transport dependencies."""

    root_pyproject = _load_pyproject(PROJECT_ROOT / "pyproject.toml")

    direct_names = {
        _normalized_dependency_name(dependency)
        for dependency in root_pyproject["project"]["dependencies"]
    }

    assert package_name not in direct_names


def test_uv_constraints_are_not_direct_dependencies() -> None:
    """Resolution constraints must not become published dependency ownership."""

    root_pyproject = _load_pyproject(PROJECT_ROOT / "pyproject.toml")
    uv_constraints = root_pyproject["tool"]["uv"].get(
        "constraint-dependencies",
        [],
    )
    constraint_names = {
        _normalized_dependency_name(dependency) for dependency in uv_constraints
    }

    direct_dependencies = root_pyproject["project"]["dependencies"]
    optional_dependencies = root_pyproject["project"]["optional-dependencies"]
    direct_names = {
        _normalized_dependency_name(dependency) for dependency in direct_dependencies
    }
    direct_names.update(
        _normalized_dependency_name(dependency)
        for dependencies in optional_dependencies.values()
        for dependency in dependencies
    )

    assert constraint_names.isdisjoint(direct_names)


def test_root_extras_do_not_undercut_local_extension_versions() -> None:
    root_pyproject = _load_pyproject(PROJECT_ROOT / "pyproject.toml")
    docker_pyproject = _load_pyproject(
        PROJECT_ROOT / "extensions" / "weft_docker" / "pyproject.toml"
    )
    macos_pyproject = _load_pyproject(
        PROJECT_ROOT / "extensions" / "weft_macos_sandbox" / "pyproject.toml"
    )
    microsandbox_pyproject = _load_pyproject(
        PROJECT_ROOT / "extensions" / "weft_microsandbox" / "pyproject.toml"
    )

    root_extras = root_pyproject["project"]["optional-dependencies"]
    docker_version = _version_tuple(docker_pyproject["project"]["version"])
    macos_version = _version_tuple(macos_pyproject["project"]["version"])
    microsandbox_version = _version_tuple(microsandbox_pyproject["project"]["version"])
    pg_version = _version_tuple(
        _minimum_dependency_version(root_extras["pg"], "simplebroker-pg")
    )

    for extra in ("docker", "all", "dev"):
        assert (
            _version_tuple(
                _minimum_dependency_version(root_extras[extra], "weft-docker")
            )
            >= docker_version
        )

    for extra in ("macos-sandbox", "all", "dev"):
        assert (
            _version_tuple(
                _minimum_dependency_version(root_extras[extra], "weft-macos-sandbox")
            )
            >= macos_version
        )

    for extra in ("microsandbox", "all", "dev"):
        assert (
            _version_tuple(
                _minimum_dependency_version(root_extras[extra], "weft-microsandbox")
            )
            >= microsandbox_version
        )

    for extra in ("all", "dev"):
        assert (
            _version_tuple(
                _minimum_dependency_version(root_extras[extra], "simplebroker-pg")
            )
            >= pg_version
        )


def test_microsandbox_sdk_dependency_is_minor_bounded() -> None:
    """The SDK adapter depends on a tight 0.5.x Microsandbox API surface."""

    microsandbox_pyproject = _load_pyproject(
        PROJECT_ROOT / "extensions" / "weft_microsandbox" / "pyproject.toml"
    )

    dependency = _dependency_entry(
        microsandbox_pyproject["project"]["dependencies"],
        "microsandbox",
    )

    assert dependency == "microsandbox>=0.5.7,<0.6"
