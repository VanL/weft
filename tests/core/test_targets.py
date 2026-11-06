"""Tests for shared target execution helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from weft.core.targets import (
    decode_work_message,
    execute_command_target,
    execute_function_target,
    prepare_call_arguments,
    serialize_result,
)


def dummy_function(a: int, b: int = 0) -> int:
    return a + b


def keyword_only_function(*, value: str = "default") -> str:
    return value


def test_decode_work_message_handles_json_and_plain_text() -> None:
    assert decode_work_message('"string"') == "string"
    assert decode_work_message("{'invalid': 'json'}") == "{'invalid': 'json'}"
    assert decode_work_message("") == {}


@pytest.mark.parametrize("text", ["", "1\n", "true", '{"key": 1}\r\n', " \n\n"])
def test_decode_work_message_unwraps_command_stdout_without_json_inference(
    text: str,
) -> None:
    """Successful command transport delivers its text at the inbox [PL-4.1]."""

    message = json.dumps({"type": "command_output", "result": text})
    assert decode_work_message(message) == text


def test_decode_work_message_preserves_command_spill_reference() -> None:
    """Inbox recognition unwraps the reference without reading its file [MF-2.1]."""

    reference = {
        "type": "large_output",
        "path": "/unavailable/output.txt",
        "size": 7,
        "sha256": "a" * 64,
        "hostname": "other-host",
    }
    message = json.dumps({"type": "command_output", "result": reference})
    assert decode_work_message(message) == reference


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "command_output"},
        {"type": "command_output", "result": None},
        {"type": "command_output", "result": 1},
        {"type": "command_output", "result": True},
        {"type": "command_output", "result": ["text"]},
        {"type": "command_output", "result": "text", "extra": True},
        {"type": "command_output", "result": {"type": "large_output"}},
        {
            "type": "command_output",
            "result": {
                "type": "large_output",
                "path": "x",
                "size": -1,
                "sha256": "a" * 64,
            },
        },
        {
            "type": "failed_output",
            "return_code": 7,
            "error": "failed",
            "result": "text",
        },
        {"type": "ordinary", "result": "text"},
    ],
)
def test_decode_work_message_leaves_nonmatching_transport_as_ordinary_input(
    payload: dict[str, Any],
) -> None:
    """Reserved-looking inbox data cannot raise a new pre-work error [MF-2]."""

    assert decode_work_message(json.dumps(payload)) == payload


def test_prepare_call_arguments_merges_spec_and_work_item() -> None:
    args, kwargs = prepare_call_arguments([1], {"b": 2}, {"kwargs": {"c": 3}})
    assert args == [1]
    assert kwargs == {"b": 2, "c": 3}


def test_prepare_call_arguments_treats_empty_mapping_as_no_payload() -> None:
    args, kwargs = prepare_call_arguments([], {"value": "ok"}, {})
    assert args == []
    assert kwargs == {"value": "ok"}


def test_execute_function_target_invokes_callable() -> None:
    module_path = f"{__name__}:dummy_function"
    result = execute_function_target(module_path, {"args": [2], "kwargs": {"b": 5}})
    assert result == 7


def test_execute_function_target_kwargs_only_ignores_empty_work_item() -> None:
    module_path = f"{__name__}:keyword_only_function"
    result = execute_function_target(module_path, {}, kwargs={"value": "ok"})
    assert result == "ok"


def test_execute_function_target_prefers_bundle_module(tmp_path: Path) -> None:
    helper = tmp_path / "helper_module.py"
    helper.write_text(
        ("def bundle_sum(a: int, b: int = 0) -> int:\n    return a + b + 10\n"),
        encoding="utf-8",
    )

    result = execute_function_target(
        "helper_module:bundle_sum",
        {"args": [2], "kwargs": {"b": 5}},
        bundle_root=str(tmp_path),
    )

    assert result == 17


def test_execute_command_target_runs_subprocess(tmp_path: Path) -> None:
    script = "import sys; print('ok'); sys.exit(0)"
    completed = execute_command_target(
        sys.executable,
        {},
        args=["-c", script],
        working_dir=str(tmp_path),
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "ok"


def test_serialize_result_prefers_json() -> None:
    assert serialize_result({"a": 1}) == '{"a": 1}'
    assert serialize_result("text") == "text"


def test_execute_command_target_failure_returns_nonzero(tmp_path: Path) -> None:
    script = "import sys; sys.exit(3)"
    completed = execute_command_target(
        sys.executable,
        {},
        args=["-c", script],
        working_dir=str(tmp_path),
    )
    assert completed.returncode == 3
