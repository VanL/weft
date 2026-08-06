"""Tests for the Docker-backed provider_cli agent runner."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from weft_docker.agent_runner import (
    DockerProviderCLIRunner,
    _resolve_work_item_mounts,
)

from weft.core.agents.provider_cli.registry import (
    ProviderCLIInvocation,
    ProviderCLIResult,
)

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize(
    ("runner_options", "message"),
    [
        (
            {"mounts": 1},
            "spec.runner.options.mounts must be a list of mount objects",
        ),
        (
            {"mounts": [1]},
            "spec.runner.options.mounts[0] must be an object",
        ),
        (
            {"mounts": [{"source": 1, "target": "/workspace"}]},
            "spec.runner.options.mounts[0].source must be a string",
        ),
        (
            {"mounts": [{"source": ".", "target": 1}]},
            "spec.runner.options.mounts[0].target must be a string",
        ),
        (
            {"work_item_mounts": 1},
            "spec.runner.options.work_item_mounts must be a list of mount objects",
        ),
        (
            {"work_item_mounts": [1]},
            "spec.runner.options.work_item_mounts[0] must be an object",
        ),
        (
            {
                "work_item_mounts": [
                    {
                        "source_path_ref": "metadata.path",
                        "target": "/workspace",
                        "read_only": 1,
                    }
                ]
            },
            "spec.runner.options.work_item_mounts[0].read_only must be a boolean",
        ),
        (
            {
                "work_item_mounts": [
                    {
                        "source_path_ref": "metadata.path",
                        "target": "/workspace",
                        "required": 1,
                    }
                ]
            },
            "spec.runner.options.work_item_mounts[0].required must be a boolean",
        ),
        (
            {"network": 1},
            "spec.runner.options.network must be a string",
        ),
    ],
)
def test_agent_runner_rejects_wrong_option_shapes_as_value_error(
    runner_options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        DockerProviderCLIRunner(
            tid="123",
            agent={
                "runtime": "provider_cli",
                "authority_class": "general",
                "conversation_scope": "per_message",
                "runtime_config": {"provider": "codex"},
            },
            env={},
            working_dir=None,
            timeout=5.0,
            limits=None,
            monitor_interval=0.05,
            runner_options=runner_options,
        )
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


def test_agent_runner_mounts_default_to_read_only(tmp_path: Path) -> None:
    runner = DockerProviderCLIRunner(
        tid="123",
        agent={
            "runtime": "provider_cli",
            "authority_class": "general",
            "conversation_scope": "per_message",
            "runtime_config": {"provider": "codex"},
        },
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_interval=0.05,
        runner_options={
            "mounts": [
                {
                    "source": str(tmp_path),
                    "target": "/workspace",
                }
            ]
        },
    )

    assert runner._mounts == [  # pyright: ignore[reportPrivateUsage]
        {
            "source": str(tmp_path.resolve()),
            "target": "/workspace",
            "read_only": True,
        }
    ]


def test_resolve_work_item_mounts_reads_document_path_from_metadata(
    tmp_path: Path,
) -> None:
    document_path = tmp_path / "overview.md"
    document_path.write_text("# Weft\n", encoding="utf-8")

    mounts = _resolve_work_item_mounts(
        (
            {
                "source_path_ref": "metadata.document_path",
                "target": "/tmp/runtime-document.md",
                "read_only": True,
                "required": True,
                "kind": "file",
            },
        ),
        {
            "template": "explain",
            "metadata": {"document_path": str(document_path)},
        },
        name="spec.runner.options.work_item_mounts",
    )

    assert mounts == [
        {
            "source": str(document_path.resolve()),
            "target": "/tmp/runtime-document.md",
            "read_only": True,
        }
    ]


def test_resolve_work_item_mounts_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        _resolve_work_item_mounts(
            (
                {
                    "source_path_ref": "metadata.document_path",
                    "target": "/tmp/runtime-document.md",
                    "read_only": True,
                    "required": True,
                    "kind": "file",
                },
            ),
            {
                "template": "explain",
                "metadata": {"document_path": "docs/specifications/00-Overview.md"},
            },
            name="spec.runner.options.work_item_mounts",
        )


def test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-201] exception
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeContainer:
        def __init__(self) -> None:
            self.name = "weft-agent-test"
            self.id = "container-id"
            self.attrs = {
                "State": {
                    "Status": "exited",
                    "ExitCode": 0,
                    "OOMKilled": False,
                }
            }

        def start(self) -> None:
            return None

        def reload(self) -> None:
            return None

        def logs(self, *, stdout: bool = False, stderr: bool = False) -> bytes:
            del stdout, stderr
            return b""

        def remove(self, force: bool = False) -> None:
            del force

    class FakeContainers:
        def create(
            self, image: str, command: list[str], **kwargs: object
        ) -> FakeContainer:
            created["image"] = image
            created["command"] = command
            created["kwargs"] = kwargs
            return FakeContainer()

    class FakeClient:
        def __init__(self) -> None:
            self.containers = FakeContainers()

    @contextmanager
    def fake_docker_client() -> Iterator[FakeClient]:
        yield FakeClient()

    class FakeMount:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeUlimit:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(
        "weft_docker.agent_runner.normalize_agent_work_item",
        lambda agent, work_item: work_item,
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.resolve_provider_cli",
        lambda agent: SimpleNamespace(name="codex"),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.resolve_provider_container_runtime",
        lambda provider_name, task_env, working_dir, explicit_mounts: SimpleNamespace(
            env={}, mounts=()
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.ensure_agent_image",
        lambda provider_name: SimpleNamespace(
            image="weft-agent-codex:cached123",
            cache_key="cached-key",
            recipe=SimpleNamespace(default_executable="codex"),
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.prepare_provider_container_runtime",
        lambda provider_name, runtime_requirements, temp_root: SimpleNamespace(
            mounts=(), env={}
        ),
    )

    class FakeProvider:
        def parse_result(
            self,
            *,
            completed: subprocess.CompletedProcess[str],
            invocation: ProviderCLIInvocation,
        ) -> ProviderCLIResult:
            del completed, invocation
            return ProviderCLIResult(output_text="parsed")

    monkeypatch.setattr(
        "weft_docker.agent_runner.prepare_provider_cli_execution",
        lambda **kwargs: SimpleNamespace(
            invocation=SimpleNamespace(
                stdin_text=None,
                cwd="/tmp",
                env={},
                command=("codex", "exec", "prompt"),
            ),
            provider=FakeProvider(),
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.build_provider_cli_execution_result",
        lambda **kwargs: "provider-output",
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.load_docker_sdk",
        lambda: SimpleNamespace(
            types=SimpleNamespace(Mount=FakeMount, Ulimit=FakeUlimit)
        ),
    )
    monkeypatch.setattr("weft_docker.agent_runner.docker_client", fake_docker_client)

    runner = DockerProviderCLIRunner(
        tid="1234567890",
        agent={
            "runtime": "provider_cli",
            "authority_class": "general",
            "conversation_scope": "per_message",
            "runtime_config": {"provider": "codex"},
        },
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_interval=0.01,
        runner_options={"container_workdir": "/tmp", "mount_workdir": False},
    )

    outcome = runner.run({"task": "Explain this document"})

    assert outcome.status == "ok"
    assert outcome.value == "provider-output"
    assert created["image"] == "weft-agent-codex:cached123"
    kwargs = cast(dict[str, object], created["kwargs"])
    labels = cast(dict[str, str], kwargs["labels"])
    assert labels["weft.agent.image.cache_key"] == "cached-key"


def test_agent_runner_reports_cancel_requested_as_cancelled(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-201] exception
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[bool] = []

    class FakeContainer:
        def __init__(self) -> None:
            self.name = "weft-agent-cancel"
            self.id = "container-id"
            state: dict[str, object] = {
                "Status": "running",
                "ExitCode": None,
                "OOMKilled": False,
            }
            self.attrs: dict[str, object] = {"State": state}

        def start(self) -> None:
            return None

        def reload(self) -> None:
            return None

        def kill(self) -> None:
            killed.append(True)
            state = cast(dict[str, object], self.attrs["State"])
            state["Status"] = "exited"
            state["ExitCode"] = 137

        def logs(self, *, stdout: bool = False, stderr: bool = False) -> bytes:
            del stdout, stderr
            return b""

        def remove(self, force: bool = False) -> None:
            del force

    class FakeContainers:
        def create(
            self, image: str, command: list[str], **kwargs: object
        ) -> FakeContainer:
            del image, command, kwargs
            return FakeContainer()

    class FakeClient:
        def __init__(self) -> None:
            self.containers = FakeContainers()

    @contextmanager
    def fake_docker_client() -> Iterator[FakeClient]:
        yield FakeClient()

    class FakeMount:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeUlimit:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(
        "weft_docker.agent_runner.resolve_provider_container_runtime",
        lambda provider_name, task_env, working_dir, explicit_mounts: SimpleNamespace(
            env={}, mounts=()
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.ensure_agent_image",
        lambda provider_name: SimpleNamespace(
            image="weft-agent-codex:cached123",
            cache_key="cached-key",
            recipe=SimpleNamespace(default_executable="codex"),
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.prepare_provider_container_runtime",
        lambda provider_name, runtime_requirements, temp_root: SimpleNamespace(
            mounts=(), env={}
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.prepare_provider_cli_execution",
        lambda **kwargs: SimpleNamespace(
            invocation=SimpleNamespace(
                stdin_text=None,
                cwd="/tmp",
                env={},
                command=("codex", "exec", "prompt"),
            ),
            provider=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        "weft_docker.agent_runner.load_docker_sdk",
        lambda: SimpleNamespace(
            types=SimpleNamespace(Mount=FakeMount, Ulimit=FakeUlimit)
        ),
    )
    monkeypatch.setattr("weft_docker.agent_runner.docker_client", fake_docker_client)

    runner = DockerProviderCLIRunner(
        tid="1234567890",
        agent={
            "runtime": "provider_cli",
            "authority_class": "general",
            "conversation_scope": "per_message",
            "runtime_config": {"provider": "codex"},
        },
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_interval=0.01,
        runner_options={"container_workdir": "/tmp", "mount_workdir": False},
    )

    outcome = runner.run_with_hooks(
        {"task": "slow"},
        cancel_requested=lambda: True,
    )

    assert killed == [True]
    assert outcome.status == "cancelled"
    assert outcome.error == "Target execution cancelled"
