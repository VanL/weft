"""Property-based TaskSpec invariant tests."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from tests.helpers.hypothesis_strategies import (
    json_values,
    taskspec_tid_strings,
)
from weft.core.taskspec import LimitsSection, TaskSpec
from weft.core.taskspec.model import resolve_taskspec_payload

pytestmark = [pytest.mark.shared, pytest.mark.property]


_JSON_MAPPING = st.dictionaries(
    st.text(min_size=1, max_size=16),
    json_values(max_leaves=4),
    max_size=4,
)
_ARGS = st.lists(json_values(max_leaves=4), max_size=4)
_METRICS = st.fixed_dictionaries(
    {
        "time": st.one_of(st.none(), st.floats(min_value=0, max_value=1000)),
        "memory": st.one_of(st.none(), st.floats(min_value=0, max_value=4096)),
        "cpu": st.one_of(st.none(), st.integers(min_value=0, max_value=100)),
        "fds": st.one_of(st.none(), st.integers(min_value=0, max_value=4096)),
        "net_connections": st.one_of(
            st.none(),
            st.integers(min_value=0, max_value=4096),
        ),
    }
)


def _minimal_payload(
    *,
    tid: str,
    args: list[object] | None = None,
    keyword_args: dict[str, object] | None = None,
    env: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "tid": tid,
        "name": f"property-{tid}",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "args": args if args is not None else [],
            "keyword_args": keyword_args if keyword_args is not None else {},
            "env": env if env is not None else {},
        },
        "io": {},
        "state": {},
        "metadata": metadata if metadata is not None else {},
    }


@given(
    tid=taskspec_tid_strings(),
    args=_ARGS,
    keyword_args=_JSON_MAPPING,
    env=st.dictionaries(st.text(min_size=1, max_size=12), st.text(max_size=24)),
    metadata=_JSON_MAPPING,
)
def test_resolve_taskspec_payload_does_not_mutate_input(
    tid: str,
    args: list[object],
    keyword_args: dict[str, object],
    env: dict[str, str],
    metadata: dict[str, object],
) -> None:
    payload = _minimal_payload(
        tid=tid,
        args=args,
        keyword_args=keyword_args,
        env=env,
        metadata=metadata,
    )
    original = copy.deepcopy(payload)

    resolved = resolve_taskspec_payload(payload)

    assert payload == original
    assert resolved["io"]["inputs"]["inbox"] == f"T{tid}.inbox"
    assert resolved["io"]["outputs"]["outbox"] == f"T{tid}.outbox"
    assert resolved["io"]["control"]["ctrl_in"] == f"T{tid}.ctrl_in"
    assert resolved["io"]["control"]["ctrl_out"] == f"T{tid}.ctrl_out"


@given(
    tid=taskspec_tid_strings(),
    args=_ARGS,
    keyword_args=_JSON_MAPPING,
    env=st.dictionaries(st.text(min_size=1, max_size=12), st.text(max_size=24)),
)
def test_resolved_taskspec_freezes_spec_and_io(
    tid: str,
    args: list[object],
    keyword_args: dict[str, object],
    env: dict[str, str],
) -> None:
    payload = resolve_taskspec_payload(
        _minimal_payload(
            tid=tid,
            args=args,
            keyword_args=keyword_args,
            env=env,
            metadata={},
        )
    )

    taskspec = TaskSpec.model_validate(payload)

    with pytest.raises(AttributeError):
        taskspec.spec.args = []
    with pytest.raises(AttributeError):
        taskspec.io.outputs = {}
    with pytest.raises((TypeError, AttributeError)):
        taskspec.spec.args.append("changed")
    with pytest.raises((TypeError, AttributeError)):
        taskspec.spec.keyword_args["changed"] = True
    with pytest.raises((TypeError, AttributeError)):
        taskspec.io.outputs["outbox"] = "changed"

    taskspec.set_metadata("owner", "property")
    taskspec.update_metrics(memory=1.0)
    assert taskspec.get_metadata("owner") == "property"
    assert taskspec.state.memory == 1.0


@given(
    memory_mb=st.one_of(st.none(), st.integers(min_value=1, max_value=4096)),
    cpu_percent=st.one_of(st.none(), st.integers(min_value=1, max_value=100)),
    max_fds=st.one_of(st.none(), st.integers(min_value=1, max_value=8192)),
    max_connections=st.one_of(st.none(), st.integers(min_value=0, max_value=8192)),
)
def test_limit_validation_accepts_positive_resource_bounds(
    memory_mb: int | None,
    cpu_percent: int | None,
    max_fds: int | None,
    max_connections: int | None,
) -> None:
    limits = LimitsSection(
        memory_mb=memory_mb,
        cpu_percent=cpu_percent,
        max_fds=max_fds,
        max_connections=max_connections,
    )

    assert limits.memory_mb == memory_mb
    assert limits.cpu_percent == cpu_percent
    assert limits.max_fds == max_fds
    assert limits.max_connections == max_connections


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("memory_mb", 0),
        ("memory_mb", -1),
        ("cpu_percent", 0),
        ("cpu_percent", 101),
        ("max_fds", 0),
        ("max_fds", -1),
        ("max_connections", -1),
    ),
)
def test_limit_validation_rejects_invalid_resource_bounds(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        LimitsSection(**{field: value})


@given(updates=st.lists(_METRICS, min_size=1, max_size=16))
def test_metric_updates_preserve_current_and_peak_relationship(
    updates: list[dict[str, float | int | None]],
) -> None:
    taskspec = TaskSpec.model_validate(
        resolve_taskspec_payload(_minimal_payload(tid="1755033993077017000"))
    )

    for update in updates:
        taskspec.update_metrics(**update)
        if taskspec.state.memory is not None:
            assert taskspec.state.peak_memory is not None
            assert taskspec.state.memory <= taskspec.state.peak_memory
        if taskspec.state.cpu is not None:
            assert taskspec.state.peak_cpu is not None
            assert taskspec.state.cpu <= taskspec.state.peak_cpu
        if taskspec.state.fds is not None:
            assert taskspec.state.peak_fds is not None
            assert taskspec.state.fds <= taskspec.state.peak_fds
        if taskspec.state.net_connections is not None:
            assert taskspec.state.peak_net_connections is not None
            assert taskspec.state.net_connections <= taskspec.state.peak_net_connections


_STATUS_OPERATIONS: dict[str, Callable[[TaskSpec], None]] = {
    "started": lambda taskspec: taskspec.mark_started(),
    "running": lambda taskspec: taskspec.mark_running(),
    "completed": lambda taskspec: taskspec.mark_completed(),
    "failed": lambda taskspec: taskspec.mark_failed(error="generated"),
    "timeout": lambda taskspec: taskspec.mark_timeout(error="generated"),
    "cancelled": lambda taskspec: taskspec.mark_cancelled(reason="generated"),
    "killed": lambda taskspec: taskspec.mark_killed(reason="generated"),
}


@given(
    operations=st.lists(
        st.sampled_from(tuple(_STATUS_OPERATIONS)),
        min_size=1,
        max_size=12,
    )
)
def test_status_operation_sequences_preserve_timestamp_invariants(
    operations: list[str],
) -> None:
    taskspec = TaskSpec.model_validate(
        resolve_taskspec_payload(_minimal_payload(tid="1755033993077017000"))
    )

    for operation in operations:
        try:
            _STATUS_OPERATIONS[operation](taskspec)
        except ValueError:
            pass

        if taskspec.state.status in {"spawning", "running"}:
            assert taskspec.state.started_at is not None
        if taskspec.state.status in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        }:
            assert taskspec.state.completed_at is not None
        if (
            taskspec.state.started_at is not None
            and taskspec.state.completed_at is not None
        ):
            assert taskspec.state.completed_at >= taskspec.state.started_at
