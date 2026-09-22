"""Registry-backed short resolution and batch preflight [CLI-1.2.3]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._exceptions import CommandUsageError, InvalidTID
from weft.client import WeftClient
from weft.commands import system as system_cmd
from weft.commands import task_monitor as task_monitor_cmd
from weft.commands import tasks as task_cmd
from weft.commands._spawn_submission import (
    _mapping_exists_for_tid,
    _spawn_reconciliation_queue_specs,
)
from weft.commands._task_snapshot_reducer import reduce_task_event
from weft.context import WeftContext, build_context
from weft.core.heartbeat import _heartbeat_runtime_handle_is_live
from weft.helpers import tid_short_form

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


@pytest.fixture
def collision(mapping_context: WeftContext) -> tuple[str, str, str]:
    first = "1760000000123456789"
    # Same short under both the current low-digit and planned folded forms.
    second = str(int(first) + (10**10 << 12))
    short = tid_short_form(first)
    assert tid_short_form(second) == short
    write_mapping(mapping_context, first, "obsolete-first", terminal=False)
    write_mapping(mapping_context, first, "obsolete-republished", terminal=True)
    write_mapping(mapping_context, second, "obsolete-second", terminal=False)
    write_mapping(mapping_context, "undecidable", short)
    return first, second, short


@pytest.mark.parametrize("surface", ["task", "status"])
def test_short_collision_names_all_candidates(
    mapping_context: WeftContext,
    collision: tuple[str, str, str],
    surface: str,
) -> None:
    first, second, short = collision
    with pytest.raises(CommandUsageError) as caught:
        if surface == "task":
            task_cmd.resolve_full_tid(mapping_context, short)
        else:
            system_cmd._resolve_tid_filters(mapping_context, short)
    assert first in str(caught.value)
    assert second in str(caught.value)
    assert "undecidable" not in str(caught.value)


@pytest.mark.parametrize("command", ["stop", "kill"])
@pytest.mark.parametrize("surface", ["commands", "client"])
def test_ambiguous_batch_sends_no_control(
    mapping_context: WeftContext,
    collision: tuple[str, str, str],
    command: str,
    surface: str,
) -> None:
    first, second, short = collision
    if surface == "client":
        client = WeftClient(context=mapping_context)
        operation = (
            client.tasks.stop_many if command == "stop" else client.tasks.kill_many
        )
        with pytest.raises(CommandUsageError):
            operation(tids=[first, short])
    else:
        command_operation = (
            task_cmd.stop_tasks if command == "stop" else task_cmd.kill_tasks
        )
        with pytest.raises(CommandUsageError):
            command_operation([first, short], context=mapping_context)
    for tid in (first, second):
        queue = mapping_context.queue(f"T{tid}.ctrl_in", persistent=False)
        try:
            assert queue.peek_many() == []
        finally:
            queue.close()


def test_resolution_derives_short_and_ignores_newer_malformed_mapping(
    mapping_context: WeftContext,
) -> None:
    full = "1760000000987654321"
    write_mapping(mapping_context, full, "old-display", terminal=False)
    write_mapping(mapping_context, full, "", terminal=True)
    write_mapping(mapping_context, "undecidable", tid_short_form(full))
    assert task_cmd.resolve_full_tid(mapping_context, tid_short_form(full)) == full
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


def test_full_selector_excludes_other_colliding_task(
    mapping_context: WeftContext,
    collision: tuple[str, str, str],
) -> None:
    first, second, _short = collision
    filters = system_cmd._resolve_tid_filters(mapping_context, first)
    assert filters == {first}
    for tid in (first, second):
        record = reduce_task_event(
            None, {"tid": tid, "status": "running"}, 1, tid_filters=filters
        )
        assert (record is not None) is (tid == first)


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


@pytest.mark.parametrize("surface", ["task", "status"])
def test_short_resolution_uses_namespace_even_with_malformed_only_rows(
    mapping_context: WeftContext,
    surface: str,
) -> None:
    """A retained namespace name reserves its short ID without JSON evidence."""
    full = "1760000000987654321"
    queue = mapping_context.queue(f"weft.state.tasks.{full}", persistent=False)
    try:
        queue.write("not-json")
    finally:
        queue.close()
    short = tid_short_form(full)
    if surface == "task":
        assert task_cmd.resolve_full_tid(mapping_context, short) == full
    else:
        assert system_cmd._resolve_tid_filters(mapping_context, short) == {full}
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
        queue.write(json.dumps({"full": tid, "short": tid_short_form(tid)}))
    finally:
        queue.close()
    assert _mapping_exists_for_tid(mapping_context, tid)


@pytest.mark.parametrize("tid", ["missing", "123", "１" * 19])
def test_non_task_selectors_have_no_runtime_state_or_state_subscription(
    mapping_context: WeftContext, tid: str
) -> None:
    assert not _mapping_exists_for_tid(mapping_context, tid)
    assert not _heartbeat_runtime_handle_is_live(mapping_context, tid=tid)
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
