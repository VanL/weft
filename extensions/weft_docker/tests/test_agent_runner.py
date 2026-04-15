"""Tests for the Docker-backed provider_cli agent runner."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from weft_docker.agent_runner import (
    DockerProviderCLIRunner,
    _resolve_work_item_mounts,
)

pytestmark = [pytest.mark.shared]


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


def test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image(
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
            return None

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
    def fake_docker_client():
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
        def parse_result(self, *, completed, invocation):
            del completed, invocation
            return "parsed"

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
    kwargs = created["kwargs"]
    assert kwargs["labels"]["weft.agent.image.cache_key"] == "cached-key"
