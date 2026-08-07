"""Tests for the standalone manager-process entry point."""

from __future__ import annotations

import base64
import json

import pytest

from weft import manager_process

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

    def fail_validate(_payload: str) -> object:
        raise RuntimeError("unexpected validation defect")

    monkeypatch.setattr(manager_process.TaskSpec, "model_validate_json", fail_validate)

    with pytest.raises(RuntimeError, match="unexpected validation defect"):
        manager_process.main(_args())
