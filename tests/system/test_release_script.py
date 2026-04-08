"""Tests for the local release helper script."""

from __future__ import annotations

import importlib.util
import subprocess
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

    with pytest.raises(RuntimeError, match="move the remote tag"):
        release.plan_tag_action(
            state,
            head_commit="b" * 40,
            version_changed=False,
            allow_retag=False,
        )


def test_plan_tag_action_replaces_stale_local_tag() -> None:
    """A stale local-only tag should be deleted and recreated automatically."""
    release = _load_release_module()
    state = release.ReleaseState(
        version="0.1.0",
        tag_name="v0.1.0",
        github_release_exists=False,
        pypi_release_exists=False,
        local_tag_commit="a" * 40,
        remote_tag_commit=None,
    )

    assert (
        release.plan_tag_action(
            state,
            head_commit="b" * 40,
            version_changed=False,
            allow_retag=False,
        )
        == "replace_local"
    )


def test_plan_tag_action_replaces_remote_tag_only_with_retag() -> None:
    """A stale remote tag should require explicit `--retag`."""
    release = _load_release_module()
    state = release.ReleaseState(
        version="0.1.0",
        tag_name="v0.1.0",
        github_release_exists=False,
        pypi_release_exists=False,
        local_tag_commit="a" * 40,
        remote_tag_commit="a" * 40,
    )

    assert (
        release.plan_tag_action(
            state,
            head_commit="b" * 40,
            version_changed=False,
            allow_retag=True,
        )
        == "replace_remote"
    )


def test_github_api_auth_headers_use_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub release lookups should use the current API token when present."""

    release = _load_release_module()
    release._github_api_token.cache_clear()
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    headers = release._github_api_auth_headers()

    assert headers == {"Authorization": "Bearer env-token"}


def test_github_api_auth_headers_fall_back_to_gh_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should fall back to `gh auth token` for authenticated lookups."""

    release = _load_release_module()
    release._github_api_token.cache_clear()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(release.shutil, "which", lambda name: "/opt/homebrew/bin/gh")
    monkeypatch.setattr(
        release,
        "_capture_command",
        lambda command: subprocess.CompletedProcess(
            command,
            0,
            stdout="gh-token\n",
            stderr="",
        ),
    )

    headers = release._github_api_auth_headers()

    assert headers == {"Authorization": "Bearer gh-token"}


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


def test_main_dry_run_deletes_stale_local_tag_before_recreating(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should show automatic cleanup of a stale local-only tag."""
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
            local_tag_commit="a" * 40,
            remote_tag_commit=None,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "b" * 40)

    exit_code = release.main(["--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "$ git tag -d v0.1.0" in captured.out
    assert "$ git tag v0.1.0" in captured.out


def test_main_dry_run_stages_uv_lock_for_release_commit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should include `uv.lock` in the release commit staging set."""
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

    exit_code = release.main(["--version", "0.1.1", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "$ git add pyproject.toml weft/_constants.py uv.lock" in captured.out


def test_main_dry_run_retags_remote_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should show remote tag deletion only when `--retag` is set."""
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
            local_tag_commit="a" * 40,
            remote_tag_commit="a" * 40,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "b" * 40)

    exit_code = release.main(["--dry-run", "--retag"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "$ git push --delete origin v0.1.0" in captured.out
    assert "$ git tag -d v0.1.0" in captured.out
    assert "$ git tag v0.1.0" in captured.out


def test_build_precheck_commands_cover_release_gate_and_quality_gates() -> None:
    """The helper precheck should always cover the core release-gate suites."""
    release = _load_release_module()
    commands = release.build_precheck_commands(
        include_docker_extension_tests=False,
        include_macos_sandbox_extension_tests=False,
    )
    sqlite_command = commands[0]
    postgres_command = commands[1]
    ruff_check_command = commands[2]
    ruff_format_command = commands[3]
    mypy_command = commands[4]

    assert sqlite_command[:11] == (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "pytest",
        "-v",
        "--tb=short",
    )
    assert "-m" in sqlite_command
    marker_index = sqlite_command.index("-m")
    assert sqlite_command[marker_index + 1] == ""
    assert (
        "--override-ini=addopts=-ra -q --strict-markers -n auto --dist loadgroup"
        in sqlite_command
    )
    assert postgres_command == (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "bin/pytest-pg",
        "--all",
    )
    assert ruff_check_command == (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "ruff",
        "check",
        "weft",
        "tests",
        "extensions/weft_docker",
        "extensions/weft_macos_sandbox",
    )
    assert ruff_format_command == (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "ruff",
        "format",
        "--check",
        "weft",
        "tests",
        "extensions/weft_docker",
        "extensions/weft_macos_sandbox",
    )
    assert mypy_command == (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "mypy",
        "weft",
        "extensions/weft_docker/weft_docker",
        "extensions/weft_macos_sandbox/weft_macos_sandbox",
        "--config-file",
        "pyproject.toml",
    )
    assert release.PRECHECK_ENV_OVERRIDES == {
        "PYTEST_ADDOPTS": "-x --maxfail=1",
        "WEFT_EAGER_FAILURE_TRACEBACK": "1",
    }


def test_build_precheck_commands_include_extension_tests_when_supported() -> None:
    """The helper should add extension-local tests only on capable hosts."""

    release = _load_release_module()

    commands = release.build_precheck_commands(
        include_docker_extension_tests=True,
        include_macos_sandbox_extension_tests=True,
    )

    assert commands[2] == release.DOCKER_EXTENSION_TEST_COMMAND
    assert commands[3] == release.MACOS_SANDBOX_EXTENSION_TEST_COMMAND


def test_build_precheck_commands_skip_extension_tests_when_unavailable() -> None:
    """Unavailable local runners should not block a release-helper precheck."""

    release = _load_release_module()

    commands = release.build_precheck_commands(
        include_docker_extension_tests=False,
        include_macos_sandbox_extension_tests=False,
    )

    assert release.DOCKER_EXTENSION_TEST_COMMAND not in commands
    assert release.MACOS_SANDBOX_EXTENSION_TEST_COMMAND not in commands


def test_docker_extension_tests_are_disabled_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows release runs should not schedule Docker extension tests."""

    release = _load_release_module()
    monkeypatch.setattr(release.os, "name", "nt")

    assert release._docker_available_for_tests() is False


def test_merge_command_env_appends_pytest_addopts() -> None:
    """Precheck env overrides should preserve existing pytest addopts."""

    release = _load_release_module()

    merged = release._merge_command_env(
        release.PRECHECK_ENV_OVERRIDES,
        base_env={
            "PATH": "/tmp/bin",
            "PYTEST_ADDOPTS": "--lf",
        },
    )

    assert merged is not None
    assert merged["PATH"] == "/tmp/bin"
    assert merged["PYTEST_ADDOPTS"] == "--lf -x --maxfail=1"
    assert merged["WEFT_EAGER_FAILURE_TRACEBACK"] == "1"


def test_run_command_dry_run_shows_env_prefix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run command logging should show precheck env overrides explicitly."""

    release = _load_release_module()
    monkeypatch.setattr(release.subprocess, "run", lambda *args, **kwargs: None)

    release.run_command(
        ("pytest", "-q"),
        dry_run=True,
        env_overrides=release.PRECHECK_ENV_OVERRIDES,
    )

    captured = capsys.readouterr()
    assert "PYTEST_ADDOPTS='-x --maxfail=1'" in captured.out
    assert "WEFT_EAGER_FAILURE_TRACEBACK=1" in captured.out
    assert "pytest -q" in captured.out
