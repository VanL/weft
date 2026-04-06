"""Tests for the local release helper script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_release_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "release.py"
    spec = importlib.util.spec_from_file_location("weft_release_script", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load release script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_version_files_updates_pyproject_and_constants(tmp_path: Path) -> None:
    """The helper should update both canonical version sources together."""
    release = _load_release_module()
    pyproject_path = tmp_path / "pyproject.toml"
    constants_path = tmp_path / "_constants.py"

    pyproject_path.write_text(
        '[project]\nname = "weft"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    constants_path.write_text(
        'from typing import Final\n__version__: Final[str] = "0.1.0"\n',
        encoding="utf-8",
    )

    release.write_version_files(
        "0.1.1",
        pyproject_path=pyproject_path,
        constants_path=constants_path,
    )

    assert 'version = "0.1.1"' in pyproject_path.read_text(encoding="utf-8")
    assert '__version__: Final[str] = "0.1.1"' in constants_path.read_text(
        encoding="utf-8"
    )


def test_read_current_version_rejects_mismatch(tmp_path: Path) -> None:
    """The helper should fail fast if canonical version files already drifted."""
    release = _load_release_module()
    pyproject_path = tmp_path / "pyproject.toml"
    constants_path = tmp_path / "_constants.py"

    pyproject_path.write_text(
        '[project]\nname = "weft"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    constants_path.write_text(
        'from typing import Final\n__version__: Final[str] = "0.1.1"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Version mismatch"):
        release.read_current_version(
            pyproject_path=pyproject_path,
            constants_path=constants_path,
        )


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1.1", "0.1.1"),
        ("  1.2.3  ", "1.2.3"),
    ],
)
def test_validate_version_accepts_explicit_semver(
    version: str,
    expected: str,
) -> None:
    """The helper should accept the strict version format it documents."""
    release = _load_release_module()
    assert release.validate_version(version) == expected


@pytest.mark.parametrize("version", ["v0.1.1", "0.1", "0.1.1rc1", "alpha"])
def test_validate_version_rejects_invalid_values(version: str) -> None:
    """The helper should reject non-X.Y.Z versions."""
    release = _load_release_module()
    with pytest.raises(ValueError, match="X.Y.Z"):
        release.validate_version(version)


def test_main_dry_run_publish_flag_defers_to_release_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The helper should no longer create GitHub releases directly."""
    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)

    exit_code = release.main(["--version", "0.1.1", "--publish", "--dry-run"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert release.RELEASE_GATE_WORKFLOW in captured.out
    assert "gh release create" not in captured.out
