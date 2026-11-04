"""TID selector and mapping contracts [CLI-1.2.3]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._exceptions import InvalidTID
from weft.commands import system as system_cmd
from weft.commands import task_monitor as task_monitor_cmd
from weft.commands import tasks as task_cmd
from weft.commands._spawn_submission import (
    _mapping_exists_for_tid,
    _spawn_reconciliation_queue_specs,
)
from weft.commands._task_snapshot_reducer import reduce_task_event
from weft.context import WeftContext, build_context
from weft.core.task_state import list_task_state_tids
from weft.helpers import encode_tid

pytestmark = pytest.mark.shared


@pytest.fixture
def mapping_context(tmp_path: Path) -> WeftContext:
    return build_context(spec_context=prepare_project_root(tmp_path))


def write_mapping(context: WeftContext, full: str, short: str, **fields: object) -> int:
    queue = context.queue(f"weft.state.tasks.{full}", persistent=False)
    try:
        return queue.write(json.dumps({"full": full, "short": short, **fields}))
    finally:
        queue.close()


def test_mapping_ignores_newer_malformed_row(
    mapping_context: WeftContext,
) -> None:
    full = "1760000000987654321"
    write_mapping(mapping_context, full, "old-display", terminal=False)
    write_mapping(mapping_context, full, "", terminal=True)
    write_mapping(mapping_context, "undecidable", encode_tid(full))
    mapping = task_cmd.mapping_for_tid(mapping_context, full)
    assert mapping is not None
    assert mapping["terminal"] is False
    assert (
        system_cmd._latest_tid_state_entries(mapping_context)[full]["terminal"] is False
    )


@pytest.mark.parametrize("tid", ["", "undecidable", "123", "x" * 19])
def test_status_event_folds_skip_non_derivable_ids(tid: str) -> None:
    payload = {"tid": tid, "status": "running", "event": "task_started"}
    assert reduce_task_event(None, payload, 1, tid_filters=None) is None
    assert system_cmd._public_status_event(payload, 1, status_filter=None) is None
    valid = {**payload, "tid": "1760000000987654321"}
    assert reduce_task_event(None, valid, 2, tid_filters=None) is not None
    assert system_cmd._public_status_event(valid, 2, status_filter=None) is not None
    scan = task_monitor_cmd._reduce_task_log([(payload, 1), (valid, 2)], limit=None)
    assert set(scan.reduced) == {valid["tid"]}


@pytest.mark.parametrize("selector", ["external", "1234567890"])
def test_unresolved_status_selector_does_not_derive_a_full_tid(
    mapping_context: WeftContext,
    selector: str,
) -> None:
    assert task_cmd.task_status(selector, context=mapping_context) is None


def test_full_tid_filter_excludes_other_tasks() -> None:
    first = "1760000000123456789"
    second = "1760000000987654321"
    for tid in (first, second):
        record = reduce_task_event(
            None, {"tid": tid, "status": "running"}, 1, tid_filters={first}
        )
        assert (record is not None) is (tid == first)


def test_encoded_selector_decodes_without_reading_state(
    mapping_context: WeftContext,
) -> None:
    full = "1760000000987654321"
    assert list(list_task_state_tids(mapping_context)) == []
    assert task_cmd.resolve_full_tid(encode_tid(full)) == full
    assert task_cmd.resolve_full_tid(encode_tid(full).lower()) == full
    assert task_cmd.resolve_full_tid("T" + encode_tid(full)) == full


@pytest.mark.parametrize("value", ["１" * 19, "²" * 19])
def test_reverse_selector_rejects_non_ascii_ids(
    mapping_context: WeftContext,
    value: str,
) -> None:
    assert task_cmd.task_tid(reverse=value, context=mapping_context) is None
    with pytest.raises(InvalidTID):
        task_cmd.cmd_task_tid(reverse=value, context=mapping_context.root)


def test_submission_mapping_proof_requires_valid_row_shape(
    mapping_context: WeftContext,
) -> None:
    tid = "1760000000123456789"
    write_mapping(mapping_context, tid, "")
    assert not _mapping_exists_for_tid(mapping_context, tid)
    write_mapping(mapping_context, tid, "valid-display")
    assert _mapping_exists_for_tid(mapping_context, tid)


def test_malformed_only_namespace_yields_no_mapping(
    mapping_context: WeftContext,
) -> None:
    full = "1760000000987654321"
    queue = mapping_context.queue(f"weft.state.tasks.{full}", persistent=False)
    try:
        queue.write("not-json")
    finally:
        queue.close()
    assert task_cmd.mapping_for_tid(mapping_context, full) is None
    assert not _mapping_exists_for_tid(mapping_context, full)


def test_submission_observes_first_valid_namespace_publication(
    mapping_context: WeftContext,
) -> None:
    """Spawn proof reads its exact queue, including its first publication."""
    tid = "1760000000123456789"
    assert not _mapping_exists_for_tid(mapping_context, tid)
    queue = mapping_context.queue(f"weft.state.tasks.{tid}", persistent=False)
    try:
        queue.write(json.dumps({"full": tid, "short": encode_tid(tid)}))
    finally:
        queue.close()
    assert _mapping_exists_for_tid(mapping_context, tid)


@pytest.mark.parametrize("tid", ["missing", "123", "１" * 19])
def test_non_task_selectors_have_no_runtime_state_or_state_subscription(
    mapping_context: WeftContext, tid: str
) -> None:
    assert not _mapping_exists_for_tid(mapping_context, tid)
    assert task_cmd.mapping_for_tid(mapping_context, tid) is None
    assert all(
        not name.startswith("weft.state.tasks.")
        for name, _persistent in _spawn_reconciliation_queue_specs(mapping_context, tid)
    )


@pytest.mark.parametrize("tid", ["missing", "123"])
def test_unresolved_control_selector_omits_task_state_queue(
    mapping_context: WeftContext, tid: str
) -> None:
    assert task_cmd._await_control_surface(mapping_context, tid, timeout=0.0) == (
        None,
        None,
    )
