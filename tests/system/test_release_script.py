"""Tests for the local release helper script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_release_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "release.py"
    spec = importlib.util.spec_from_file_location("weft_release_script", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load release script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version: release.ReleaseState(
            version=version,
            tag_name=f"v{version}",
            github_release_exists=False,
            pypi_release_exists=False,
            local_tag_commit=None,
            remote_tag_commit=None,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "a" * 40)

    exit_code = release.main(["--version", "0.1.1", "--publish", "--dry-run"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert release.RELEASE_GATE_WORKFLOW in captured.out
    assert "gh release create" not in captured.out


def test_resolve_target_version_reuses_current_when_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should reuse the current version until it is externally published."""
    release = _load_release_module()
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version: release.ReleaseState(
            version=version,
            tag_name=f"v{version}",
            github_release_exists=False,
            pypi_release_exists=False,
            local_tag_commit=None,
            remote_tag_commit=None,
        ),
    )

    target_version, state = release.resolve_target_version(
        None,
        current_version="0.1.0",
    )

    assert target_version == "0.1.0"
    assert state.tag_name == "v0.1.0"


def test_resolve_target_version_requires_new_version_after_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The current version cannot be reused after a GitHub Release or PyPI publish."""
    release = _load_release_module()
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version: release.ReleaseState(
            version=version,
            tag_name=f"v{version}",
            github_release_exists=True,
            pypi_release_exists=False,
            local_tag_commit=None,
            remote_tag_commit=None,
        ),
    )

    with pytest.raises(RuntimeError, match="Pass --version with a new version"):
        release.resolve_target_version(None, current_version="0.1.0")


def test_plan_tag_action_rejects_existing_tag_on_different_commit() -> None:
    """The helper should not silently move an existing unpublished tag."""
    release = _load_release_module()
    state = release.ReleaseState(
        version="0.1.0",
        tag_name="v0.1.0",
        github_release_exists=False,
        pypi_release_exists=False,
        local_tag_commit=None,
        remote_tag_commit="a" * 40,
    )

    with pytest.raises(RuntimeError, match="move the tag"):
        release.plan_tag_action(
            state,
            head_commit="b" * 40,
            version_changed=False,
        )


def test_main_dry_run_reuses_current_unpublished_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should allow the current unpublished version without a bump."""
    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version: release.ReleaseState(
            version=version,
            tag_name=f"v{version}",
            github_release_exists=False,
            pypi_release_exists=False,
            local_tag_commit=None,
            remote_tag_commit=None,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "a" * 40)

    exit_code = release.main(["--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "target:  0.1.0" in captured.out
    assert "would reuse existing version files" in captured.out
    assert "would update pyproject.toml" not in captured.out


def test_precheck_commands_cover_sqlite_and_postgres_release_gate() -> None:
    """The helper precheck should cover both release-gate backend suites."""
    release = _load_release_module()
    sqlite_command = release.PRECHECK_COMMANDS[0]
    postgres_command = release.PRECHECK_COMMANDS[1]

    assert sqlite_command[:5] == ("uv", "run", "pytest", "-v", "--tb=short")
    assert (
        "--override-ini=addopts=-ra -q --strict-markers -n auto --dist loadgroup"
        in sqlite_command
    )
    assert postgres_command == ("uv", "run", "bin/pytest-pg", "--all")
