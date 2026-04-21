"""Packaging metadata checks for optional integration extras."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.shared]


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


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
