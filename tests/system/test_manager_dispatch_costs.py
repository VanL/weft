"""Real dispatch-cost measurements for the October 27 serial-dispatch plan.

Draft integration target: tests/system/test_manager_dispatch_costs.py.
Spec: 03-Manager_Architecture.md [MA-1.8], [MA-3]; 08-Testing_Strategy.md [TS-0].

Run each actual checkout (A, B, C1) with the same command and capture mode:
  PYTEST_WEFT_DISPATCH_ARTIFACT_DIR=/absolute/persistent/path/A <pytest command>
Use the normal repo worker configuration. Presence of this environment variable
opts into the experiment and selects its artifact destination; it never selects
production arm behavior. If absent, collection skips this experiment before PG
fixture startup. Present empty, relative, existing, or unwritable destinations fail.
Use a fresh parent directory for every invocation, including any extra batch set;
exactly one run directory below it must contain the completion marker.
A skipped experiment is not passed acceptance; original correctness gates remain.

Select one node at a time from the intended source checkout, after loading .envrc:
  PYTHONPATH="$PWD" PYTEST_WEFT_DISPATCH_ARTIFACT_DIR=/absolute/run/sqlite/A-before/quiet \
    /absolute/shared-venv/bin/python -m pytest -m '' \
    'tests/system/test_manager_dispatch_costs.py::test_manager_dispatch_costs[quiet]'
  PYTHONPATH="$PWD" PYTEST_WEFT_DISPATCH_ARTIFACT_DIR=/absolute/run/sqlite/A-before/trace \
    /absolute/shared-venv/bin/python -m pytest -m '' \
    'tests/system/test_manager_dispatch_costs.py::test_manager_dispatch_costs[trace]'
  PYTHONPATH="$PWD" PYTEST_WEFT_DISPATCH_ARTIFACT_DIR=/absolute/run/postgres/A-before/quiet \
    bin/pytest-pg --all \
    'tests/system/test_manager_dispatch_costs.py::test_manager_dispatch_costs[quiet]'
  PYTHONPATH="$PWD" PYTEST_WEFT_DISPATCH_ARTIFACT_DIR=/absolute/run/postgres/A-before/trace \
    bin/pytest-pg --all \
    'tests/system/test_manager_dispatch_costs.py::test_manager_dispatch_costs[trace]'
Run both selected modes on each backend in separate invocations. Require pytest
success AND completion.json with actual child source preflight, warmup plus
three measured repetitions and all raw files; a skip or missing completion marker is not a measurement result.
Each invocation warms once and records three samples, plus separate STOP trials.
Cross-checkout order is A/B/A batches, then B/C1/B, not single-sample alternation.
Select exactly one node ID, test_manager_dispatch_costs[quiet] or [trace], per
measurement invocation. Modes select observation, never production source arms.
Quiet retains three causal boundary hooks, so neither mode claims zero overhead.
Forwarding the trace emitter does not establish that its overhead is negligible.
The existing six/eight-parent acceptance tests remain unchanged and must also run.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from itertools import pairwise
from math import ceil
from pathlib import Path
from statistics import fmean, median
from typing import Any, Literal

import pytest

import simplebroker
import tests.helpers.family_workload as family_workload_module
import tests.helpers.weft_harness as harness_module
import weft
import weft.core.control_probe as control_probe_module
import weft.core.manager as manager_module
from tests.helpers.family_workload import FAMILY_EVENTS
from tests.helpers.test_backend import active_test_backend
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    MANAGER_CHILD_LAUNCH_WORKER_LANE,
    MANAGER_STARTUP_FAST_PATH_WINDOW,
    TERMINAL_TASK_STATUSES,
    WEFT_SPAWN_REQUESTS_QUEUE,
    resolve_runtime_config,
)
from weft.client import WeftClient
from weft.context import WeftContext, build_context
from weft.core.control_messages import encode_control_message
from weft.core.runtime_identity import get_runtime_identity
from weft.core.spawn_requests import submit_spawn_request
from weft.core.task_evidence import peek_terminal_ctrl_out_evidence
from weft.core.tasks.service import ServiceWorkerEvent

pytest_plugins = ["tests.helpers.pytest_pg_runtime"]
pytestmark = [pytest.mark.shared, pytest.mark.slow, pytest.mark.timeout(300)]


@pytest.fixture
def dispatch_backend(request: pytest.FixtureRequest) -> None:
    """Provision native PG capacity before any harness or target exists."""
    if active_test_backend() == "postgres":
        request.getfixturevalue("isolated_postgres_server")


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _manifest(
    request: pytest.FixtureRequest, destination: Path, mode: Literal["quiet", "trace"]
) -> None:
    """Reject a shared editable install accidentally importing the wrong arm."""
    root = Path(request.config.rootpath).resolve()
    simplebroker_file = inspect.getfile(simplebroker)
    imports = {
        "weft/__init__.py": weft.__file__,
        "weft/core/manager.py": manager_module.__file__,
        "tests/helpers/weft_harness.py": harness_module.__file__,
        "tests/helpers/family_workload.py": family_workload_module.__file__,
        "tests/system/test_manager_dispatch_costs.py": __file__,
    }
    for relative, loaded in imports.items():
        assert Path(loaded).resolve() == root / relative, (relative, loaded, root)
    source_hashes = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in imports
    }
    untracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "weft",
            "tests",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    untracked = sorted(path for path in untracked if path)
    unexpected = set(untracked) - {"tests/system/test_manager_dispatch_costs.py"}
    assert not unexpected, (
        f"Untracked sources are outside arm provenance: {sorted(unexpected)}"
    )
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary", "--", "weft", "tests", "pyproject.toml"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (destination / "source.diff").write_bytes(diff)
    _json(
        destination / "manifest.json",
        {
            "measurement_mode": mode,
            "test_file": str(Path(__file__).resolve()),
            "weft_file": weft.__file__,
            "simplebroker_file": simplebroker_file,
            "simplebroker_sources_sha256": hashlib.sha256(
                b"".join(
                    path.read_bytes()
                    for path in sorted(
                        Path(simplebroker_file).resolve().parent.rglob("*.py")
                    )
                )
            ).hexdigest(),
            "cwd": str(Path.cwd()),
            "imported_files": imports,
            "sys_path": sys.path,
            "pythonpath": os.environ.get("PYTHONPATH"),
            "git_head": head,
            "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "source_hashes": source_hashes,
            "untracked_source_files": untracked,
            "source_provenance_scope": "HEAD plus source.diff identify tracked Weft state; hashes bind the actual imported owners and the sole allowed untracked source, this measurement test. Other nonignored untracked weft/tests files fail before preflight. SimpleBroker's separate code fingerprint remains because it is outside Weft's Git provenance.",
            "external_analysis_requirement": "For B and successors that contain its gate, require trace maintenance_gate_calls_observed > 0 in every sample before using maintenance-yield metrics; otherwise mark them unavailable. A zero count alone cannot distinguish absent code from an unobserved gate. Production revision determines the arm, never a harness selector.",
            "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "harness_sha256": hashlib.sha256(
                Path(harness_module.__file__).read_bytes()
            ).hexdigest(),
            "python": sys.version,
            "platform": platform.platform(),
            "worker": os.environ.get("PYTEST_XDIST_WORKER"),
            "worker_count": os.environ.get("PYTEST_XDIST_WORKER_COUNT"),
            "capture": request.config.getoption("capture"),
            "pytest_args": list(request.config.invocation_params.args),
            "harness_activity_wait_bound_seconds": 0.01,
            "trace_sink": "original emitter forwarded and copied"
            if mode == "trace"
            else "default logging; emitter untouched",
            "measurement_scope": "quiet: default logging with three causal boundary hooks; internal metrics unavailable. trace: full counts/spans and instrumented latency. Neither overhead is measured or assumed to cancel.",
            "quiet_causal_hooks": [
                "_handle_child_launch_result",
                "_handle_control_command",
                "_finish_graceful_shutdown",
            ],
            "child_import_provenance": "mandatory integrated real Manager/Consumer/function-worker preflight before warmup; verified source-provenance.json required for completion",
            "method": "one warmup plus three measured samples; separate STOP workload",
            "percentile_method": "nearest rank ceil(0.95*n), with sample count and raw values; empty is unavailable",
            "temperature": "source preflight precedes the retained warmup; no forced host-cache cold claim; every sample has a fresh Manager/harness",
            "comparison_rule": "Use A/B/A batches per backend and mode, each warmup plus three measured repetitions with one server lifetime; later B/C1/B. Quiet useful-work elapsed, canonical PING call-to-return, and STOP write-to-handler-return are primary; all other series are secondary. Regression requires all three candidate values above both surrounding baseline maxima. A speed benefit requires all three PostgreSQL quiet elapsed values below both surrounding PostgreSQL baseline minima; both backends must have no primary quiet regression. Keep matched ordinals for reporting only. Disjoint baseline spreads indicate drift. Allow at most one extra surrounding-baseline batch set for overlap/ambiguous controls. Proven redundant-sweep reduction may justify neutral B. Neutral C1 requires all three PostgreSQL trace receipt-only-turn counts below both surrounding PostgreSQL baseline minima, with receipt_turns_censored == 0 in every compared sample (otherwise unavailable) and no primary quiet regression on either backend. C2 requires the PostgreSQL elapsed benefit. Safety/control/custody failures reject regardless of timing. Conservative diagnostic decisions, not significance claims or throughput assertions; trace overhead does not cancel automatically.",
        },
    )


def _source_modules() -> dict[str, dict[str, str]]:
    return {
        relative: {
            "path": str(Path(loaded).resolve()),
            "sha256": hashlib.sha256(Path(loaded).read_bytes()).hexdigest(),
        }
        for relative, loaded in {
            "weft/core/manager.py": manager_module.__file__,
            "tests/helpers/family_workload.py": family_workload_module.__file__,
            "tests/system/test_manager_dispatch_costs.py": __file__,
        }.items()
    }


def dispatch_source_probe() -> dict[str, Any]:
    """Executed by a real spawned function worker under this pytest invocation."""
    identity = get_runtime_identity()
    assert identity is not None
    return {
        "modules": _source_modules(),
        "executable": sys.executable,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "tid": identity.tid,
        "backend": identity.backend_name,
    }


def _source_preflight(root: Path) -> dict[str, Any]:
    """One disposable real pipeline, completed and closed before sample warmup."""
    expected = _source_modules()
    for relative, entry in expected.items():
        assert entry["path"] == str(root / relative)
    with WeftTestHarness() as harness:
        context = harness.context
        assert context.config["AUTOSTART_TASKS"] is True
        assert context.config["ADMISSION_MAX_CONNECTIONS"] == 0
        harness.ensure_foreground_manager()
        with WeftClient(context=context) as client:
            task = client.submit(
                {
                    "name": "dispatch-source-preflight",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.system.test_manager_dispatch_costs:dispatch_source_probe",
                    },
                },
            )
            harness.register_tid(task.tid)
            result = task.result(timeout=60)
            assert result.status == "completed", result.error
            observed = result.value
            assert isinstance(observed, dict)
            assert observed["modules"] == expected, observed
            assert observed["executable"] == sys.executable
            assert observed["pid"] != os.getpid() and observed["ppid"] != os.getpid()
            assert (
                observed["tid"] == task.tid
                and observed["backend"] == context.backend_name
            )
    return {
        "checkout": str(root),
        "executable": sys.executable,
        "backend": context.backend_name,
        "expected": expected,
        "worker": observed,
        "scope": "actual pytest Manager -> Consumer -> function-worker, before warmup",
    }


class _Observe:
    """Forward actual calls; record existing spans and narrow missing counters.

    Never ask the scheduler, admission controller, or broker for extra evidence.
    State snapshots run on the owning reactor; only ready-receipt inspection
    takes the local Queue mutex, copies envelopes, and immediately releases it.
    """

    def __init__(self, parent_tids: set[str], control_id: str) -> None:
        self.parent_tids = parent_tids
        self.control_id = control_id
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.local = threading.local()
        self.first_receipt = threading.Event()
        self.driver_ready = threading.Event()
        self.control_seen = threading.Event()
        self.cancel = threading.Event()
        self.manager_tid: str | None = None
        self.next_span = 0

    def record(self, event: str, **fields: Any) -> None:
        with self.lock:
            self.rows.append(
                {
                    "event": event,
                    "at_ns": time.time_ns(),
                    "mono_ns": time.perf_counter_ns(),
                    **fields,
                }
            )

    def state(self, manager: Any) -> dict[str, Any]:
        startup = len(manager._startup_feedback)
        limit = (
            MANAGER_STARTUP_FAST_PATH_WINDOW
            if active_test_backend() == "postgres"
            else 1
        )
        return {
            "manager_tid": manager.tid,
            "turn": manager._loop_iteration,
            "active_launches": len(manager._active_child_launches),
            "startup_members": startup,
            "blocked_startup": bool(startup)
            and (manager._startup_blocked or startup >= limit),
            "blocked_pressure_lanes": sorted(manager._admission_blocked_lanes),
            "blocked_unknown_evidence": manager._admission_evidence_unavailable,
            "blocked_custody": bool(manager._scheduler_undurable),
            "draining": manager._draining,
            "should_stop": manager.should_stop,
            "classification_pending": manager._family_classification_pending,
            "indexed_pending": len(manager._family_scheduler._pending),
        }

    def boundary(self, manager: Any, boundary: str) -> None:
        queue = manager._worker_result_queue
        with queue.mutex:
            ready = tuple(queue.queue)
        receipt_tids = [
            item.value.request_id
            for item in ready
            if isinstance(item.value, ServiceWorkerEvent)
            and item.value.name == MANAGER_CHILD_LAUNCH_WORKER_LANE
            and item.value.kind in {"result", "error"}
        ]
        self.record(
            "delivery_boundary",
            boundary=boundary,
            ready_receipt_tids=receipt_tids,
            ready_receipt_kinds=[
                item.value.kind
                for item in ready
                if isinstance(item.value, ServiceWorkerEvent)
                and item.value.name == MANAGER_CHILD_LAUNCH_WORKER_LANE
                and item.value.kind in {"result", "error"}
            ],
            **self.state(manager),
        )

    def install(
        self, patch: pytest.MonkeyPatch, mode: Literal["quiet", "trace"]
    ) -> None:
        original_finish = manager_module.Manager._finish_graceful_shutdown

        def finish(manager: Any) -> None:
            original_finish(manager)
            self.record(
                "stop_drain_finish",
                reason=manager._drain_reason,
                status=manager.taskspec.state.status,
                manager_tid=manager.tid,
                should_stop=manager.should_stop,
            )

        patch.setattr(manager_module.Manager, "_finish_graceful_shutdown", finish)
        if mode == "quiet":
            self._observe_quiet_boundaries(patch)
            return
        original_emit = manager_module.emit_serve_log_record

        def emit(record: dict[str, Any]) -> None:
            original_emit(record)
            stack = getattr(self.local, "spans", [])
            fields: dict[str, Any] = {"record": dict(record)}
            # process_launch is measured on the worker, emitted later by the
            # reactor. It must not be subtracted from a containing reactor span.
            if (
                record.get("event") == "manager_phase_timing"
                and record.get("phase") != "process_launch"
                and stack
            ):
                fields.update(
                    span_id=stack[-1],
                    parent_span_id=stack[-2] if len(stack) > 1 else None,
                )
            self.record("trace", **fields)

        patch.setattr(manager_module, "emit_serve_log_record", emit)
        original_timed = manager_module.Manager._timed_manager_call

        def timed(
            manager: Any,
            phase: str,
            operation: Callable[[], Any],
            *,
            child_tid: str | None = None,
        ) -> Any:
            delivery = getattr(self.local, "maintenance_delivery", None)
            if phase == "admission" and delivery is not None:
                delivery["admission_entered"] = True
            if not hasattr(self.local, "spans"):
                self.local.spans = []
            self.next_span += 1
            self.local.spans.append(self.next_span)
            try:
                return original_timed(manager, phase, operation, child_tid=child_tid)
            finally:
                self.local.spans.pop()

        patch.setattr(manager_module.Manager, "_timed_manager_call", timed)
        names = (
            "_process_manager_reactor_turn",
            "_handle_work_message",
            "_cleanup_children",
            "_seed_child_inbox",
            "_launch_child_task",
            "_handle_child_launch_result",
            "_register_tid_state",
            "_record_admission_decision",
            "_read_postgres_connection_usage",
            "_read_postgres_connection_stats",
            "_handle_control_command",
            "_drain_queue",
            "_drain_spawn_requests_from_queue",
        )
        for name in names:
            original = getattr(manager_module.Manager, name)
            patch.setattr(manager_module.Manager, name, self._forward(name, original))
        self._observe_maintenance(patch)
        self._observe_ping(patch)

    def _first_parent_receipt(self, tid: str) -> None:
        if tid in self.parent_tids and not self.first_receipt.is_set():
            self.record("first_parent_receipt", tid=tid)
            self.first_receipt.set()

    def _control_boundary(
        self,
        manager: Any,
        request: Any,
        event: str,
        *,
        succeeded: bool = True,
        **fields: Any,
    ) -> None:
        if request.request_id != self.control_id:
            return
        self.record(
            event,
            command=request.command,
            request_id=self.control_id,
            **{
                "draining": manager._draining,
                "indexed_pending": len(manager._family_scheduler._pending),
                "succeeded": succeeded,
                **fields,
            },
        )
        if event == "control_return" and succeeded:
            self.control_seen.set()

    def _observe_quiet_boundaries(self, patch: pytest.MonkeyPatch) -> None:
        """Two causal hooks plus the shared STOP-finish hook, no detailed observers."""
        original_receipt = manager_module.Manager._handle_child_launch_result
        original_control = manager_module.Manager._handle_control_command

        def receipt(manager: Any, result: Any) -> None:
            original_receipt(manager, result)
            self._first_parent_receipt(str(result.request.child_spec.tid))

        def control(manager: Any, request: Any, context: Any) -> bool:
            self._control_boundary(manager, request, "control_enter")
            result = original_control(manager, request, context)
            self._control_boundary(manager, request, "control_return")
            return result

        patch.setattr(manager_module.Manager, "_handle_child_launch_result", receipt)
        patch.setattr(manager_module.Manager, "_handle_control_command", control)

    def _observe_maintenance(self, patch: pytest.MonkeyPatch) -> None:
        """Observe only actual B gate calls; A supplies no artificial predicate.

        Retain returned values; classify them after the measured interval.
        Never repeat the deadline predicate on the live reactor. A false return
        is attributable only before admission/handler entry. No scope flag is mirrored.
        """
        original_wait = manager_module._ManagerChildSentinelAdapter.wait
        original_timeouts = manager_module.Manager._manager_child_timeouts
        original_delivery = manager_module.Manager._process_queue_message

        def wait(adapter: Any, timeout: float) -> bool:
            result = original_wait(adapter, timeout)
            frame = getattr(self.local, "maintenance_delivery", None)
            if (
                frame is not None
                and adapter is frame["manager"]._child_sentinel_adapter
                and timeout == 0
            ):
                frame["probes"].append({"kind": "sentinel_ready", "returned": result})
            return result

        def timeouts(manager: Any, *, now_ns: int) -> list[float]:
            result = original_timeouts(manager, now_ns=now_ns)
            frame = getattr(self.local, "maintenance_delivery", None)
            if frame is not None and manager is frame["manager"]:
                frame["probes"].append(
                    {
                        "kind": "child_deadline_due",
                        "returned": tuple(result),
                    }
                )
            return result

        def deliver(
            manager: Any, queue_name: str, inactive_candidates: set[str]
        ) -> bool:
            if queue_name not in (
                manager._queue_names.get("inbox"),
                manager._queue_names.get("internal_inbox"),
            ):
                return original_delivery(manager, queue_name, inactive_candidates)
            previous = getattr(self.local, "maintenance_delivery", None)
            frame: dict[str, Any] = {
                "manager": manager,
                "probes": [],
                "admission_entered": False,
                "handler_entered": False,
            }
            self.local.maintenance_delivery = frame
            result: bool | None = None
            try:
                result = original_delivery(manager, queue_name, inactive_candidates)
                return result
            finally:
                self.local.maintenance_delivery = previous
                if frame["probes"]:
                    self.record(
                        "maintenance_delivery_return",
                        queue_name=queue_name,
                        probes=frame["probes"],
                        processed=result,
                        admission_entered=frame["admission_entered"],
                        handler_entered=frame["handler_entered"],
                        manager_tid=manager.tid,
                        turn=manager._loop_iteration,
                    )

        patch.setattr(manager_module._ManagerChildSentinelAdapter, "wait", wait)
        patch.setattr(manager_module.Manager, "_manager_child_timeouts", timeouts)
        patch.setattr(manager_module.Manager, "_process_queue_message", deliver)

    def _observe_ping(self, patch: pytest.MonkeyPatch) -> None:
        """Keep the shared keyed probe's watcher, matching, and retirement owner."""
        original_write = control_probe_module._write_probe_request
        original_read = control_probe_module._read_matching_pong

        def write(
            reply_queue: Any, *, broker: Any | None, ctrl_in_name: str, message: str
        ) -> None:
            ours = json.loads(message).get("request_id") == self.control_id
            if ours:
                self.record(
                    "control_write_enter", command="PING", request_id=self.control_id
                )
            original_write(
                reply_queue, broker=broker, ctrl_in_name=ctrl_in_name, message=message
            )
            if ours:
                self.record(
                    "control_write_return", command="PING", request_id=self.control_id
                )

        def read(reply_queue: Any, *, tid: str, request_id: str) -> Any:
            result = original_read(reply_queue, tid=tid, request_id=request_id)
            if request_id == self.control_id and result is not None:
                self.record("pong_matched", request_id=request_id)
            return result

        patch.setattr(control_probe_module, "_write_probe_request", write)
        patch.setattr(control_probe_module, "_read_matching_pong", read)

    def _forward(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def observed(manager: Any, *args: Any, **kwargs: Any) -> Any:
            if name == "_handle_work_message":
                self.local.handler_depth = getattr(self.local, "handler_depth", 0) + 1
                frame = getattr(self.local, "maintenance_delivery", None)
                if frame is not None:
                    frame["handler_entered"] = True
            delivery = name in {"_drain_queue", "_drain_spawn_requests_from_queue"}
            if delivery:
                self.local.delivery_depth = getattr(self.local, "delivery_depth", 0) + 1
            self._before(name, manager, args, kwargs)
            result: Any = None
            succeeded = False
            try:
                result = original(manager, *args, **kwargs)
                succeeded = True
                return result
            finally:
                if name == "_handle_work_message":
                    self.local.handler_depth -= 1
                if delivery:
                    self.local.delivery_depth -= 1
                    if succeeded and self.local.delivery_depth == 0:
                        self.boundary(manager, name)
                self._after(name, manager, args, kwargs, result, succeeded)

        return observed

    def _before(
        self, name: str, manager: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        if name == "_process_manager_reactor_turn":
            self.manager_tid = manager.tid
            self.record("turn_enter", **self.state(manager))
        elif name == "_cleanup_children":
            self.record(
                "sweep",
                inside_handler=bool(getattr(self.local, "handler_depth", 0)),
                manager_tid=manager.tid,
                turn=manager._loop_iteration,
            )
        elif name == "_launch_child_task":
            # Count entry at the launch attempt boundary, including later seed,
            # class resolution, checkpoint or handoff failure. Spec validation
            # already passed. Built-in services count.
            self.record(
                "launch_attempt_enter",
                tid=args[0].tid,
                task_name=args[0].name,
                source_queue=kwargs.get("source_queue"),
                reserved_queue=kwargs.get("reserved_queue"),
                message_timestamp=kwargs.get("message_timestamp"),
                workload_parent=str(args[0].tid) in self.parent_tids,
                **self.state(manager),
            )
        elif name == "_seed_child_inbox":
            self.record("seed_enter", tid=args[0].tid, **self.state(manager))
        elif (
            name == "_handle_control_command" and args[0].request_id == self.control_id
        ):
            self._control_boundary(
                manager, args[0], "control_enter", **self.state(manager)
            )

    def _after(
        self,
        name: str,
        manager: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
        succeeded: bool,
    ) -> None:
        if name == "_process_manager_reactor_turn":
            self.record("turn_exit", succeeded=succeeded, **self.state(manager))
        elif name == "_handle_child_launch_result":
            tid = str(args[0].request.child_spec.tid)
            self.record(
                "receipt_commit_return",
                tid=tid,
                succeeded=succeeded,
                launch_error=args[0].error is not None,
                **self.state(manager),
            )
            if succeeded:
                self._first_parent_receipt(tid)
        elif name == "_launch_child_task":
            self.record(
                "worker_handoff_return",
                tid=args[0].tid,
                handed_off=result is True,
                succeeded=succeeded,
                **self.state(manager),
            )
        elif name == "_register_tid_state":
            self.record(
                "checkpoint_return", written=result is True, succeeded=succeeded
            )
        elif name == "_record_admission_decision":
            decision = kwargs["decision"]
            self.record(
                "admission_decision",
                lane=kwargs["lane"],
                backend=kwargs["backend"],
                capacity=decision.capacity,
                blocked_lanes=sorted(decision.blocked_lanes),
                wait_phase=decision.wait_phase,
                succeeded=succeeded,
            )
        elif name in {
            "_read_postgres_connection_usage",
            "_read_postgres_connection_stats",
        }:
            self.record(
                name.removeprefix("_read_"), value=dict(result) if succeeded else None
            )
        elif (
            name == "_handle_control_command" and args[0].request_id == self.control_id
        ):
            self._control_boundary(
                manager,
                args[0],
                "control_return",
                succeeded=succeeded,
                **self.state(manager),
            )


def _control(
    context: WeftContext,
    observer: _Observe,
    command: str,
    failures: list[BaseException],
) -> None:
    """Send real control after a real parent receipt; never block the reactor."""
    try:
        assert observer.first_receipt.wait(60), "no parent launch receipt"
        assert observer.driver_ready.wait(60), "driver did not become ready"
        if observer.cancel.is_set():
            return
        tid = observer.manager_tid
        assert tid is not None
        if command == "PING":
            observer.record("ping_call_enter", request_id=observer.control_id)
            probe = control_probe_module.send_keyed_ping_probe(
                context,
                tid=tid,
                ctrl_in_name=f"T{tid}.ctrl_in",
                timeout=60,
                request_id=observer.control_id,
            )
            observer.record(
                "ping_call_return",
                request_id=observer.control_id,
                matched=probe.matched is not None,
                timed_out=probe.timed_out,
                error=probe.error,
            )
            assert probe.matched is not None, probe
            assert observer.control_seen.wait(60), (
                "PING matched without target handler observation"
            )
            return
        message = encode_control_message(command, request_id=observer.control_id)
        with context.queue(f"T{tid}.ctrl_in", persistent=True) as queue:
            observer.record(
                "control_write_enter", command=command, request_id=observer.control_id
            )
            queue.write(message)
            observer.record(
                "control_write_return", command=command, request_id=observer.control_id
            )
        assert observer.control_seen.wait(60), f"{command} was not handled"
    except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-379] exception
        failures.append(exc)


def _distribution(values: list[float]) -> dict[str, Any]:
    """Small-sample diagnostic, with an explicit nearest-rank p95 convention."""
    ordered = sorted(values)
    return {
        "count": len(values),
        "raw_seconds": values,
        "mean_seconds": fmean(ordered) if ordered else None,
        "median_seconds": median(ordered) if ordered else None,
        "p95_seconds": ordered[ceil(0.95 * len(ordered)) - 1] if ordered else None,
        "min_seconds": ordered[0] if ordered else None,
        "max_seconds": ordered[-1] if ordered else None,
    }


def _latencies(
    rows: list[dict[str, Any]], family: list[dict[str, Any]]
) -> dict[str, Any]:
    submitted = {
        row["tid"]: row["submitted_at_ns"]
        for row in rows
        if row["event"] == "parent_submitted"
    }
    starts = {
        str(row["tid"]): int(row["broker_timestamp"])
        for row in family
        if row["event"] in {"enrichment_started", "leaf_started"}
    }
    attempts = sorted(
        (row for row in rows if row["event"] == "launch_attempt_enter"),
        key=lambda row: row["at_ns"],
    )
    following = []
    for completion in family:
        if completion["event"] != "enrichment_completed":
            continue
        stamp = int(completion["broker_timestamp"])
        next_attempt = next(
            (
                row
                for row in attempts
                if row["at_ns"] > stamp and str(row["tid"]) in starts
            ),
            None,
        )
        if next_attempt is not None:
            following.append(
                {
                    "completed_tid": completion["tid"],
                    "next_tid": next_attempt["tid"],
                    "seconds": (starts[str(next_attempt["tid"])] - stamp) / 1e9,
                }
            )
    enqueues = [
        {"tid": tid, "seconds": (starts[tid] - begin) / 1e9}
        for tid, begin in submitted.items()
        if tid in starts
    ]
    parent_starts = sorted(starts[tid] for tid in submitted if tid in starts)
    return {
        "parent_start_spacing_secondary": _distribution(
            [(later - earlier) / 1e9 for earlier, later in pairwise(parent_starts)]
        ),
        "parent_enqueue_begin_to_start_seconds": enqueues,
        "completion_to_next_actually_authorized_start": following,
        "completion_observations": sum(
            row["event"] == "enrichment_completed" for row in family
        ),
        "completion_samples_without_subsequent_user_authorization": sum(
            row["event"] == "enrichment_completed" for row in family
        )
        - len(following),
        "latency_summary": {
            "enqueue_to_start": _distribution([row["seconds"] for row in enqueues]),
            "completion_to_next_authorization": _distribution(
                [row["seconds"] for row in following]
            ),
        },
        "latency_limit": "broker HLC timestamps approximate wall-clock event time; the completion-to-next-authorization proxy measures the start associated with the next actually authorized user launch after completion, with eligibility proven at authorization only, not continuously since completion; no later user launch yields no sample, not zero latency",
    }


def _closure_summary(
    rows: list[dict[str, Any]], controls: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Only the measured STOP request can anchor STOP completion timings."""
    sent = controls.get("control_write_enter", {})
    if (
        sent.get("command") != "STOP"
        or controls.get("control_enter", {}).get("command") != "STOP"
    ):
        return {}
    events = {
        row["event"]: row
        for row in rows
        if row["event"] == "stop_driver_retired"
        or (
            row["event"] == "stop_drain_finish"
            and row["reason"] == "STOP command received"
        )
    }
    result: dict[str, Any] = {
        "interpretation": "STOP handler return, drain finish, and driver retirement are separate; harness cleanup timing is not measured"
    }
    if "stop_drain_finish" in events:
        result["stop_write_begin_to_drain_finish_seconds"] = (
            events["stop_drain_finish"]["mono_ns"] - sent["mono_ns"]
        ) / 1e9
    if "stop_driver_retired" in events:
        result["stop_write_begin_to_observed_driver_retirement_seconds"] = (
            events["stop_driver_retired"]["mono_ns"] - sent["mono_ns"]
        ) / 1e9
        result["driver_retired_before_harness_cleanup"] = events["stop_driver_retired"][
            "closed"
        ]
    return result


def _maintenance_summary(
    rows: list[dict[str, Any]], attempt_count: int
) -> dict[str, Any]:
    """Classify actual gate results offline; never add predicate work to B."""
    observations = [
        row for row in rows if row["event"] == "maintenance_delivery_return"
    ]
    yields = []
    reasons: Counter[str] = Counter()
    unattributed = 0
    for row in observations:
        positive = [
            probe["kind"]
            for probe in row["probes"]
            if (
                probe["returned"] is True
                if probe["kind"] == "sentinel_ready"
                else any(value <= 0 for value in probe["returned"])
            )
        ]
        yielded = (
            row["processed"] is False
            and bool(positive)
            and not row["admission_entered"]
            and not row["handler_entered"]
        )
        if yielded:
            yields.append(row)
            reasons.update(positive)
        else:
            unattributed += bool(positive)
    return {
        "maintenance_gate_calls_observed": len(observations),
        "maintenance_observability": "observed"
        if observations
        else "unobserved; B/successor analysis must reject maintenance metrics until its gate is observed",
        "maintenance_predicate_calls": dict(
            Counter(probe["kind"] for row in observations for probe in row["probes"])
        ),
        "maintenance_yields": len(yields),
        "maintenance_yield_turns": len(
            {(row["manager_tid"], row["turn"]) for row in yields}
        ),
        "maintenance_yields_per_launch_attempt": len(yields) / attempt_count
        if attempt_count
        else None,
        "maintenance_yield_reasons": dict(reasons),
        "maintenance_unattributed_positive_observations": unattributed,
        "maintenance_qualification": "actual in-delivery calls only; false return precedes admission and handler; an unobserved gate is not proof of absence",
    }


def _mark_quiet_unavailable(
    summary: dict[str, Any], mode: Literal["quiet", "trace"]
) -> None:
    """Suppress absent internal evidence rather than reporting invented zeroes."""
    if mode != "quiet":
        return
    # Missing instrumentation is never evidence of zero activity or a passed fence.
    unavailable = (
        "calls",
        "worker_handoffs_per_turn",
        "all_launch_attempts_per_turn",
        "all_launch_attempts",
        "receipt_only_turns",
        "receipt_turns_completed",
        "receipt_turns_censored",
        "seed_calls",
        "handler_sweeps",
        "handler_sweeps_per_launch_attempt",
        "sweeps_per_launch_attempt",
        "phase_times",
        "successful_checkpoint_publications",
        "observed_global_pressure_max",
        "max_active_launches",
        "max_startup_members",
        "sample_held_state_seconds_overlapping",
        "ready_receipt_boundary_count",
        "completion_to_next_actually_authorized_start",
        "completion_samples_without_subsequent_user_authorization",
    )
    for name in (
        *unavailable,
        *(name for name in summary if name.startswith("maintenance_")),
    ):
        summary[name] = None
    summary["latency_summary"]["completion_to_next_authorization"] = dict.fromkeys(
        _distribution([])
    )
    summary["unavailable_reason"] = (
        "quiet has only three causal hooks; internal activity and authorization fence are unobserved, not zero or verified"
    )
    summary["control_latency"]["ping_write_begin_to_matched_pong_seconds"] = None


def _summary(
    rows: list[dict[str, Any]],
    family: list[dict[str, Any]],
    started: int,
    ended: int,
    mode: Literal["quiet", "trace"],
) -> dict[str, Any]:
    active = [row for row in rows if started <= row["mono_ns"] <= ended]
    attempts = [row for row in active if row["event"] == "launch_attempt_enter"]
    completed_turns = {
        (row["manager_tid"], row["turn"])
        for row in active
        if row["event"] == "turn_exit" and row["succeeded"]
    }
    receipt_turns = {
        (row["manager_tid"], row["turn"])
        for row in active
        if row["event"] == "receipt_commit_return" and row["succeeded"]
    }
    authorization_turns = {(row["manager_tid"], row["turn"]) for row in attempts}
    phases = [
        row
        for row in active
        if row["event"] == "trace" and row["record"]["event"] == "manager_phase_timing"
    ]
    direct_children: dict[int, float] = defaultdict(float)
    for row in phases:
        if row.get("parent_span_id") is not None:
            direct_children[row["parent_span_id"]] += row["record"]["elapsed_seconds"]
    phase_totals: dict[str, dict[str, Any]] = {}
    for row in phases:
        record = row["record"]
        total = phase_totals.setdefault(
            record["phase"],
            {"count": 0, "inclusive_seconds": 0.0, "exclusive_seconds": 0.0},
        )
        total["count"] += 1
        total["inclusive_seconds"] += record["elapsed_seconds"]
        span_id = row.get("span_id")
        child_seconds = (
            direct_children.get(span_id, 0.0) if span_id is not None else 0.0
        )
        total["exclusive_seconds"] += record["elapsed_seconds"] - child_seconds
    states = [row for row in active if "active_launches" in row]
    held: dict[str, float] = defaultdict(float)
    for previous, following in pairwise(states):
        for field in (
            "active_launches",
            "blocked_startup",
            "blocked_pressure_lanes",
            "blocked_unknown_evidence",
            "blocked_custody",
        ):
            if previous[field]:
                held[field] += (following["mono_ns"] - previous["mono_ns"]) / 1e9
    controls = {
        row["event"]: row for row in active if row["event"].startswith("control_")
    }
    control_latency: dict[str, Any] = {}
    if {
        "control_write_enter",
        "control_write_return",
        "control_enter",
        "control_return",
    } <= controls.keys():
        arrival = controls["control_enter"]["mono_ns"]
        control_latency = {
            "command": controls["control_enter"]["command"],
            "write_begin_to_handler_seconds": (
                arrival - controls["control_write_enter"]["mono_ns"]
            )
            / 1e9,
            "write_begin_to_handler_return_seconds": (
                controls["control_return"]["mono_ns"]
                - controls["control_write_enter"]["mono_ns"]
            )
            / 1e9,
            "write_return_to_handler_lower_bound_seconds": max(
                0, arrival - controls["control_write_return"]["mono_ns"]
            )
            / 1e9,
        }
    pong = next((row for row in active if row["event"] == "pong_matched"), None)
    if pong is not None:
        control_latency["ping_write_begin_to_matched_pong_seconds"] = (
            pong["mono_ns"] - controls["control_write_enter"]["mono_ns"]
        ) / 1e9
    ping_calls = {
        row["event"]: row
        for row in active
        if row["event"] in {"ping_call_enter", "ping_call_return"}
    }
    if {"ping_call_enter", "ping_call_return"} <= ping_calls.keys():
        control_latency["ping_call_to_return_seconds"] = (
            ping_calls["ping_call_return"]["mono_ns"]
            - ping_calls["ping_call_enter"]["mono_ns"]
        ) / 1e9
    completion = next(
        (row for row in active if row["event"] == "workload_results_complete"), None
    )
    elapsed_end = completion["mono_ns"] if completion is not None else ended
    summary = {
        "measurement_mode": mode,
        "elapsed_seconds": (elapsed_end - started) / 1e9,
        "observation_window_seconds": (ended - started) / 1e9,
        "elapsed_scope": "PING workload ends after all validated user results; control join and cleanup are reported separately. STOP trial is a control observation, not useful-work throughput.",
        "measurement_window_mono_ns": {"start": started, "end": ended},
        "closure": _closure_summary(rows, controls),
        "useful_parent_completions": sum(
            row["event"] == "enrichment_completed" for row in family
        ),
        "useful_leaf_starts": sum(row["event"] == "leaf_started" for row in family),
        "calls": dict(Counter(row["event"] for row in active)),
        "worker_handoffs_per_turn": dict(
            Counter(
                str(row["turn"])
                for row in active
                if row["event"] == "worker_handoff_return" and row["handed_off"]
            )
        ),
        "all_launch_attempts_per_turn": dict(
            Counter(str(row["turn"]) for row in attempts)
        ),
        "all_launch_attempts": len(attempts),
        "receipt_only_turns": len(
            (receipt_turns & completed_turns) - authorization_turns
        ),
        "receipt_turns_completed": len(receipt_turns & completed_turns),
        "receipt_turns_censored": len(receipt_turns - completed_turns),
        "receipt_only_turns_scope": "completed observed turns containing a successfully handled launch receipt but no new authorization attempt; other turn work may occur; receipts without an observed successful turn exit inside the measurement window are censored, not classified as receipt-only",
        **_maintenance_summary(active, len(attempts)),
        "launch_attempt_scope": "all _launch_child_task entries, including built-in services and attempts that later fail; fixed user workload is separately verified",
        "seed_calls": sum(row["event"] == "seed_enter" for row in active),
        "handler_sweeps": sum(
            row["event"] == "sweep" and row["inside_handler"] for row in active
        ),
        "handler_sweeps_per_launch_attempt": sum(
            row["event"] == "sweep" and row["inside_handler"] for row in active
        )
        / len(attempts)
        if attempts
        else None,
        "sweeps_per_launch_attempt": sum(row["event"] == "sweep" for row in active)
        / len(attempts)
        if attempts
        else None,
        "phase_times": phase_totals,
        "successful_checkpoint_publications": sum(
            row["event"] == "checkpoint_return" and row["written"] for row in active
        ),
        "observed_global_pressure_max": max(
            (
                row["value"]["total"]
                for row in active
                if row["event"] == "postgres_connection_usage"
                and row["value"] is not None
            ),
            default=None,
        ),
        "phase_accounting": "exclusive subtracts direct measured reactor spans only; worker process_launch is separate; uninstrumented gaps remain",
        "max_active_launches": max(
            (row["active_launches"] for row in states), default=0
        ),
        "max_startup_members": max(
            (row["startup_members"] for row in states), default=0
        ),
        "sample_held_state_seconds_overlapping": held,
        "state_duration_limit": "last-observation-held diagnostic estimate, not continuous eligibility proof; reasons overlap",
        "ready_receipt_boundary_count": sum(
            row["event"] == "delivery_boundary" and bool(row["ready_receipt_tids"])
            for row in active
        ),
        "c2_limit": "ready receipt is an opportunity, not authorization; no extra drain, capacity query, candidate selection, or next_wait_timeout call",
        "control_latency": control_latency,
        **_latencies(rows, family),
    }

    summary["post_stop_launch_attempt_check"] = (
        "verified"
        if any(row["event"] == "post_stop_launch_fence_verified" for row in rows)
        else None
    )
    _mark_quiet_unavailable(summary, mode)
    return summary


def _stop_custody(
    context: WeftContext, manager: Any, accepted: dict[str, str]
) -> list[dict[str, Any]]:
    """Inspect exact requests after the stopped driver has retired, before disposal.

    This is outside the measured interval. Reuse the strict read-only terminal
    selector; do not acknowledge, move, settle, or infer death from missing data.
    """
    evidence = []
    with (
        context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source,
        source.get_connection() as broker,
    ):
        for tid, body in accepted.items():
            source_body = broker.peek_one(
                source.name, exact_timestamp=int(tid), with_timestamps=False
            )
            reserved_body = broker.peek_one(
                manager._queue_names["reserved"],
                exact_timestamp=int(tid),
                with_timestamps=False,
            )
            terminal = peek_terminal_ctrl_out_evidence(
                context, tid=tid, ctrl_out_name=f"T{tid}.ctrl_out", broker=broker
            )
            assert source_body is None or reserved_body is None, (
                f"duplicate request custody: {tid}"
            )
            assert source_body is None or source_body == body
            assert reserved_body is None or reserved_body == body
            assert (
                source_body is not None
                or reserved_body is not None
                or terminal is not None
            ), f"request disappeared without terminal proof: {tid}"
            assert source_body is None or terminal is None, (
                f"terminal task was returned to source: {tid}"
            )
            evidence.append(
                {
                    "tid": tid,
                    "source_queue": source.name,
                    "reserved_queue": manager._queue_names["reserved"],
                    "in_source": source_body is not None,
                    "in_reserved": reserved_body is not None,
                    "terminal_status": terminal.status
                    if terminal is not None
                    else None,
                    "terminal_source": terminal.source
                    if terminal is not None
                    else None,
                    "terminal_observed_at": terminal.observed_at
                    if terminal is not None
                    else None,
                }
            )
    assert any(row["in_source"] for row in evidence), (
        "STOP trial retained no source backlog"
    )
    return evidence


def _retire_stopped_driver(
    harness: WeftTestHarness,
    observer: _Observe,
    accepted: dict[str, str],
    destination: Path,
) -> None:
    """Observe existing STOP completion; never ask the harness to stop it here."""
    owners = [
        entry
        for entry in harness._inline_managers
        if entry[0].tid == observer.manager_tid
    ]
    assert len(owners) == 1
    manager, driver, _stop_event = owners[0]
    driver.join(60)
    observer.record(
        "stop_driver_retired",
        closed=not driver.is_alive(),
        status=manager.taskspec.state.status,
    )
    assert not driver.is_alive(), "STOP did not retire its Manager driver"
    assert manager.taskspec.state.status in TERMINAL_TASK_STATUSES
    assert any(
        row["event"] == "stop_drain_finish"
        and row["reason"] == "STOP command received"
        and row["should_stop"]
        for row in observer.rows
    ), "driver retired without completing its STOP drain"
    _json(
        destination / "stop-custody.json",
        _stop_custody(harness.context, manager, accepted),
    )


def _native_capacity_after_window(
    harness: WeftTestHarness, observer: _Observe, mode: Literal["quiet", "trace"]
) -> None:
    """Inspect installed native capacity after timing; add no backend call."""
    if harness.context.backend_name == "postgres":
        # Read the actual ceiling installed by normal admission calls,
        # outside the timed window, with no extra backend observation.
        ceilings = [
            entry[0]._admission_controller._max_connections
            for entry in harness._inline_managers
        ]
        assert ceilings and all(value == 300 for value in ceilings)
        if mode == "trace":
            stats = [
                row["value"]
                for row in observer.rows
                if row["event"] == "postgres_connection_stats"
                and row["value"] is not None
            ]
            assert stats and all(row["max_connections"] == 300 for row in stats)
        observer.record("native_capacity_after_window", ceilings=ceilings)


@contextmanager
def _sample_harness(observer: _Observe) -> Iterator[WeftTestHarness]:
    """Keep the body failure primary even if harness exit raises another error."""
    body_error: BaseException | None = None
    try:
        with WeftTestHarness() as harness:
            try:
                yield harness
            except BaseException as exc:
                body_error = exc
                raise
    except BaseException as exc:
        primary = body_error if body_error is not None else exc
        if exc is not primary:
            observer.record("harness_cleanup_error", error=repr(exc))
            primary.add_note(f"Harness cleanup also failed: {exc!r}")
        observer.record(
            "sample_failure",
            error=repr(primary),
            notes=list(getattr(primary, "__notes__", [])),
        )
        if exc is not primary:
            raise primary from None
        raise


def _retire_control(observer: _Observe, thread: threading.Thread) -> None:
    """Release and join the owned helper without hiding an active sample failure."""
    primary = sys.exception()
    observer.cancel.set()
    observer.first_receipt.set()
    observer.driver_ready.set()
    observer.control_seen.set()
    try:
        if thread.is_alive():
            thread.join(65)
        if thread.is_alive():
            raise RuntimeError("control thread outlived harness")
    except Exception as exc:
        observer.record("control_cleanup_error", error=repr(exc))
        if primary is None:
            raise
        primary.add_note(f"Control cleanup also failed: {exc!r}")


def _persist_sample(
    destination: Path,
    observer: _Observe,
    family: list[dict[str, Any]],
    started: int,
    ended: int,
    mode: Literal["quiet", "trace"],
    primary: BaseException | None,
) -> dict[str, Any]:
    """Attempt every artifact; preserve the primary and fail sole diagnostics errors."""
    errors: list[Exception] = []
    try:
        summary = _summary(observer.rows, family, started, ended, mode)
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-380] exception
        errors.append(exc)
        observer.record("summary_error", error=repr(exc))
        summary = {"measurement_mode": mode, "summary_error": repr(exc)}
    writes: tuple[tuple[str, Callable[[], None]], ...] = (
        (
            "family.jsonl",
            functools.partial(_jsonl, destination / "family.jsonl", family),
        ),
        (
            "summary.json",
            functools.partial(_json, destination / "summary.json", summary),
        ),
        (
            "events.jsonl",
            functools.partial(_jsonl, destination / "events.jsonl", observer.rows),
        ),
    )
    for name, write in writes:
        try:
            write()
        except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-380] exception
            errors.append(exc)
            observer.record("artifact_write_error", artifact=name, error=repr(exc))
    if errors:
        if primary is None:
            raise ExceptionGroup("Dispatch sample diagnostics failed", errors)
        for error in errors:
            primary.add_note(f"Dispatch diagnostics also failed: {error!r}")
    return summary


def _sample(
    destination: Path, command: str, *, repetition: int, mode: Literal["quiet", "trace"]
) -> dict[str, Any]:
    destination.mkdir()
    _json(
        destination / "sample.json",
        {
            "source_manifest": "../manifest.json",
            "measurement_mode": mode,
            "created_at_ns": time.time_ns(),
            "repetition": repetition,
            "phase": "warmup" if repetition == 0 else "measured",
            "scenario": "throughput-with-ping"
            if command == "PING"
            else "stop-under-backlog",
            "fresh_manager_and_harness": True,
            "cache_control": "none; warmup is first scenario execution, not a forced cold-cache claim",
        },
    )
    family: list[dict[str, Any]] = []
    observer = _Observe(set(), uuid.uuid4().hex)
    failures: list[BaseException] = []
    thread: threading.Thread | None = None
    started = ended = time.perf_counter_ns()
    # The patch remains installed through harness cleanup, so no late callback
    # can outlive its observer. Cleanup events are outside the sample interval.
    try:
        with (
            pytest.MonkeyPatch.context() as patch,
            _sample_harness(observer) as harness,
        ):
            config = dict(harness.context.config)
            assert config["AUTOSTART_TASKS"] is True
            assert config["ADMISSION_MAX_CONNECTIONS"] == 0
            original_target = harness.context.broker_target
            if mode == "trace":
                harness._context = build_context(
                    spec_context=harness.root,
                    config=resolve_runtime_config(
                        {
                            **config,
                            "MANAGER_SERVE_LOG_ACTIVE": True,
                            "MANAGER_SERVE_LOG_LEVEL": "trace",
                        }
                    ),
                )
            else:
                assert config["MANAGER_SERVE_LOG_ACTIVE"] is False, (
                    "quiet mode requires default quiet logging"
                )
            context = harness.context
            assert context.broker_target == original_target
            _json(
                destination / "config.json",
                {
                    "backend": context.backend_name,
                    **{
                        key: value
                        for key, value in context.config.items()
                        if key.startswith("ADMISSION_")
                        or key
                        in {
                            "AUTOSTART_TASKS",
                            "MANAGER_SERVE_LOG_ACTIVE",
                            "MANAGER_SERVE_LOG_LEVEL",
                        }
                    },
                },
            )
            observer.install(patch, mode)
            tids: list[str] = []
            for index in range(12):
                submitted_at = time.time_ns()
                tid = str(
                    submit_spawn_request(
                        context.broker_target,
                        taskspec={
                            "name": f"dispatch-parent-{index}",
                            "spec": {
                                "type": "function",
                                "function_target": "tests.helpers.family_workload:family_enrichment",
                            },
                        },
                        work_payload={"index": index, "gated": command == "STOP"},
                        config=context.config,
                        inherited_weft_context=str(context.root),
                    )
                )
                observer.record(
                    "parent_submitted",
                    tid=tid,
                    index=index,
                    submitted_at_ns=submitted_at,
                )
                tids.append(tid)
                harness.register_tid(tid)
            # Exact original bytes are captured before the timed interval.
            with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source:
                accepted = {
                    str(stamp): body
                    for body, stamp in source.peek_generator(with_timestamps=True)
                    if str(stamp) in tids
                }
            assert set(accepted) == set(tids)
            observer.parent_tids.update(tids)
            thread = threading.Thread(
                target=_control,
                args=(context, observer, command, failures),
                name="dispatch-control",
            )
            started = time.perf_counter_ns()
            thread.start()
            try:
                manager_record = harness.ensure_foreground_manager()
                observer.manager_tid = str(manager_record["tid"])
                observer.driver_ready.set()
                if command == "PING":
                    with WeftClient(context=context) as client:
                        for index, tid in enumerate(tids):
                            result = client.task(tid).result(timeout=90)
                            assert result.status == "completed", result.error
                            assert isinstance(result.value, dict)
                            assert result.value["index"] == index
                            assert result.value["value"] == f"leaf:{index}"
                            harness.register_tid(result.value["child_tid"])
                    observer.record("workload_results_complete")
                else:
                    assert observer.control_seen.wait(60), "STOP not accepted"
                    stop = next(
                        row for row in observer.rows if row["event"] == "control_return"
                    )
                    assert stop["draining"]
                    assert stop["indexed_pending"] > 0, (
                        "STOP trial did not overlap pending dispatch"
                    )
                thread.join(65)
                assert not thread.is_alive(), "control thread did not finish"
                assert not failures, failures
                ended = time.perf_counter_ns()
                _native_capacity_after_window(harness, observer, mode)
                if command == "STOP":
                    _retire_stopped_driver(harness, observer, accepted, destination)
                with context.queue(FAMILY_EVENTS, persistent=True) as queue:
                    family = [
                        dict(json.loads(body), broker_timestamp=stamp)
                        for body, stamp in queue.peek_generator(with_timestamps=True)
                    ]
                if command == "PING":
                    assert (
                        sum(row["event"] == "enrichment_completed" for row in family)
                        == 12
                    )
                    assert sum(row["event"] == "leaf_started" for row in family) == 12
                elif mode == "trace":
                    assert not [
                        row
                        for row in observer.rows
                        if row["event"] == "launch_attempt_enter"
                        and row["mono_ns"] > stop["mono_ns"]
                    ]
                    observer.record("post_stop_launch_fence_verified")
            finally:
                # STOP trials hold actual parent work, not the Manager/worker.
                # Existing harness cleanup owns stopping the tasks and broker.
                _retire_control(observer, thread)
    finally:
        summary = _persist_sample(
            destination, observer, family, started, ended, mode, sys.exception()
        )
    return summary


def _run_spread(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain preregistered per-run values; never turn timing into pass/fail."""
    measured = [run for run in runs if run["repetition"] > 0]
    series: dict[str, list[Any]] = defaultdict(list)
    for run in measured:
        work, stop = run["throughput"], run["stop"]
        values = {
            "elapsed_seconds": work["elapsed_seconds"],
            "ping_call_to_return_seconds": work["control_latency"].get(
                "ping_call_to_return_seconds"
            ),
            "ping_write_to_matched_pong_seconds": work["control_latency"].get(
                "ping_write_begin_to_matched_pong_seconds"
            ),
            "stop_handler_return_seconds": (stop["control_latency"] or {}).get(
                "write_begin_to_handler_return_seconds"
            ),
            "stop_drain_seconds": stop["closure"].get(
                "stop_write_begin_to_drain_finish_seconds"
            ),
        }
        for name in (
            "receipt_only_turns",
            "receipt_turns_censored",
            "maintenance_yields",
            "maintenance_yield_turns",
            "maintenance_yields_per_launch_attempt",
        ):
            values[name] = work[name]
        values["parent_start_spacing_mean_secondary_seconds"] = work[
            "parent_start_spacing_secondary"
        ]["mean_seconds"]
        for distribution, stats in work["latency_summary"].items():
            for statistic in ("median_seconds", "p95_seconds"):
                values[f"{distribution}_{statistic}"] = stats[statistic]
        for name, value in values.items():
            series[name].append(value)
    output = {}
    for name, samples in series.items():
        known = [value for value in samples if value is not None]
        output[name] = {
            "per_run_values": samples,
            "min": min(known) if known else None,
            "max": max(known) if known else None,
            "available_runs": len(known),
        }
    return {
        "measured_repetitions": [run["repetition"] for run in measured],
        "warmup_excluded": True,
        "summary_series": output,
        "decision": "diagnostic only; compare actual revision arms using manifest's predeclared rule; missing samples, disjoint surrounding baseline spreads, or unmatched batch ordinals remain uncertainty",
    }


def _verify_artifacts(destination: Path, mode: Literal["quiet", "trace"]) -> None:
    """Completion marker is written only after the full raw-artifact gate."""
    expected = []
    for repetition in range(4):
        label = "warmup" if repetition == 0 else f"sample-{repetition}"
        for scenario in ("throughput", "stop"):
            folder = destination / f"{label}-{scenario}"
            names = [
                "sample.json",
                "config.json",
                "events.jsonl",
                "family.jsonl",
                "summary.json",
            ]
            if scenario == "stop":
                names.append("stop-custody.json")
            for name in names:
                assert (folder / name).is_file(), (
                    f"missing raw artifact: {folder / name}"
                )
            sample = json.loads((folder / "sample.json").read_text())
            assert (
                sample["repetition"] == repetition
                and sample["measurement_mode"] == mode
            )
            assert (folder / "events.jsonl").stat().st_size > 0
            expected.append(folder.name)
    batch = json.loads((destination / "arm-summary.json").read_text())
    assert batch["measured_repetitions"] == [1, 2, 3]
    assert all(
        (destination / name).is_file()
        for name in ("manifest.json", "source.diff", "source-provenance.json")
    )
    provenance = json.loads((destination / "source-provenance.json").read_text())
    manifest = json.loads((destination / "manifest.json").read_text())
    # Integrity checks on persisted proof, not independent lifecycle assertions.
    assert provenance["worker"]["modules"] == provenance["expected"]
    assert all(
        entry["sha256"] == manifest["source_hashes"][relative]
        for relative, entry in provenance["expected"].items()
    )
    _json(
        destination / "completion.json",
        {
            "status": "completed",
            "measurement_mode": mode,
            "warmups": 1,
            "measured_repetitions": [1, 2, 3],
            "sample_directories": expected,
            "raw_artifacts_verified": True,
            "child_source_provenance_verified": True,
            "note": "STOP family.jsonl may be empty when STOP precedes the first user function event",
        },
    )


@pytest.mark.skipif(
    "PYTEST_WEFT_DISPATCH_ARTIFACT_DIR" not in os.environ,
    reason="Opt-in dispatch experiment: set PYTEST_WEFT_DISPATCH_ARTIFACT_DIR to an absolute writable directory; existing correctness acceptance is separate",
)
@pytest.mark.parametrize("measurement_mode", ["quiet", "trace"], ids=["quiet", "trace"])
def test_manager_dispatch_costs(
    measurement_mode: Literal["quiet", "trace"],
    dispatch_backend: None,
    request: pytest.FixtureRequest,
) -> None:
    """Measure real source arms; assert completion/control, never speed ratios."""
    del dispatch_backend
    configured = os.environ["PYTEST_WEFT_DISPATCH_ARTIFACT_DIR"]
    parent = Path(configured)
    if not configured or not parent.is_absolute():
        pytest.fail(
            "PYTEST_WEFT_DISPATCH_ARTIFACT_DIR must be a nonempty absolute path",
            pytrace=False,
        )
    destination = (
        parent
        / f"{measurement_mode}-{active_test_backend()}-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-{uuid.uuid4().hex}"
    )
    try:
        # Atomic claim: stale runs cannot share this invocation's artifact parent.
        parent.mkdir(parents=True)
        destination.mkdir()
        _manifest(request, destination, measurement_mode)
    except OSError as exc:
        pytest.fail(
            f"Dispatch artifacts require a fresh writable parent {parent}: {exc}",
            pytrace=False,
        )
    print(f"DISPATCH_ARTIFACTS={destination}", flush=True)
    _json(
        destination / "source-provenance.json",
        _source_preflight(Path(request.config.rootpath).resolve()),
    )
    runs = []
    for repetition in range(4):
        label = "warmup" if repetition == 0 else f"sample-{repetition}"
        throughput = _sample(
            destination / f"{label}-throughput",
            "PING",
            repetition=repetition,
            mode=measurement_mode,
        )
        stopped = _sample(
            destination / f"{label}-stop",
            "STOP",
            repetition=repetition,
            mode=measurement_mode,
        )
        runs.append(
            {"repetition": repetition, "throughput": throughput, "stop": stopped}
        )
        _json(destination / "arm-summary.json", _run_spread(runs))
    _verify_artifacts(destination, measurement_mode)
