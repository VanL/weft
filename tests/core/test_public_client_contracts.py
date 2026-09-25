"""Public construction and context contracts.

Spec: docs/specifications/14-Python_API_Surfaces.md [PY-1].
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers.test_backend import prepare_project_root
from weft.client import (
    LimitsSection,
    RunInputStdinSection,
    SpecSection,
    TaskSpec,
    WeftClient,
    WeftContext,
    build_context,
)

pytestmark = [pytest.mark.shared]


def test_public_models_construct_validate_and_freeze_execution_fields() -> None:
    spec = TaskSpec(
        name="public-task",
        tid=str(time.time_ns()),
        spec=SpecSection(
            type="command",
            process_target="echo",
            args=["hello"],
            limits=LimitsSection(memory_mb=128),
        ),
    )
    assert spec.tid is not None
    assert spec.io.outputs["outbox"] == f"T{spec.tid}.outbox"
    restored = TaskSpec.model_validate_json(spec.model_dump_json())
    assert restored.spec.args == ["hello"]
    assert restored.spec.limits.memory_mb == 128
    with pytest.raises(AttributeError, match="immutable|frozen"):
        spec.spec.process_target = "other"
    with pytest.raises(AttributeError, match="immutable|frozen"):
        spec.io.outputs = {"outbox": "other"}
    with pytest.raises(ValidationError):
        LimitsSection(memory_mb=-1)
    with pytest.raises(ValidationError):
        RunInputStdinSection.model_validate({"type": "binary"})
    with pytest.raises(ValidationError) as invalid_type:
        TaskSpec.model_validate(
            {"name": "invalid", "spec": {"type": "unknown"}},
            context={"template": True, "auto_expand": False},
        )
    assert any(
        error["loc"] == ("spec", "type") and error["type"] == "literal_error"
        for error in invalid_type.value.errors()
    )


def test_public_template_preparation_preserves_context_without_enqueue(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context: WeftContext = build_context(root)
    client = WeftClient(context)
    assert client.context is context
    template = TaskSpec.model_validate(
        {
            "name": "prepared",
            "spec": SpecSection(type="command", process_target="echo"),
        },
        context={"template": True, "auto_expand": False},
    )
    assert template.tid is None
    spawn = context.queue("weft.spawn.requests")
    try:
        spawn.write("positive-control")
        assert spawn.read() == "positive-control"
        prepared = client.prepare(template)
        assert prepared.name == "prepared"
        assert template.tid is None
        assert spawn.read() is None
        assert client.prepare(template.model_dump()).name == "prepared"
        assert spawn.read() is None
    finally:
        spawn.close()


def test_public_context_creation_flags_skip_metadata_and_broker_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "uncreated"
    context = build_context(root, create_dirs=False, create_database=False)
    assert context.root == root
    assert root.is_dir()
    assert not context.logs_dir.exists()
    assert not context.outputs_dir.exists()
    if context.database_path is not None:
        assert not context.database_path.exists()


def test_public_construction_type_checks_and_rejects_bad_client_inputs(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "public_client_probe.py"
    probe.write_text(
        "from weft.client import IOSection, LimitsSection, SpecSection, TaskSpec, WeftClient, WeftContext, build_context\n"
        "import time\n"
        "context: WeftContext = build_context(create_dirs=False, create_database=False)\n"
        "client = WeftClient(context)\n"
        "with client as entered:\n"
        "    same_client: WeftClient = entered\n"
        "client.close()\n"
        "with client:\n"
        "    pass\n"
        "spec = TaskSpec(name='typed', tid=str(time.time_ns()), spec=SpecSection(type='command', process_target='echo', limits=LimitsSection(memory_mb=128)))\n"
        "client.prepare(spec)\n"
        "client.prepare({'name': 'mapping', 'spec': {'type': 'command', 'process_target': 'echo'}})\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(root / "pyproject.toml"),
        str(probe),
    ]
    accepted = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=60, check=False
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    with probe.open("a", encoding="utf-8") as stream:
        stream.write("client.prepare(42)\nclient.submit(['invalid'])\n")
    rejected = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=60, check=False
    )
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert rejected.stdout.count("[arg-type]") == 2, rejected.stdout
