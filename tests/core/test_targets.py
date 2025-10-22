"""Tests for shared target execution helpers."""

from __future__ import annotations

import sys

from weft.core.targets import (
    decode_work_message,
    execute_command_target,
    execute_function_target,
    prepare_call_arguments,
    serialize_result,
)


def dummy_function(a: int, b: int = 0) -> int:
    return a + b


def test_decode_work_message_handles_json_and_plain_text():
    assert decode_work_message('"string"') == "string"
    assert decode_work_message("{'invalid': 'json'}") == "{'invalid': 'json'}"
    assert decode_work_message("") == {}


def test_prepare_call_arguments_merges_spec_and_work_item():
    args, kwargs = prepare_call_arguments([1], {"b": 2}, {"kwargs": {"c": 3}})
    assert args == [1]
    assert kwargs == {"b": 2, "c": 3}


def test_execute_function_target_invokes_callable():
    module_path = f"{__name__}:dummy_function"
    result = execute_function_target(module_path, {"args": [2], "kwargs": {"b": 5}})
    assert result == 7


def test_execute_command_target_runs_subprocess(tmp_path):
    script = "import sys; print('ok'); sys.exit(0)"
    completed = execute_command_target(
        [sys.executable, "-c", script], {}, working_dir=str(tmp_path)
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "ok"


def test_serialize_result_prefers_json():
    assert serialize_result({"a": 1}) == '{"a": 1}'
    assert serialize_result("text") == "text"


def test_execute_command_target_failure_returns_nonzero(tmp_path):
    script = "import sys; sys.exit(3)"
    completed = execute_command_target(
        [sys.executable, "-c", script], {}, working_dir=str(tmp_path)
    )
    assert completed.returncode == 3
