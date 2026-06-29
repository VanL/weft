"""Property-based tests for read-only task evidence queue helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers.hypothesis_strategies import taskspec_tid_strings
from weft.core.task_evidence import (
    control_queue_names_for_tid,
    queue_names_for_tid,
)

pytestmark = [pytest.mark.shared, pytest.mark.property]


_QUEUE_NAME_VALUES = st.one_of(
    st.none(),
    st.just(""),
    st.text(min_size=1, max_size=32),
)


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
