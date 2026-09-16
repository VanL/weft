"""Tests for the local release helper script."""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml


def _load_release_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "release.py"
    spec = importlib.util.spec_from_file_location("weft_release_script", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load release script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _release_state(
    release: ModuleType,
    *,
    version: str,
    tag_name: str,
    target: Any = None,  # ReleaseTarget is loaded from an extensionless script.
    github_release_exists: bool = False,
    pypi_release_exists: bool = False,
    local_tag_commit: str | None = None,
    remote_tag_commit: str | None = None,
) -> Any:  # ReleaseState belongs to the dynamically loaded script module.
    return release.ReleaseState(
        target=release.ROOT_RELEASE_TARGET if target is None else target,
        version=version,
        tag_name=tag_name,
        github_release_exists=github_release_exists,
        pypi_release_exists=pypi_release_exists,
        local_tag_commit=local_tag_commit,
        remote_tag_commit=remote_tag_commit,
    )


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo", "owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git.git", "owner/repo.git"),
        ("git@example.com:owner/repo.git", None),
    ],
)
def test_github_repo_slug_from_remote_removes_one_git_suffix(
    remote_url: str,
    expected: str | None,
) -> None:
    release = _load_release_module()

    assert release.github_repo_slug_from_remote(remote_url) == expected


def test_write_version_files_updates_pyproject_and_constants(tmp_path: Path) -> None:
    """The helper should update both canonical root-package version sources."""

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


def test_write_target_version_updates_extension_pyproject_only(tmp_path: Path) -> None:
    """Extension packages should version from their own pyproject only."""

    release = _load_release_module()
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "weft-docker"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    target = release.ReleaseTarget(
        key="docker",
        package_name="weft-docker",
        display_name="weft-docker",
        package_dir=tmp_path,
        pyproject_path=pyproject_path,
        tag_namespace="weft_docker",
        release_gate_workflow=".github/workflows/release-gate-docker.yml",
    )

    release.write_target_version(target, "0.1.1")

    assert 'version = "0.1.1"' in pyproject_path.read_text(encoding="utf-8")


def test_read_current_version_rejects_mismatch(tmp_path: Path) -> None:
    """The helper should fail fast if canonical root version files drifted."""

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


def test_inspect_release_state_uses_target_package_name_and_tag_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target-specific tag namespaces and package names should be preserved."""

    release = _load_release_module()
    monkeypatch.setattr(release, "github_release_exists", lambda tag_name: False)
    monkeypatch.setattr(
        release,
        "pypi_version_exists",
        lambda package_name, version: (
            package_name == "weft-docker" and version == "0.1.0"
        ),
    )
    monkeypatch.setattr(release, "local_tag_commit", lambda tag_name: "a" * 40)
    monkeypatch.setattr(release, "remote_tag_commit", lambda tag_name: None)

    state = release.inspect_release_state("0.1.0", target=release.DOCKER_RELEASE_TARGET)

    assert state.tag_name == "weft_docker/v0.1.0"
    assert state.pypi_release_exists is True
    assert state.target is release.DOCKER_RELEASE_TARGET


def test_django_release_target_uses_namespaced_tag_and_package_dir() -> None:
    """The Django integration should publish like the other first-party packages."""

    release = _load_release_module()

    assert release.DJANGO_RELEASE_TARGET.package_name == "weft-django"
    assert release.DJANGO_RELEASE_TARGET.tag_name("0.1.0") == "weft_django/v0.1.0"
    assert release.DJANGO_RELEASE_TARGET.package_dir == release.DJANGO_INTEGRATION_DIR


def test_main_rejects_removed_publish_flag() -> None:
    """The obsolete direct-publish spelling is not part of the CLI contract."""

    release = _load_release_module()

    with pytest.raises(SystemExit) as exc_info:
        release.main(["--publish"])

    assert exc_info.value.code == 2


def test_resolve_target_version_reuses_current_when_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should reuse the current version until it is externally published."""

    release = _load_release_module()
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
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
    """The current version cannot be reused after publication."""

    release = _load_release_module()
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
            github_release_exists=target.github_release_enabled,
            pypi_release_exists=not target.github_release_enabled,
        ),
    )

    with pytest.raises(RuntimeError, match="Pass --version with a new version"):
        release.resolve_target_version(None, current_version="0.1.0")


def test_plan_tag_action_rejects_existing_tag_on_different_commit() -> None:
    """The helper should not silently move an existing unpublished tag."""

    release = _load_release_module()
    state = _release_state(
        release,
        version="0.1.0",
        tag_name="v0.1.0",
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
    state = _release_state(
        release,
        version="0.1.0",
        tag_name="v0.1.0",
        local_tag_commit="a" * 40,
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
    """A stale remote tag should require explicit ``--retag``."""

    release = _load_release_module()
    state = _release_state(
        release,
        version="0.1.0",
        tag_name="v0.1.0",
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


def test_collect_extension_release_plans_skips_already_published_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Published extension versions should not have their tags pushed again."""

    release = _load_release_module()
    monkeypatch.setattr(
        release,
        "read_target_version",
        lambda target: (
            "0.1.0"
            if target is release.DOCKER_RELEASE_TARGET
            else "0.2.0"
            if target is release.DJANGO_RELEASE_TARGET
            else "0.3.0"
        ),
    )
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
            pypi_release_exists=target is release.DOCKER_RELEASE_TARGET,
        ),
    )

    plans, skipped = release.collect_extension_release_plans(
        head_commit="a" * 40,
        allow_retag=False,
    )

    assert len(plans) == 3
    assert plans[0].state.target is release.DJANGO_RELEASE_TARGET
    assert plans[1].state.target is release.MACOS_SANDBOX_RELEASE_TARGET
    assert plans[2].state.target is release.MICROSANDBOX_RELEASE_TARGET
    assert len(skipped) == 1
    assert skipped[0].target is release.DOCKER_RELEASE_TARGET


def test_post_ci_revalidation_rejects_publication_or_tag_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every requested target must remain unchanged through the CI wait."""

    release = _load_release_module()
    initial = _release_state(
        release,
        version="0.1.0",
        tag_name="v0.1.0",
        target=release.ROOT_RELEASE_TARGET,
        remote_tag_commit="a" * 40,
    )
    published = dataclasses.replace(initial, pypi_release_exists=True)
    monkeypatch.setattr(
        release, "inspect_release_state", lambda *args, **kwargs: published
    )

    with pytest.raises(RuntimeError, match="was published while release CI ran"):
        release.revalidate_release_states_after_ci(
            (initial,),
            head_commit="b" * 40,
            allow_retag=True,
        )

    moved = dataclasses.replace(initial, remote_tag_commit="c" * 40)
    monkeypatch.setattr(release, "inspect_release_state", lambda *args, **kwargs: moved)

    with pytest.raises(RuntimeError, match="Tag state.*changed"):
        release.revalidate_release_states_after_ci(
            (initial,),
            head_commit="b" * 40,
            allow_retag=True,
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
    """The helper should fall back to ``gh auth token`` for authenticated lookups."""

    release = _load_release_module()
    release._github_api_token.cache_clear()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(release.shutil, "which", lambda name: "/opt/homebrew/bin/gh")
    monkeypatch.setattr(
        release,
        "_capture_command",
        lambda command, cwd=release.PROJECT_ROOT: subprocess.CompletedProcess(
            command,
            0,
            stdout="gh-token\n",
            stderr="",
        ),
    )

    headers = release._github_api_auth_headers()

    assert headers == {"Authorization": "Bearer gh-token"}


def test_matching_test_runs_accepts_only_exact_main_push_and_selects_latest() -> None:
    """Release CI proof must not accept PR, dispatch, branch, or stale-SHA runs."""

    release = _load_release_module()
    sha = "a" * 40
    matching_old = {
        "id": 1,
        "head_sha": sha,
        "head_branch": "main",
        "event": "push",
        "created_at": "2026-09-16T10:00:00Z",
    }
    matching_new = {
        "id": 2,
        "head_sha": sha,
        "head_branch": "main",
        "event": "push",
        "created_at": "2026-09-16T11:00:00Z",
    }
    rejected = [
        {**matching_new, "id": 3, "event": "pull_request"},
        {**matching_new, "id": 4, "event": "workflow_dispatch"},
        {**matching_new, "id": 5, "head_branch": "feature"},
        {**matching_new, "id": 6, "head_sha": "b" * 40},
    ]

    runs = release._matching_test_runs(
        [matching_old, *rejected, matching_new],
        release_sha=sha,
    )

    assert runs == (matching_new, matching_old)


def test_wait_for_test_workflow_fails_immediately_on_completed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known failed Test run must stop release without sleeping or tagging."""

    release = _load_release_module()
    sha = "a" * 40
    monkeypatch.setattr(release, "_github_api_token", lambda: "token")
    monkeypatch.setattr(
        release,
        "_fetch_test_runs",
        lambda release_sha, *, token: [
            {
                "id": 1,
                "head_sha": release_sha,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.test/run/1",
            }
        ],
    )
    monkeypatch.setattr(
        release.time,
        "sleep",
        lambda seconds: pytest.fail(f"must not sleep after failure: {seconds}"),
    )

    with pytest.raises(RuntimeError, match="concluded failure"):
        release.wait_for_test_workflow(sha)


def test_main_dry_run_reuses_current_unpublished_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should allow the current unpublished version without a bump."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "read_target_version", lambda target: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
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
    """Dry-run should show automatic cleanup of a stale local-only root tag."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "read_target_version", lambda target: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)

    def inspect(version: str, *, target: Any = release.ROOT_RELEASE_TARGET) -> Any:
        return _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
            local_tag_commit="a" * 40
            if target is release.ROOT_RELEASE_TARGET
            else None,
        )

    monkeypatch.setattr(release, "inspect_release_state", inspect)
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
    """Dry-run should include ``uv.lock`` in the release commit staging set."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "read_target_version", lambda target: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
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
    """Dry-run should lease-protect an explicitly requested remote retag."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(release, "read_target_version", lambda target: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
            local_tag_commit="a" * 40,
            remote_tag_commit="a" * 40,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "b" * 40)

    exit_code = release.main(["--dry-run", "--retag"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "$ git tag -d v0.1.0" in captured.out
    assert "$ git tag v0.1.0" in captured.out
    assert "--force-with-lease=refs/tags/v0.1.0:" in captured.out


def test_main_dry_run_pushes_unpublished_extension_tags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run should push unpublished first-party extension tags too."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_current_version", lambda: "0.1.0")
    monkeypatch.setattr(
        release,
        "read_target_version",
        lambda target: (
            "0.2.0"
            if target is release.DOCKER_RELEASE_TARGET
            else "0.3.0"
            if target is release.DJANGO_RELEASE_TARGET
            else "0.4.0"
        ),
    )
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "a" * 40)

    exit_code = release.main(["all", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "weft_docker/v0.2.0" in captured.out
    assert "weft_django/v0.3.0" in captured.out
    assert "weft_macos_sandbox/v0.4.0" in captured.out
    assert "$ git push origin weft_docker/v0.2.0" in captured.out
    assert "$ git push origin weft_django/v0.3.0" in captured.out
    assert "$ git push origin weft_macos_sandbox/v0.4.0" in captured.out


def test_main_dry_run_defaults_to_core_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default release target must not publish extension packages."""

    release = _load_release_module()
    monkeypatch.setattr(release, "read_target_version", lambda target: "0.1.0")
    monkeypatch.setattr(release, "is_dirty_worktree", lambda: False)
    monkeypatch.setattr(
        release,
        "inspect_release_state",
        lambda version, *, target=release.ROOT_RELEASE_TARGET: _release_state(
            release,
            version=version,
            tag_name=target.tag_name(version),
            target=target,
        ),
    )
    monkeypatch.setattr(release, "current_head_commit", lambda: "a" * 40)

    assert release.main(["--dry-run", "--skip-checks"]) == 0

    output = capsys.readouterr().out
    assert "$ git push origin v0.1.0" in output
    assert "weft_docker/v" not in output
    assert "weft_django/v" not in output
    assert "weft_macos_sandbox/v" not in output
    assert "weft_microsandbox/v" not in output
    assert output.index("$ git push origin main") < output.index(
        "would wait for the exact main/master push run"
    )
    assert output.index("would wait for the exact main/master push run") < output.index(
        "$ git tag v0.1.0"
    )


def test_build_precheck_commands_cover_release_gate_and_quality_gates() -> None:
    """The helper precheck should always cover the core release-gate suites."""

    release = _load_release_module()
    commands = release.build_precheck_commands(
        include_docker_extension_tests=False,
        include_macos_sandbox_extension_tests=False,
    )
    sqlite_command = next(
        command for command in commands if "pytest" in command and "-m" in command
    )
    postgres_command = next(
        command for command in commands if "bin/pytest-pg" in command
    )
    ruff_check_command = next(
        command for command in commands if "ruff" in command and "check" in command
    )
    ruff_format_command = next(
        command for command in commands if "ruff" in command and "format" in command
    )
    mypy_command = next(command for command in commands if "mypy" in command)
    live_provider_command = next(
        command for command in commands if "bin/pytest-live-providers" in command
    )

    # Gate coverage matters; harmless reordering of extras and gate commands does not.
    for command in (
        sqlite_command,
        postgres_command,
        ruff_check_command,
        ruff_format_command,
        mypy_command,
    ):
        assert command[:2] == ("uv", "run")
        extras = {
            command[index + 1] for index, arg in enumerate(command) if arg == "--extra"
        }
        assert extras == {"dev", "docker", "django", "macos-sandbox", "microsandbox"}
    assert sqlite_command[sqlite_command.index("-m") + 1] == ""
    assert "--all" in postgres_command
    pytest_targets = {
        arg
        for command in commands
        if "pytest" in command
        for arg in command
        if arg.endswith("/tests")
    }
    assert pytest_targets == {
        "integrations/weft_django/tests",
        "extensions/weft_microsandbox/tests",
    }
    quality_targets = {
        "weft",
        "tests",
        "integrations/weft_django",
        "extensions/weft_docker",
        "extensions/weft_macos_sandbox",
        "extensions/weft_microsandbox",
    }
    for command in (ruff_check_command, ruff_format_command):
        assert quality_targets <= set(command)
    assert "--check" in ruff_format_command
    assert {
        "weft",
        "tests",
        "bin",
        "integrations/weft_django/weft_django",
        "extensions/weft_docker/weft_docker",
        "extensions/weft_macos_sandbox/weft_macos_sandbox",
        "extensions/weft_microsandbox/weft_microsandbox",
    } <= set(mypy_command)
    assert mypy_command[mypy_command.index("--config-file") + 1] == "pyproject.toml"
    assert live_provider_command[:2] == ("uv", "run")
    assert "python" in live_provider_command
    assert "dev" in live_provider_command
    assert release.PRECHECK_ENV_OVERRIDES == {
        "PYTEST_ADDOPTS": "-x --maxfail=1",
        "WEFT_EAGER_FAILURE_TRACEBACK": "1",
        "WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS": "0",
        "WEFT_RUN_LIVE_PROVIDER_CLI_TESTS": "0",
    }


def test_build_precheck_env_overrides_sets_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release prechecks should use the repo helper's xdist worker count."""

    release = _load_release_module()
    monkeypatch.setattr(release, "_pytest_worker_count", lambda: "17")

    assert release.build_precheck_env_overrides() == {
        "PYTEST_ADDOPTS": "-x --maxfail=1",
        "WEFT_EAGER_FAILURE_TRACEBACK": "1",
        "WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS": "0",
        "WEFT_RUN_LIVE_PROVIDER_CLI_TESTS": "0",
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "17",
    }


def test_build_postupdate_steps_build_all_publishable_packages() -> None:
    """Post-update verification should build every publishable first-party package."""

    release = _load_release_module()

    steps = release.build_postupdate_steps()

    assert steps[0].command == (
        "uv",
        "run",
        "pytest",
        "tests/system/test_constants.py",
        "-q",
    )
    assert steps[1] == release.CommandStep(("uv", "build"), cwd=release.PROJECT_ROOT)
    assert steps[2] == release.CommandStep(
        ("uv", "build"),
        cwd=release.DJANGO_INTEGRATION_DIR,
    )
    assert steps[3] == release.CommandStep(
        ("uv", "build"),
        cwd=release.DOCKER_EXTENSION_DIR,
    )
    assert steps[4] == release.CommandStep(
        ("uv", "build"),
        cwd=release.MACOS_SANDBOX_EXTENSION_DIR,
    )
    assert steps[5] == release.CommandStep(
        ("uv", "build"),
        cwd=release.MICROSANDBOX_EXTENSION_DIR,
    )


def test_build_precheck_commands_include_extension_tests_when_supported() -> None:
    """The helper should add extension-local tests only on capable hosts."""

    release = _load_release_module()
    commands = release.build_precheck_commands(
        include_docker_extension_tests=True,
        include_macos_sandbox_extension_tests=True,
    )

    targets = {
        arg
        for command in commands
        if "pytest" in command
        for arg in command
        if arg.endswith("/tests")
    }
    assert targets == {
        "integrations/weft_django/tests",
        "extensions/weft_microsandbox/tests",
        "extensions/weft_docker/tests",
        "extensions/weft_macos_sandbox/tests",
    }


def test_build_precheck_commands_skip_extension_tests_when_unavailable() -> None:
    """Unavailable local runners should not block a release-helper precheck."""

    release = _load_release_module()
    commands = release.build_precheck_commands(
        include_docker_extension_tests=False,
        include_macos_sandbox_extension_tests=False,
    )

    targets = {
        arg
        for command in commands
        if "pytest" in command
        for arg in command
        if arg.endswith("/tests")
    }
    assert targets == {
        "integrations/weft_django/tests",
        "extensions/weft_microsandbox/tests",
    }


def test_docker_extension_tests_are_disabled_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows release runs should not schedule Docker extension tests."""

    release = _load_release_module()

    class _WindowsOS:
        """Delegate to the real os module while reporting a Windows os.name.

        Patch the release module's ``os`` binding, never the global os module:
        a global ``os.name = "nt"`` lets pathlib hand out WindowsPath objects
        on POSIX and corrupts concurrent lazy imports in this process.
        """

        def __init__(self) -> None:
            self.name = "nt"

        def __getattr__(self, attr: str) -> Any:
            return getattr(os, attr)

    monkeypatch.setattr(release, "os", _WindowsOS())

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
    assert merged["UV_PROJECT_ENVIRONMENT"] == str(release.PROJECT_VENV_PATH)
    assert merged["PYTEST_ADDOPTS"] == "--lf -x --maxfail=1"
    assert merged["WEFT_EAGER_FAILURE_TRACEBACK"] == "1"


def test_merge_command_env_drops_non_project_virtualenv() -> None:
    """Release helper commands should not inherit an unrelated active venv."""

    release = _load_release_module()
    merged = release._merge_command_env(
        None,
        base_env={
            "PATH": "/tmp/bin",
            "VIRTUAL_ENV": "/tmp/unrelated-venv",
        },
    )

    assert merged["UV_PROJECT_ENVIRONMENT"] == str(release.PROJECT_VENV_PATH)
    assert "VIRTUAL_ENV" not in merged


def test_run_command_dry_run_shows_env_prefix_and_cwd(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run command logging should show env overrides and non-root cwd."""

    release = _load_release_module()

    def unexpected_execution(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not execute the command")

    monkeypatch.setattr(release.subprocess, "run", unexpected_execution)

    release.run_command(
        ("pytest", "-q"),
        cwd=release.DOCKER_EXTENSION_DIR,
        dry_run=True,
        env_overrides=release.PRECHECK_ENV_OVERRIDES,
    )

    captured = capsys.readouterr()
    assert "PYTEST_ADDOPTS='-x --maxfail=1'" in captured.out
    assert "WEFT_EAGER_FAILURE_TRACEBACK=1" in captured.out
    assert "pytest -q" in captured.out
    assert "(cwd=extensions/weft_docker)" in captured.out


def test_ci_runs_one_staged_test_graph_before_release() -> None:
    """Main proof must precede parallel extensions and release gates run no tests."""

    root = Path(__file__).resolve().parents[2]
    test_workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    )
    jobs = test_workflow["jobs"]
    main_jobs = {"test", "test-postgres", "coverage", "lint"}
    extension_jobs = {
        "test-django-integration",
        "test-docker-extension",
        "test-macos-sandbox-extension",
        "test-microsandbox-extension",
    }

    assert main_jobs | extension_jobs <= jobs.keys()
    for job_name in extension_jobs:
        assert set(jobs[job_name]["needs"]) == main_jobs
        assert "if" not in jobs[job_name]

    discovered = set((root / "tests").rglob("test*.py"))
    for job_name in ("test", "test-postgres"):
        included: list[Path] = []
        for row in jobs[job_name]["strategy"]["matrix"]["include"]:
            for target_text in row["pytest_targets"].split():
                target = root / target_text
                assert target.exists(), target
                included.extend(
                    sorted(target.rglob("test*.py")) if target.is_dir() else [target]
                )
        assert len(included) == len(set(included)), f"duplicate targets in {job_name}"
        assert set(included) == discovered

    release_gate_paths = sorted(
        (root / ".github" / "workflows").glob("release-gate*.yml")
    )
    assert release_gate_paths
    for workflow_path in release_gate_paths:
        release_workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in release_workflow["jobs"].values():
            for step in job.get("steps", []):
                assert "pytest" not in step.get("run", "")


def test_release_gate_routes_each_tag_to_exactly_one_package() -> None:
    """Each immutable package tag must invoke only its matching publication."""

    root = Path(__file__).resolve().parents[2]
    expected = {
        "release-gate.yml": ("v*", "weft", ".", True),
        "release-gate-django.yml": (
            "weft_django/v*",
            "weft-django",
            "integrations/weft_django",
            False,
        ),
        "release-gate-docker.yml": (
            "weft_docker/v*",
            "weft-docker",
            "extensions/weft_docker",
            False,
        ),
        "release-gate-macos-sandbox.yml": (
            "weft_macos_sandbox/v*",
            "weft-macos-sandbox",
            "extensions/weft_macos_sandbox",
            False,
        ),
        "release-gate-microsandbox.yml": (
            "weft_microsandbox/v*",
            "weft-microsandbox",
            "extensions/weft_microsandbox",
            False,
        ),
    }
    gate_paths = sorted((root / ".github" / "workflows").glob("release-gate*.yml"))
    assert {path.name for path in gate_paths} == expected.keys()

    for path in gate_paths:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        tag, package, package_dir, github_release = expected[path.name]
        assert workflow[True]["push"]["tags"] == [tag]
        assert set(workflow["jobs"]) == {"publish-release"}
        publish = workflow["jobs"]["publish-release"]
        assert publish["uses"] == "./.github/workflows/release.yml"
        assert publish["with"] == {
            "package_name": package,
            "package_dir": package_dir,
            "tag_name": "${{ github.ref_name }}",
            "release_ref": "${{ github.sha }}",
            "expected_tag_commit": "${{ github.sha }}",
            "create_github_release": github_release,
        }
        assert publish["permissions"]["actions"] == "read"


def test_publish_checks_completed_main_push_once_without_polling() -> None:
    """Publication must fail closed on one exact completed Test push lookup."""

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    verification = jobs["verify-main-test-workflow"]
    verification_step = next(
        step
        for step in verification["steps"]
        if step.get("uses", "").startswith("actions/github-script@")
    )
    script = verification_step["with"]["script"]

    assert verification_step["env"]["EXPECTED_SHA"] == (
        "${{ inputs.expected_tag_commit }}"
    )
    assert 'workflow_id: "test.yml"' in script
    assert "head_sha: expectedSha" in script
    assert 'event: "push"' in script
    assert '["main", "master"]' in script
    assert 'latest.status !== "completed"' in script
    assert 'latest.conclusion !== "success"' in script
    assert "while (" not in script
    assert "setTimeout" not in script
    assert "verify-main-test-workflow" in jobs["publish-to-pypi"]["needs"]
    assert "publish-to-pypi" in jobs["github-release"]["needs"]
