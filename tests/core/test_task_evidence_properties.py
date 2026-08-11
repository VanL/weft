"""Property-based tests for read-only task evidence queue helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers.hypothesis_strategies import taskspec_tid_strings
from weft.core.task_evidence import (
    TaskEvidenceSnapshot,
    control_queue_names_for_tid,
    monitor_failure_classification,
    queue_names_for_tid,
    state_timestamp_from_log_payload,
    status_from_log_payload,
    task_name_from_taskspec,
)

pytestmark = [pytest.mark.shared, pytest.mark.property]


_QUEUE_NAME_VALUES = st.one_of(
    st.none(),
    st.just(""),
    st.text(min_size=1, max_size=32),
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, None),
        ({"taskspec": []}, None),
        ({"taskspec": {"state": []}}, None),
        ({"taskspec": {"state": {"started_at": "12"}}}, None),
        ({"taskspec": {"state": {"started_at": 12}}}, 12),
    ],
)
def test_state_timestamp_parser_preserves_strict_nested_shape(
    payload: dict[str, object],
    expected: int | None,
) -> None:
    assert state_timestamp_from_log_payload(payload, "started_at") == expected


@pytest.mark.parametrize(
    ("classification", "status", "expected"),
    [
        ("terminal_log", "failed", "domain_failure"),
        ("terminal_log", "completed", "terminal_log"),
        ("runtime_unavailable", "failed", "runtime_unavailable"),
    ],
)
def test_monitor_failure_classification_maps_only_failed_terminal_logs(
    classification: str,
    status: str,
    expected: str,
) -> None:
    snapshot = TaskEvidenceSnapshot(
        tid="123",
        status=status,
        classification=classification,
        source="task_log",
        terminal=True,
    )

    assert monitor_failure_classification(snapshot) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, None),
        ({}, None),
        ({"name": 1}, None),
        ({"name": ""}, None),
        ({"name": "worker"}, "worker"),
    ],
)
def test_taskspec_name_parser_accepts_only_non_empty_top_level_names(
    payload: dict[str, object] | None,
    expected: str | None,
) -> None:
    assert task_name_from_taskspec(payload) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, None),
        ({"status": ""}, None),
        ({"status": 1}, None),
        ({"status": "running"}, "running"),
        ({"status": "running", "taskspec": {"state": {"status": "failed"}}}, "running"),
        ({"taskspec": []}, None),
        ({"taskspec": {"state": []}}, None),
        ({"taskspec": {"state": {"status": ""}}}, None),
        ({"taskspec": {"state": {"status": "failed"}}}, "failed"),
    ],
)
def test_log_status_parser_prefers_top_level_then_nested_taskspec_state(
    payload: dict[str, object],
    expected: str | None,
) -> None:
    assert status_from_log_payload(payload) == expected


@given(
    tid=taskspec_tid_strings(),
    outbox=_QUEUE_NAME_VALUES,
    ctrl_in=_QUEUE_NAME_VALUES,
    ctrl_out=_QUEUE_NAME_VALUES,
)
def test_task_evidence_queue_helpers_preserve_non_empty_overrides(
    tid: str,
    outbox: str | None,
    ctrl_in: str | None,
    ctrl_out: str | None,
) -> None:
    payload = {
        "io": {
            "outputs": {"outbox": outbox},
            "control": {
                "ctrl_in": ctrl_in,
                "ctrl_out": ctrl_out,
            },
        }
    }

    observed_outbox, observed_output_control = queue_names_for_tid(tid, payload)
    observed_input_control, observed_control_output = control_queue_names_for_tid(
        tid,
        payload,
    )

    assert observed_outbox == (outbox or f"T{tid}.outbox")
    assert observed_output_control == (ctrl_out or f"T{tid}.ctrl_out")
    assert observed_input_control == (ctrl_in or f"T{tid}.ctrl_in")
    assert observed_control_output == (ctrl_out or f"T{tid}.ctrl_out")


@given(
    tid=taskspec_tid_strings(),
    payload=st.one_of(st.none(), st.dictionaries(st.text(), st.integers(), max_size=4)),
)
def test_task_evidence_queue_helpers_fall_back_for_missing_io(
    tid: str,
    payload: dict[str, int] | None,
) -> None:
    assert queue_names_for_tid(tid, payload) == (
        f"T{tid}.outbox",
        f"T{tid}.ctrl_out",
    )
    assert control_queue_names_for_tid(tid, payload) == (
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
    )
