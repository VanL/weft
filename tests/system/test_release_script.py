"""Tests for the local release helper script."""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import re
import subprocess
import sys
import tomllib
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


# Workflow structure is found by content, never by job id or step `env` key.
# The rejection-condition check deliberately fixes one JavaScript expression;
# these source checks do not prove execution or general JavaScript behavior.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
# [TS-4] names both files.
_RELEASE_WORKFLOW = _WORKFLOWS / "release.yml"
_TEST_WORKFLOW = _WORKFLOWS / "test.yml"
_INPUT_REFERENCE = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z_][\w-]*)\s*\}\}$")


def _load_workflow(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), path
    return loaded


def _workflow_trigger(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML parses the bare key `on` as the boolean True.
    trigger = workflow.get("on", workflow.get(True))
    return trigger if isinstance(trigger, dict) else {}


def _release_gate_workflows() -> dict[Path, dict[str, Any]]:
    """Return the tag-triggered workflows, keyed by resolved path."""

    gates: dict[Path, dict[str, Any]] = {}
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = _load_workflow(path)
        push = _workflow_trigger(workflow).get("push")
        if isinstance(push, dict) and push.get("tags"):
            gates[path.resolve()] = workflow
    return gates


def _publishing_jobs(gate: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a gate's jobs that call a reusable workflow in this repository."""

    return [
        job
        for job in gate.get("jobs", {}).values()
        if str(job.get("uses", "")).startswith("./")
    ]


def _jobs_with_step(workflow: dict[str, Any], uses_prefix: str) -> list[str]:
    """Return the ids of jobs holding a step whose `uses` has the prefix."""

    return [
        job_id
        for job_id, job in workflow["jobs"].items()
        if any(
            str(step.get("uses", "")).startswith(uses_prefix)
            for step in job.get("steps", [])
        )
    ]


def _needs_closure(jobs: dict[str, Any], job_id: str) -> set[str]:
    """Return every job the given job transitively needs."""

    closure: set[str] = set()
    pending = [job_id]
    while pending:
        needs = jobs[pending.pop()].get("needs", [])
        for needed in [needs] if isinstance(needs, str) else needs:
            if needed not in closure:
                closure.add(needed)
                pending.append(needed)
    return closure


def _input_reference(value: object) -> str | None:
    """Return `<name>` when the value is exactly `${{ inputs.<name> }}`."""

    if not isinstance(value, str):
        return None
    match = _INPUT_REFERENCE.match(value.strip())
    return None if match is None else match.group(1)


def _checkout_ref_inputs(workflow: dict[str, Any]) -> set[str]:
    """Return the inputs the workflow's checkout steps use as `ref`."""

    names: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                name = _input_reference((step.get("with") or {}).get("ref"))
                if name is not None:
                    names.add(name)
    return names


def _carries_built_commit(
    name: str | None,
    gate_inputs: list[dict[str, Any]],
    checkout_refs: set[str],
) -> bool:
    """Whether every gate passes for `name` the commit the build checks out."""

    if name is None:
        return False
    return any(
        all(
            name in inputs and ref in inputs and inputs[name] == inputs[ref]
            for inputs in gate_inputs
        )
        for ref in checkout_refs
    )


def _release_switch_input(reusable: dict[str, Any]) -> str:
    """Return the input the release-asset job's `if:` consumes."""

    names = {
        name
        for job_id in _jobs_with_step(reusable, "softprops/action-gh-release@")
        for name in re.findall(
            r"\binputs\.([A-Za-z_][\w-]*)", str(reusable["jobs"][job_id].get("if", ""))
        )
    }
    (name,) = names
    return str(name)


def _tag_pattern_prefix(pattern: str) -> str:
    """Return the literal prefix of a supported `<literal>*` tag filter."""

    assert re.fullmatch(r"[^*?+\[\]!]*\*", pattern), (
        f"unsupported tag filter {pattern!r}"
    )
    return pattern.removesuffix("*")


def _prefix_matches(prefix: str, tag: str) -> bool:
    """Whether `<prefix>*` matches the tag (`*` matches anything but `/`)."""

    return tag.startswith(prefix) and "/" not in tag.removeprefix(prefix)


def _extension_targets(release: ModuleType) -> list[Any]:
    """Return every release target other than the root target."""

    return [
        target
        for target in release.RELEASE_TARGETS.values()
        if target.tag_namespace is not None
    ]


def _suite_path(target: Any) -> str:
    """Return a first-party package's test-suite path relative to the root."""

    return str((target.package_dir / "tests").relative_to(_REPO_ROOT).as_posix())


def _assert_gate_publishes_target(
    gate: dict[str, Any], target: Any, targets: list[Any]
) -> set[str]:
    """Check a gate's publishing jobs against its target; return what they call.

    Identifier-free relations only: the target's package name is passed and no
    other target's is, exactly one input names the target's package directory,
    and the input the reusable workflow consumes as its release-asset switch
    (or that input's default) equals the target's release-enabled flag.
    """

    publishing = _publishing_jobs(gate)
    assert publishing, target.key
    called: set[str] = set()
    for job in publishing:
        # No condition, so every matching tag reaches the publication.
        assert "if" not in job, target.key
        called.add(job["uses"])
        reusable = _load_workflow(_REPO_ROOT / job["uses"])
        reusable_trigger = _workflow_trigger(reusable)
        assert "workflow_call" in reusable_trigger, job["uses"]
        inputs = job.get("with", {})
        values = list(inputs.values())
        assert target.package_name in values, target.key
        for other in targets:
            if other is not target:
                assert other.package_name not in values, (target.key, other.key)
        package_dir_values = [
            value
            for value in values
            if isinstance(value, str)
            and (_REPO_ROOT / value).resolve() == target.package_dir.resolve()
        ]
        assert len(package_dir_values) == 1, target.key
        switch = _release_switch_input(reusable)
        declared = reusable_trigger["workflow_call"]["inputs"]
        assert switch in declared
        enabled = inputs.get(switch, declared[switch].get("default"))
        assert enabled == target.github_release_enabled, target.key
    return called


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match is not None, requirement
    return match.group()


def _normalized_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    target = dataclasses.replace(
        release.DOCKER_RELEASE_TARGET,
        package_name="example-package",
        tag_namespace="example_namespace",
    )
    monkeypatch.setattr(release, "github_release_exists", lambda tag_name: False)
    monkeypatch.setattr(
        release,
        "pypi_version_exists",
        lambda package_name, version: (
            package_name == "example-package" and version == "0.1.0"
        ),
    )
    monkeypatch.setattr(release, "local_tag_commit", lambda tag_name: "a" * 40)
    monkeypatch.setattr(release, "remote_tag_commit", lambda tag_name: None)

    state = release.inspect_release_state("0.1.0", target=target)

    assert state.tag_name == "example_namespace/v0.1.0"
    assert state.pypi_release_exists is True
    assert state.target is target


def test_django_release_target_uses_namespaced_tag_and_package_dir() -> None:
    """The Django integration should publish like the other first-party packages."""

    release = _load_release_module()
    django = release.DJANGO_RELEASE_TARGET

    assert django.tag_namespace is not None
    assert django.pyproject_path.parent == django.package_dir
    project = tomllib.loads(django.pyproject_path.read_text(encoding="utf-8"))
    assert project["project"]["name"] == django.package_name
    owned = dataclasses.replace(django, tag_namespace="example_namespace")
    assert owned.tag_name("0.1.0") == "example_namespace/v0.1.0"


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
    for target in _extension_targets(release):
        assert target.tag_name("") not in output, target.key
    assert output.index("$ git push origin main") < output.index(
        "would wait for the exact main/master push run"
    )
    assert output.index("would wait for the exact main/master push run") < output.index(
        "$ git tag v0.1.0"
    )


def test_build_precheck_commands_cover_release_gate_and_quality_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper precheck should always cover the core release-gate suites."""

    release = _load_release_module()
    monkeypatch.setattr(release, "_pytest_worker_count", lambda: "17")
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
    assert any("bin/pytest-live-providers" in command for command in commands)

    extensions = _extension_targets(release)
    assert extensions
    pyproject = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    optional_dependencies = pyproject["project"]["optional-dependencies"]
    # Every first-party package must be installed by the extras each gate
    # passes; extra extras and reordering are harmless.
    for command in (
        sqlite_command,
        postgres_command,
        ruff_check_command,
        ruff_format_command,
        mypy_command,
    ):
        extras = {
            command[index + 1] for index, arg in enumerate(command) if arg == "--extra"
        }
        installed = {
            _normalized_package_name(_requirement_name(requirement))
            for extra in extras
            for requirement in optional_dependencies[extra]
        }
        for target in extensions:
            assert _normalized_package_name(target.package_name) in installed, (
                command,
                target.key,
            )

    # All markers, so the slow tests run ([TS-4]).
    assert sqlite_command[sqlite_command.index("-m") + 1] == ""
    assert "--all" in postgres_command
    assert "--check" in ruff_format_command
    for command in (ruff_check_command, ruff_format_command):
        assert "weft" in command
        assert "tests" in command
        for target in extensions:
            assert target.package_dir.relative_to(_REPO_ROOT).as_posix() in command
    # [TS-3]: mypy covers tests/ and bin/ as well as every first-party package.
    assert "weft" in mypy_command
    assert "tests" in mypy_command
    assert "bin" in mypy_command
    for target in extensions:
        relative = target.package_dir.relative_to(_REPO_ROOT)
        assert (relative / relative.name).as_posix() in mypy_command

    addopts = release.build_precheck_env_overrides()["PYTEST_ADDOPTS"].split()
    assert "-x" in addopts or "--exitfirst" in addopts or "--maxfail=1" in addopts
    # Live provider suites run only through their own command.
    assert release.PRECHECK_ENV_OVERRIDES["WEFT_RUN_LIVE_PROVIDER_CLI_TESTS"] == "0"
    assert release.PRECHECK_ENV_OVERRIDES["WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS"] == "0"


def test_build_precheck_env_overrides_sets_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release prechecks should use the repo helper's xdist worker count."""

    release = _load_release_module()
    monkeypatch.setattr(release, "_pytest_worker_count", lambda: "17")

    result = release.build_precheck_env_overrides()

    assert result["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "17"
    assert release.PRECHECK_ENV_OVERRIDES.items() <= result.items()


def test_build_postupdate_steps_build_all_publishable_packages() -> None:
    """Post-update verification should build every publishable first-party package."""

    release = _load_release_module()

    steps = release.build_postupdate_steps()

    built_dirs = {step.cwd.resolve() for step in steps if "build" in step.command}
    # [TS-4]: packages are added to the release gates and the release helper
    # atomically, so the gates' package directories are the independent oracle.
    gate_package_dirs: set[Path] = set()
    for gate_path, gate in _release_gate_workflows().items():
        for job in _publishing_jobs(gate):
            package_dirs = {
                (_REPO_ROOT / value).resolve()
                for value in job.get("with", {}).values()
                if isinstance(value, str)
                and (_REPO_ROOT / value / "pyproject.toml").is_file()
            }
            assert len(package_dirs) == 1, gate_path
            gate_package_dirs |= package_dirs
    assert gate_package_dirs
    assert built_dirs == gate_package_dirs


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
    for target in _extension_targets(release):
        assert _suite_path(target) in targets, target.key


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
    host_dependent = (
        release.DOCKER_RELEASE_TARGET,
        release.MACOS_SANDBOX_RELEASE_TARGET,
    )
    for target in _extension_targets(release):
        if target in host_dependent:
            assert _suite_path(target) not in targets, target.key
        else:
            assert _suite_path(target) in targets, target.key


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
        {"PYTEST_ADDOPTS": "-k example", "EXAMPLE_FLAG": "1"},
        base_env={
            "PATH": "/tmp/bin",
            "PYTEST_ADDOPTS": "--lf",
        },
    )

    assert merged is not None
    assert merged["PATH"] == "/tmp/bin"
    assert merged["UV_PROJECT_ENVIRONMENT"] == str(release.PROJECT_VENV_PATH)
    assert merged["PYTEST_ADDOPTS"] == "--lf -k example"
    assert merged["EXAMPLE_FLAG"] == "1"


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
        env_overrides={"PYTEST_ADDOPTS": "-k example", "EXAMPLE_FLAG": "1"},
    )

    captured = capsys.readouterr()
    assert "PYTEST_ADDOPTS='-k example'" in captured.out
    assert "EXAMPLE_FLAG=1" in captured.out
    assert "pytest -q" in captured.out
    assert "(cwd=extensions/weft_docker)" in captured.out


def test_release_gate_routes_each_tag_to_exactly_one_package() -> None:
    """Each immutable package tag must invoke only its matching publication."""

    release = _load_release_module()
    targets = list(release.RELEASE_TARGETS.values())
    gates = _release_gate_workflows()

    def gate_path_of(target: Any) -> Path:
        return (_REPO_ROOT / str(target.release_gate_workflow)).resolve()

    # Every tag-triggered workflow is some target's gate, and every target has one.
    assert set(gates) == {gate_path_of(target) for target in targets}

    # Supported tag language: `<literal>*` only, so each pattern matches exactly
    # the tags that begin with its literal prefix and have no `/` after it.
    prefixes = {
        gate_path: [
            _tag_pattern_prefix(pattern)
            for pattern in _workflow_trigger(gate)["push"]["tags"]
        ]
        for gate_path, gate in gates.items()
    }
    for target in targets:
        namespace = target.tag_name("")
        covering = {
            gate_path
            for gate_path, gate_prefixes in prefixes.items()
            if any(_prefix_matches(prefix, namespace) for prefix in gate_prefixes)
        }
        assert covering == {gate_path_of(target)}, target.key
    for gate_path, gate_prefixes in prefixes.items():
        covered = [
            target
            for target in targets
            if any(
                _prefix_matches(prefix, target.tag_name("")) for prefix in gate_prefixes
            )
        ]
        assert len(covered) == 1, gate_path
    # Exclusivity: no tag of any version can match two gates.
    gate_prefix_pairs = [
        (gate_path, prefix)
        for gate_path, gate_prefixes in prefixes.items()
        for prefix in gate_prefixes
    ]
    for gate_path, prefix in gate_prefix_pairs:
        for other_path, other_prefix in gate_prefix_pairs:
            assert other_path == gate_path or not other_prefix.startswith(prefix), (
                gate_path,
                other_path,
            )
    # Readable sanity case, not the proof.
    for target in targets:
        tag = target.tag_name("1.2.3")
        matching = {
            gate_path
            for gate_path, gate_prefixes in prefixes.items()
            if any(_prefix_matches(prefix, tag) for prefix in gate_prefixes)
        }
        assert matching == {gate_path_of(target)}, tag

    # Each gate calls the sole package build/publish workflow for its target.
    called_workflows: set[str] = set()
    for target in targets:
        called_workflows |= _assert_gate_publishes_target(
            gates[gate_path_of(target)], target, targets
        )
    assert len(called_workflows) == 1


def test_publish_workflow_checks_test_push_and_publication_dependencies() -> None:
    """Check release wiring and narrow source guards, not JavaScript execution.

    Spec: docs/specifications/08-Testing_Strategy.md [TS-4].
    """

    workflow = _load_workflow(_RELEASE_WORKFLOW)
    jobs = workflow["jobs"]
    run_checks = [
        (job_id, step)
        for job_id, job in jobs.items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/github-script@")
        and "listWorkflowRuns(" in str((step.get("with") or {}).get("script", ""))
    ]
    assert run_checks

    gate_inputs = [
        job.get("with", {})
        for gate in _release_gate_workflows().values()
        for job in _publishing_jobs(gate)
    ]
    assert gate_inputs
    checkout_refs = _checkout_ref_inputs(workflow)
    assert checkout_refs
    test_workflow = _load_workflow(_TEST_WORKFLOW)
    # [TS-4]: the `Test` workflow is the sole automated test owner.
    assert test_workflow["name"] == "Test"

    for job_id, step in run_checks:
        script = step["with"]["script"]
        commit_keys = [
            key
            for key, value in (step.get("env") or {}).items()
            if _carries_built_commit(
                _input_reference(value), gate_inputs, checkout_refs
            )
        ]
        assert commit_keys, job_id
        filtered_keys = []
        for key in commit_keys:
            binding = re.search(
                rf"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*process\.env\.{re.escape(key)}\b",
                script,
            )
            if binding is not None and re.search(
                rf"\bhead_sha\s*:\s*{re.escape(binding.group(1))}\b", script
            ):
                filtered_keys.append(key)
        assert filtered_keys, job_id

        workflow_id = re.search(r"workflow_id:\s*[\"']([^\"']+)[\"']", script)
        assert workflow_id is not None
        assert workflow_id.group(1) == _TEST_WORKFLOW.name
        assert re.search(r"\bevent:\s*[\"']push[\"']", script)
        assert re.search(r"[\"']main[\"']", script)
        assert re.search(r"[\"']master[\"']", script)
        # Keep the whole condition together so replacing || with && fails.
        # This deliberately fixes the expression's spelling, not its reachability.
        assert (
            'if (latest.status !== "completed" || latest.conclusion !== "success")'
            in script
        )
        # A source guard against timer calls, not proof that no helper polls.
        assert re.search(r"\bset(?:Timeout|Interval)\s*\(", script) is None

    pypi_jobs = [
        job_id
        for job_id, job in jobs.items()
        if isinstance(job.get("permissions"), dict)
        and job["permissions"].get("id-token") == "write"
    ]
    (pypi_job,) = pypi_jobs
    for job_id, _step in run_checks:
        assert job_id in _needs_closure(jobs, pypi_job)
    asset_jobs = _jobs_with_step(workflow, "softprops/action-gh-release@")
    assert asset_jobs
    for asset_job in asset_jobs:
        assert pypi_job in _needs_closure(jobs, asset_job)
