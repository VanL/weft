"""Platform-guard tests for Docker-dependent builtins."""

from __future__ import annotations

import pytest

from weft.builtins import builtin_task_catalog
from weft.builtins.agent_images import prepare_agent_images_task
from weft.commands import specs as spec_cmd
from weft.core.spec_parameterization import materialize_taskspec_template
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _load_dockerized_agent_taskspec() -> TaskSpec:
    resolved = spec_cmd.resolve_named_spec(
        "dockerized-agent",
        spec_type=spec_cmd.SPEC_TYPE_TASK,
    )
    taskspec = TaskSpec.model_validate(
        resolved.payload,
        context={"template": True, "auto_expand": False},
    )
    taskspec.set_bundle_root(resolved.bundle_root)
    return taskspec


def test_builtin_catalog_reports_supported_platforms_for_docker_builtins() -> None:
    catalog = {item.name: item for item in builtin_task_catalog()}

    assert catalog["prepare-agent-images"].supported_platforms == ("linux", "darwin")
    assert catalog["dockerized-agent"].supported_platforms == ("linux", "darwin")


def test_prepare_agent_images_task_rejects_windows_before_runner_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("weft.builtins.sys.platform", "win32")
    monkeypatch.setattr(
        "weft.builtins.agent_images.require_runner_plugin",
        lambda name: (_ for _ in ()).throw(
            AssertionError("runner plugin lookup should not run on Windows")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"Builtin 'prepare-agent-images' is currently supported only on "
            r"Linux and macOS; Windows is not supported\."
        ),
    ):
        prepare_agent_images_task()


def test_dockerized_agent_materialization_rejects_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("weft.builtins.sys.platform", "win32")
    taskspec = _load_dockerized_agent_taskspec()

    with pytest.raises(
        ValueError,
        match=(
            r"Builtin 'dockerized-agent' is currently supported only on "
            r"Linux and macOS; Windows is not supported\."
        ),
    ):
        materialize_taskspec_template(
            taskspec,
            arguments={"provider": "codex"},
            context_root="/tmp",
        )
