"""The shared short-form derivation boundary [OBS.5]."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import DEFAULT_TASK_COMPLETION_TIMEOUT, WeftTestHarness
from weft.commands.tasks import resolve_full_tid
from weft.context import build_context
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import IOSection, SpecSection, TaskSpec
from weft.helpers import iter_queue_json_entries, tid_short_form
from weft.liveness.host import inspect_host_process

pytestmark = pytest.mark.shared


@pytest.mark.parametrize("tid", ["", "123", "undecidable", "x" * 19, "²" * 19])
def test_short_form_rejects_non_derivable_ids(tid: str) -> None:
    with pytest.raises(ValueError, match="19-digit"):
        tid_short_form(tid)


def test_short_form_separates_old_forty_second_collision() -> None:
    first = 1_760_000_000_000_000_000 & ~0xFFF
    second = first + 40_000_000_000
    assert first & 0xFFF == second & 0xFFF == 0
    assert str(first)[-10:] == str(second)[-10:]
    assert tid_short_form(str(first)) != tid_short_form(str(second))


def test_short_form_zero_pads_folded_grain() -> None:
    tid = str((43_000 * 10**10 + 123) << 12)
    assert tid_short_form(tid) == "0000000123"


@pytest.mark.parametrize(
    "counter,expected",
    [(0, "7500000000"), (1, "7502441406"), (1000, "9941406000"), (4095, "7497557570")],
)
def test_short_form_folds_counter_and_wraps(counter: int, expected: str) -> None:
    assert tid_short_form(str(1_760_000_000_000_000_000 | counter)) == expected


def test_short_form_distinguishes_every_counter_in_one_grain() -> None:
    base = 1_760_000_000_000_000_000 & ~0xFFF
    forms = {tid_short_form(str(base | counter)) for counter in range(4096)}
    assert len(forms) == 4096
    assert all(
        len(short) == 10 and short.isascii() and short.isdecimal() for short in forms
    )


def test_invalid_expected_tid_preserves_exact_process_evidence() -> None:
    process = psutil.Process(os.getpid())
    observation = inspect_host_process(
        process.pid, process.create_time(), expected_tid="invalid"
    )
    assert observation.evidence == "live"
    assert observation.reason == "identity_match_title_unconfirmed"


def test_old_stored_short_resolves_from_full_tid(tmp_path: Path) -> None:
    context = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1760000000123456789"
    old_short = tid[-10:]
    assert old_short != tid_short_form(tid)
    queue = context.queue(task_state_queue_name(tid), persistent=False)
    try:
        queue.write(json.dumps({"full": tid, "short": old_short}))
        assert resolve_full_tid(context, tid_short_form(tid)) == tid
        assert resolve_full_tid(context, tid) == tid
        assert resolve_full_tid(context, old_short) is None
    finally:
        queue.close()


def test_consumer_publishes_and_matches_new_short_title(
    broker_env: BrokerEnv, weft_harness: WeftTestHarness
) -> None:
    if importlib.util.find_spec("setproctitle") is None:
        pytest.skip("setproctitle is not installed")
    tid = str(time.time_ns())
    spec = TaskSpec(
        tid=tid,
        name="short-form-test",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            enable_process_title=False,
            weft_context=str(weft_harness.root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
    )
    # Native title libraries own process-global argv memory. Importing stock
    # setproctitle after another test started macOS's deferred writer bypasses
    # its padded handoff and can permanently truncate subsequent titles.
    script = """
import os
import sys
import psutil
import setproctitle
from weft.context import build_context
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import tid_short_form
from weft.liveness.host import inspect_host_process

spec = TaskSpec.model_validate_json(sys.stdin.read())
context = build_context(spec_context=spec.spec.weft_context)
task = Consumer(context.broker_target, spec)
try:
    task.enable_process_title = True
    task._update_process_title("running")
    assert (
        f"-{tid_short_form(task.tid)}:short-form-test:running"
        in setproctitle.getproctitle()
    ), setproctitle.getproctitle()
    process = psutil.Process(os.getpid())
    observation = inspect_host_process(
        process.pid, process.create_time(), expected_tid=task.tid
    )
    assert observation.evidence == "live", observation
    expected_reason = (
        "identity_match_title_unconfirmed"
        if sys.platform == "win32"
        else "identity_match_title_match"
    )
    assert observation.reason == expected_reason, observation
finally:
    task.stop()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=spec.model_dump_json(),
        capture_output=True,
        text=True,
        check=False,
        timeout=DEFAULT_TASK_COMPLETION_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _target, make_queue = broker_env
    queue = make_queue(task_state_queue_name(tid))
    rows = [
        row for row, _stamp in iter_queue_json_entries(queue) if row.get("full") == tid
    ]
    assert rows[-1]["short"] == tid_short_form(tid)
    # An old display field remains usable after a real Consumer publication.
    queue.write(json.dumps({**rows[-1], "short": tid[-10:]}))
    assert resolve_full_tid(weft_harness.context, tid_short_form(tid)) == tid
