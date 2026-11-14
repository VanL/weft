"""Tests for shared target execution helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import pytest

from weft.core.agents.runtime import AgentExecutionResult
from weft.core.targets import (
    PreparedOutput,
    PreparedTaskResult,
    decode_work_message,
    execute_command_target,
    execute_function_target,
    prepare_agent_execution,
    prepare_call_arguments,
    prepare_output,
    prepare_task_result,
    prepared_result_from_payload,
    prepared_result_to_payload,
    project_task_result,
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


class PrintableValue:
    """Non-JSON object with a deterministic whole-value representation."""

    def __str__(self) -> str:
        return "printable value"

    def __repr__(self) -> str:
        return "PrintableValue()"


@pytest.mark.parametrize(
    ("value", "output_format", "text", "projection"),
    [
        ("null", "text", "null", "null"),
        ("true", "text", "true", "true"),
        ("1", "text", "1", "1"),
        ('{"a": 1}', "text", '{"a": 1}', '{"a": 1}'),
        (" \n", "text", " \n", " \n"),
        ("", "text", "", ""),
        (None, "json", "null", None),
        (True, "json", "true", True),
        (1, "json", "1", 1),
        ((1, "a"), "json", '[1, "a"]', [1, "a"]),
        (
            {1: "first", "1": "last"},
            "json",
            '{"1": "first", "1": "last"}',
            {"1": "last"},
        ),
        (PrintableValue(), "text", "printable value", "printable value"),
        (
            {"nested": PrintableValue()},
            "text",
            "{'nested': PrintableValue()}",
            "{'nested': PrintableValue()}",
        ),
    ],
)
def test_prepared_value_preserves_exact_output_and_direct_interpretation(
    value: Any,
    output_format: Literal["text", "json"],
    text: str,
    projection: Any,
) -> None:
    """Tags disambiguate JSON-looking text; public text is never re-encoded."""
    prepared = prepare_task_result(value)
    assert prepared == PreparedTaskResult(
        kind="value",
        outputs=(PreparedOutput(format=output_format, text=text),),
        agent_execution=None,
    )
    assert serialize_result(value) == text
    assert prepared_result_to_payload(prepared) == {
        "kind": "value",
        "outputs": [{"format": output_format, "text": text}],
        "agent_execution_json": None,
    }
    projected = project_task_result(prepared)
    assert projected == projection
    assert type(projected) is type(projection)


@pytest.mark.parametrize(
    ("value", "output_format", "text"),
    [
        (float("nan"), "json", "NaN"),
        (float("inf"), "json", "Infinity"),
        (float("-inf"), "json", "-Infinity"),
        ("\ud800", "text", "\ud800"),
    ],
)
def test_prepared_output_keeps_legacy_nonfinite_and_surrogate_policy(
    value: Any, output_format: Literal["text", "json"], text: str
) -> None:
    assert prepare_output(value) == PreparedOutput(format=output_format, text=text)


def test_public_output_cycle_is_fatal_but_agent_metadata_cycle_falls_back() -> None:
    """Auxiliary metadata keeps its existing best-effort policy [AR-5]."""
    cycle: dict[str, Any] = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="Circular reference"):
        prepare_output(cycle)

    prepared = prepare_task_result(
        AgentExecutionResult(
            runtime="test",
            model=None,
            output_mode="text",
            outputs=("ok",),
            metadata=cycle,
        )
    )
    assert prepared.agent_execution == {
        "runtime": "test",
        "model": None,
        "output_mode": "text",
        "status": "completed",
    }
    projected = project_task_result(prepared)
    assert isinstance(projected, AgentExecutionResult)
    assert projected.outputs == ("ok",)
    assert projected.metadata == {}
    assert projected.usage is None
    assert projected.tool_trace == ()
    assert projected.artifacts == ()


def test_agent_preparation_detaches_metadata_and_preserves_output_boundaries() -> None:
    """Public items stay separate from the detached execution log snapshot."""
    values = ["original"]
    metadata = {"values": values, "custom": PrintableValue()}
    result = AgentExecutionResult(
        runtime="test",
        model="model",
        output_mode="messages",
        outputs=("1", {"answer": 42}),
        metadata=metadata,
        usage={"tokens": 3},
        tool_trace=({"name": "tool"},),
        artifacts=({"path": "x"},),
    )
    prepared = prepare_task_result(result)
    values.append("changed")
    expected_metadata = {
        "runtime": "test",
        "model": "model",
        "output_mode": "messages",
        "status": "completed",
        "metadata": {"values": ["original"], "custom": "printable value"},
        "usage": {"tokens": 3},
        "tool_trace": [{"name": "tool"}],
        "artifacts": [{"path": "x"}],
    }
    assert prepared.outputs == (
        PreparedOutput(format="text", text="1"),
        PreparedOutput(format="json", text='{"answer": 42}'),
    )
    assert prepared.agent_execution == expected_metadata
    assert prepare_task_result(prepared) is prepared
    assert prepared_result_to_payload(prepared)["agent_execution_json"] == (
        '{"runtime": "test", "model": "model", "output_mode": "messages", "status": "completed", '
        '"metadata": {"values": ["original"], "custom": "printable value"}, "usage": {"tokens": 3}, '
        '"tool_trace": [{"name": "tool"}], "artifacts": [{"path": "x"}]}'
    )
    projected = project_task_result(prepared)
    assert isinstance(projected, AgentExecutionResult)
    assert projected.outputs == ("1", {"answer": 42})
    assert projected.metadata == expected_metadata["metadata"]
    assert projected.usage == {"tokens": 3}
    assert projected.tool_trace == ({"name": "tool"},)
    assert projected.artifacts == ({"path": "x"},)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime", ""),
        ("runtime", 1),
        ("output_mode", ""),
        ("output_mode", None),
        ("status", ""),
        ("status", False),
        ("model", 1),
    ],
)
def test_agent_preparation_rejects_invalid_identity(field: str, value: Any) -> None:
    """Existing session identity predicates run before the wire boundary."""
    fields = {
        "runtime": "test",
        "model": None,
        "output_mode": "text",
        "outputs": (),
        field: value,
    }
    with pytest.raises(TypeError, match=field):
        prepare_agent_execution(AgentExecutionResult(**fields))


def test_private_payload_reader_keeps_output_text_opaque() -> None:
    """Only a direct-call projection interprets output JSON [CC-3.5]."""
    prepared = prepared_result_from_payload(
        {
            "kind": "value",
            "outputs": [{"format": "json", "text": "not JSON"}],
            "agent_execution_json": None,
        }
    )
    assert prepared.outputs == (PreparedOutput(format="json", text="not JSON"),)
    with pytest.raises(ValueError):
        project_task_result(prepared)


def test_private_agent_payload_reader_parses_metadata_without_second_schema() -> None:
    prepared = prepared_result_from_payload(
        {
            "kind": "agent",
            "outputs": [],
            "agent_execution_json": '{"retained": 1}',
        }
    )
    assert prepared == PreparedTaskResult(
        kind="agent", outputs=(), agent_execution={"retained": 1}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": None},
        {"kind": "unknown"},
        {"kind": None},
        {"outputs": ()},
        {"outputs": []},
        {"outputs": [None]},
        {"outputs": [{"format": "json", "text": None}]},
        {"outputs": [{"format": "unknown", "text": "x"}]},
        {"outputs": [{"format": "text", "text": "x", "extra": None}]},
        {"outputs": [{"format": "text", "text": "x"}, {"format": "text", "text": "y"}]},
        {"agent_execution_json": "{}"},
    ],
)
def test_private_value_payload_reader_rejects_malformed_structure(
    changes: dict[str, Any],
) -> None:
    payload = {
        "kind": "value",
        "outputs": [{"format": "text", "text": "ok"}],
        "agent_execution_json": None,
    }
    payload.update(changes)
    with pytest.raises((TypeError, ValueError)):
        prepared_result_from_payload(payload)


@pytest.mark.parametrize("payload", [None, [], {}, {"kind": "value", "outputs": []}])
def test_private_payload_reader_requires_complete_object(payload: Any) -> None:
    with pytest.raises(TypeError):
        prepared_result_from_payload(payload)


@pytest.mark.parametrize("metadata", [None, 1, "[1]", "not JSON"])
def test_private_agent_payload_reader_rejects_bad_metadata(metadata: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        prepared_result_from_payload(
            {"kind": "agent", "outputs": [], "agent_execution_json": metadata}
        )


def test_execute_command_target_failure_returns_nonzero(tmp_path: Path) -> None:
    script = "import sys; sys.exit(3)"
    completed = execute_command_target(
        sys.executable,
        {},
        args=["-c", script],
        working_dir=str(tmp_path),
    )
    assert completed.returncode == 3
