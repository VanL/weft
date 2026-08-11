"""Tests for the standalone manager-process entry point."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from weft import manager_process
from weft.core.taskspec import (
    TaskSpec,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)

pytestmark = [pytest.mark.shared]


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _encoded_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _args() -> list[str]:
    broker_target = json.dumps(
        {
            "backend_name": "sqlite",
            "target": "/tmp/weft-manager-process-test.db",
            "backend_options": {},
        }
    )
    return [
        "manager.path",
        _encoded(broker_target),
        _encoded("{}"),
        _encoded("{}"),
        "0.1",
    ]


@pytest.mark.parametrize("failure", [OSError("unreadable cwd")])
def test_main_renders_supported_manager_argument_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise failure

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    assert manager_process.main(_args()) == 2
    assert capsys.readouterr().err == f"Invalid manager arguments: {failure}\n"


@pytest.mark.parametrize(
    ("argument_index", "malformed_value"),
    [
        (1, "a"),
        (1, _encoded_bytes(b"\xff")),
        (1, _encoded("{")),
        (2, "a"),
        (2, _encoded_bytes(b"\xff")),
        (3, "a"),
        (3, _encoded_bytes(b"\xff")),
        (3, _encoded("{")),
        (4, "not-a-float"),
    ],
)
def test_main_renders_each_malformed_manager_argument_family(
    capsys: pytest.CaptureFixture[str],
    argument_index: int,
    malformed_value: str,
) -> None:
    args = _args()
    args[argument_index] = malformed_value

    assert manager_process.main(args) == 2
    assert capsys.readouterr().err.startswith("Invalid manager arguments: ")


def test_main_propagates_unexpected_manager_argument_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise RuntimeError("unexpected deserialize defect")

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    with pytest.raises(RuntimeError, match="unexpected deserialize defect"):
        manager_process.main(_args())


def test_main_propagates_manager_argument_type_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise TypeError("unexpected deserialize type defect")

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    with pytest.raises(TypeError, match="unexpected deserialize type defect"):
        manager_process.main(_args())


def test_main_renders_invalid_taskspec(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    assert manager_process.main(_args()) == 2
    assert capsys.readouterr().err.startswith("Invalid manager TaskSpec: ")


def test_main_propagates_unexpected_taskspec_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    def fail_validate(_payload: object) -> object:
        raise RuntimeError("unexpected validation defect")

    monkeypatch.setattr(
        manager_process,
        "decode_taskspec_transport_payload",
        fail_validate,
    )

    with pytest.raises(RuntimeError, match="unexpected validation defect"):
        manager_process.main(_args())


def test_main_decodes_canonical_taskspec_transport_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "manager-bundle"
    bundle_root.mkdir()
    taskspec = validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "manager",
            "spec": {
                "type": "function",
                "function_target": "manager_bundle:run",
            },
        },
        bundle_root=bundle_root,
    )
    args = _args()
    args[2] = _encoded(json.dumps(encode_taskspec_transport_payload(taskspec)))
    captured: list[TaskSpec] = []
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    def capture_run(
        _task_cls_path: str,
        _broker_target: object,
        spec: TaskSpec,
        _config: object,
        _poll_interval: float,
        *,
        hard_exit_on_return: bool,
    ) -> None:
        assert hard_exit_on_return is True
        captured.append(spec)

    monkeypatch.setattr(manager_process, "run_manager_process", capture_run)

    assert manager_process.main(args) == 0
    decoded = captured[0]
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert "_weft_bundle_root" not in decoded.model_dump(mode="json")


def test_run_manager_process_preserves_bundle_provenance_at_task_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "foreground-manager-bundle"
    bundle_root.mkdir()
    taskspec = validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "manager",
            "spec": {
                "type": "function",
                "function_target": "manager_bundle:run",
            },
        },
        bundle_root=bundle_root,
    )
    captured_json: list[str] = []

    def capture_entry(
        _task_cls_path: str,
        _broker_target: object,
        spec_json: str,
        _config: object,
        _poll_interval: float,
        _hard_exit_on_return: bool,
    ) -> None:
        captured_json.append(spec_json)

    monkeypatch.setattr(manager_process, "_task_process_entry", capture_entry)

    manager_process.run_manager_process(
        "weft.core.manager.Manager",
        "unused.db",
        taskspec,
        {},
        0.1,
        hard_exit_on_return=True,
    )

    payload = json.loads(captured_json[0])
    assert payload["_weft_bundle_root"] == str(bundle_root.resolve())
    decoded = manager_process.decode_taskspec_transport_payload(payload)
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert "_weft_bundle_root" not in taskspec.model_dump(mode="json")
