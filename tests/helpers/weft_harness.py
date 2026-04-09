from __future__ import annotations

import gc
import json
import os
import tempfile
import time
from pathlib import Path
from types import TracebackType

import psutil

from simplebroker import Queue
from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root
from weft._constants import (
    CONTROL_STOP,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.commands import tasks as task_cmd
from weft.commands import worker as worker_cmd
from weft.context import WeftContext, build_context
from weft.helpers import iter_queue_json_entries, pid_is_live, terminate_process_tree

DEFAULT_TASK_COMPLETION_TIMEOUT = 60.0
TERMINAL_TASK_EVENTS = {
    "work_completed",
    "work_failed",
    "work_timeout",
    "work_limit_violation",
    "task_signal_stop",
    "task_signal_kill",
}


class WeftTestHarness:
    """Isolated Weft test environment with automatic cleanup."""

    DEFAULT_DB_NAME = "weft-tests.db"

    def __init__(self, *, manager_timeout: float = 30.0) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="weft-harness-")
        self.root = Path(self._tempdir.name)
        self._manager_timeout = manager_timeout
        self._original_env: dict[str, str | None] = {}
        self._orig_cwd: Path | None = None
        self._context: WeftContext | None = None
        self._registered_pids: set[int] = set()
        self._registered_owner_pids: set[int] = set()
        self._registered_managed_pids: set[int] = set()
        self._registered_tids: set[str] = set()
        self._registered_worker_tids: set[str] = set()
        self._closed = False
        self._self_pid = os.getpid()
        self._safe_pids = self._compute_safe_pid_set()

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------
    def __enter__(self) -> WeftTestHarness:
        self._orig_cwd = Path.cwd()
        self._patch_environment()
        prepare_project_root(self.root)
        os.chdir(self.root)
        # Ensure context and database are initialized early.
        _ = self.context
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()

    def __del__(self) -> None:  # noqa: D401
        self.cleanup()

    @property
    def context(self) -> WeftContext:
        if self._context is None:
            self._context = build_context(spec_context=self.root)
        return self._context

    # ------------------------------------------------------------------
    # Resource tracking
    # ------------------------------------------------------------------
    def register_pid(
        self,
        pid: int,
        tid: str | None = None,
        *,
        kind: str = "owner",
    ) -> None:
        if pid > 0 and not self._should_skip_pid(pid):
            self._registered_pids.add(pid)
            if kind == "managed":
                self._registered_managed_pids.add(pid)
            else:
                self._registered_owner_pids.add(pid)
        if tid:
            self._registered_tids.add(tid)

    def register_tid(self, tid: str) -> None:
        if tid:
            self._registered_tids.add(tid)

    def register_worker_tid(self, tid: str) -> None:
        if tid:
            self._registered_worker_tids.add(tid)

    def registered_tids(self) -> set[str]:
        return set(self._registered_tids)

    def registered_worker_tids(self) -> set[str]:
        return set(self._registered_worker_tids)

    @staticmethod
    def _format_debug_payload(payload: object) -> str:
        try:
            decoded = json.loads(payload) if isinstance(payload, str) else payload
            return json.dumps(decoded, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(payload)

    def _peek_queue_lines(
        self,
        name: str,
        *,
        persistent: bool,
        limit: int = 20,
    ) -> list[str]:
        queue = Queue(
            name,
            db_path=self.context.broker_target,
            persistent=persistent,
            config=self.context.broker_config,
        )
        try:
            entries = queue.peek_many(limit=limit, with_timestamps=True) or []
        except Exception as exc:  # pragma: no cover - defensive logging
            return [f"<error reading {name}: {exc}>"]
        finally:
            queue.close()

        formatted: list[str] = []
        for entry in entries[-limit:]:
            if isinstance(entry, tuple) and len(entry) == 2:
                payload, ts = entry
            else:
                payload, ts = entry, None
            formatted.append(f"    ts={ts}: {self._format_debug_payload(payload)}")
        return formatted

    def _task_log_tail_lines(self, tid: str, *, limit: int = 10) -> list[str]:
        queue = Queue(
            WEFT_GLOBAL_LOG_QUEUE,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            matching: list[str] = []
            for data, ts in iter_queue_json_entries(queue):
                if data.get("tid") != tid:
                    continue
                matching.append(f"    ts={ts}: {self._format_debug_payload(data)}")
        finally:
            queue.close()
        return matching[-limit:]

    def dump_completion_timeout_state(self, tid: str) -> str:
        lines = [
            "Task completion timeout snapshot:",
            f"  tid={tid}",
        ]

        latest_event = self._latest_task_events().get(tid)
        lines.append(f"  latest_task_event={latest_event or '<missing>'}")

        latest_mapping = self._latest_tid_mapping_payloads().get(tid)
        if latest_mapping is None:
            lines.append("  latest_tid_mapping=<missing>")
            candidate_pids: list[int] = []
        else:
            lines.append(
                f"  latest_tid_mapping={self._format_debug_payload(latest_mapping)}"
            )
            candidate_pids = []
            for key in ("task_pid", "pid"):
                value = latest_mapping.get(key)
                if isinstance(value, int):
                    candidate_pids.append(value)
            managed = latest_mapping.get("managed_pids")
            if isinstance(managed, list):
                candidate_pids.extend(
                    value for value in managed if isinstance(value, int)
                )

        deduped_candidate_pids = sorted(set(candidate_pids))
        lines.append(f"  candidate_pids={deduped_candidate_pids}")
        live_candidate_pids = [
            pid
            for pid in deduped_candidate_pids
            if not self._should_skip_pid(pid) and self._pid_alive(pid)
        ]
        lines.append(f"  live_candidate_pids={live_candidate_pids}")
        lines.append(f"  registered_live_pids={self._live_registered_pids()}")

        outbox_name = f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"
        outbox_queue = Queue(
            outbox_name,
            db_path=self.context.broker_target,
            persistent=True,
            config=self.context.broker_config,
        )
        try:
            outbox_message = outbox_queue.peek_one()
        finally:
            outbox_queue.close()

        lines.append(f"  outbox_present={outbox_message is not None}")
        if outbox_message is not None:
            lines.append(f"  outbox_head={self._format_debug_payload(outbox_message)}")

        lines.append("  task_log_tail:")
        lines.extend(self._task_log_tail_lines(tid, limit=10) or ["    <empty>"])

        lines.append(self.dump_debug_state())
        return "\n".join(lines)

    def dump_debug_state(self) -> str:
        """Return a human-readable snapshot of harness state for debugging."""

        lines: list[str] = [
            "WeftTestHarness snapshot:",
            f"  root={self.root}",
            f"  registered_tids={sorted(self._registered_tids)}",
            f"  registered_worker_tids={sorted(self._registered_worker_tids)}",
            f"  registered_pids={sorted(self._registered_pids)}",
        ]

        context = self._context
        if context is None:
            lines.append("  context=uninitialized")
            return "\n".join(lines)

        lines.append(f"  database_path={context.database_path}")

        registry_dump = self._peek_queue_lines(
            WEFT_WORKERS_REGISTRY_QUEUE,
            persistent=False,
            limit=10,
        )
        lines.append("  registry_tail:")
        lines.extend(registry_dump or ["    <empty>"])

        log_dump = self._peek_queue_lines(
            WEFT_GLOBAL_LOG_QUEUE,
            persistent=False,
            limit=10,
        )
        lines.append("  log_tail:")
        lines.extend(log_dump or ["    <empty>"])

        return "\n".join(lines)

    def wait_for_completion(
        self,
        tid: str,
        timeout: float = DEFAULT_TASK_COMPLETION_TIMEOUT,
    ) -> None:
        log_queue = Queue(
            WEFT_GLOBAL_LOG_QUEUE,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        deadline = time.time() + timeout
        last_seen: int | None = None
        try:
            while time.time() < deadline:
                next_last_seen = last_seen
                for data, ts in iter_queue_json_entries(
                    log_queue,
                    since_timestamp=last_seen,
                ):
                    if last_seen is not None and ts <= last_seen:
                        continue
                    next_last_seen = ts
                    if data.get("tid") != tid:
                        continue
                    event = data.get("event")
                    if event == "work_completed":
                        return
                    if event in {
                        "work_failed",
                        "work_timeout",
                        "work_limit_violation",
                        "control_stop",
                        "control_kill",
                        "task_signal_stop",
                        "task_signal_kill",
                    }:
                        raise RuntimeError(f"Task {tid} reported {event}")
                last_seen = next_last_seen
                time.sleep(0.05)
        finally:
            log_queue.close()

        # Fallback: inspect task outbox directly in case log events were missed.
        outbox_name = f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"
        outbox_queue = Queue(
            outbox_name,
            db_path=self.context.broker_target,
            persistent=True,
            config=self.context.broker_config,
        )
        try:
            message = outbox_queue.peek_one()
            if message is not None:
                return
        finally:
            outbox_queue.close()

        raise TimeoutError(
            f"Timed out waiting for task {tid}\n"
            f"{self.dump_completion_timeout_state(tid)}"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self, *, preserve_database: bool = False) -> None:
        if self._closed:
            return

        try:
            if preserve_database:
                self._cleanup_preserving_database()
                return

            self._stop_active_workers(
                force=True,
                drain_registry=True,
                stop_tasks=True,
            )
            self._collect_pid_mappings()
            self._wait_for_registered_pids_to_exit()
            if not preserve_database:
                self._terminate_registered_pids()
                self._remove_database_files()
                cleanup_prepared_roots(self.root)
        finally:
            self._restore_cwd()
            self._restore_environment()
            if not preserve_database:
                self._tempdir.cleanup()
            self._closed = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _patch_environment(self) -> None:
        overrides = {
            "WEFT_TEST_MODE": "1",
            "WEFT_MANAGER_REUSE_ENABLED": "0",
            "WEFT_MANAGER_LIFETIME_TIMEOUT": str(self._manager_timeout),
        }
        if os.environ.get("BROKER_TEST_BACKEND", "sqlite") == "sqlite":
            overrides.update(
                {
                    "WEFT_DEFAULT_DB_LOCATION": str(self.root),
                    "WEFT_DEFAULT_DB_NAME": self.DEFAULT_DB_NAME,
                    "BROKER_DEFAULT_DB_LOCATION": str(self.root),
                    "BROKER_DEFAULT_DB_NAME": self.DEFAULT_DB_NAME,
                }
            )
        for key, value in overrides.items():
            if key not in self._original_env:
                self._original_env[key] = os.environ.get(key)
            os.environ[key] = value

    def _restore_environment(self) -> None:
        for key, original in self._original_env.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        self._original_env.clear()

    def _restore_cwd(self) -> None:
        if self._orig_cwd is not None:
            try:
                os.chdir(self._orig_cwd)
            except FileNotFoundError:
                pass
            self._orig_cwd = None

    def _load_tid_mapping_payloads(self) -> list[dict[str, object]]:
        queue = Queue(
            WEFT_TID_MAPPINGS_QUEUE,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            records = queue.peek_many(limit=2048) or []
        except Exception:  # pragma: no cover - defensive
            queue.close()
            return []
        finally:
            queue.close()

        payloads: list[dict[str, object]] = []
        for raw in records:
            payload = raw[0] if isinstance(raw, tuple) else raw
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                payloads.append(data)
        return payloads

    def _latest_tid_mapping_payloads(self) -> dict[str, dict[str, object]]:
        latest: dict[str, dict[str, object]] = {}
        for data in self._load_tid_mapping_payloads():
            full_tid = data.get("full")
            if isinstance(full_tid, str) and full_tid:
                latest[full_tid] = data
        return latest

    @staticmethod
    def _mapping_role(data: dict[str, object]) -> str | None:
        role = data.get("role")
        if isinstance(role, str) and role:
            return role
        return None

    def _mapping_owner_pid(self, data: dict[str, object]) -> int | None:
        for key in ("task_pid", "pid"):
            value = data.get(key)
            if isinstance(value, int):
                return value
        return None

    def _mapping_has_safe_owner(self, data: dict[str, object]) -> bool:
        owner_pid = self._mapping_owner_pid(data)
        return isinstance(owner_pid, int) and self._should_skip_pid(owner_pid)

    def _latest_task_events(self) -> dict[str, str]:
        queue = Queue(
            WEFT_GLOBAL_LOG_QUEUE,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        latest: dict[str, str] = {}
        try:
            for data, _timestamp in iter_queue_json_entries(queue):
                tid = data.get("tid")
                event = data.get("event")
                if isinstance(tid, str) and isinstance(event, str):
                    latest[tid] = event
        finally:
            queue.close()
        return latest

    def _live_task_tids_from_mappings(self) -> list[str]:
        live_tids: list[str] = []
        terminal_events = self._latest_task_events()
        for full_tid, data in self._latest_tid_mapping_payloads().items():
            if (
                full_tid in self._registered_worker_tids
                or self._mapping_role(data) == "manager"
                or self._mapping_has_safe_owner(data)
                or terminal_events.get(full_tid) in TERMINAL_TASK_EVENTS
            ):
                continue

            candidate_pids: list[int] = []
            for key in ("task_pid", "pid"):
                value = data.get(key)
                if isinstance(value, int):
                    candidate_pids.append(value)

            managed = data.get("managed_pids")
            if isinstance(managed, list):
                candidate_pids.extend(
                    value for value in managed if isinstance(value, int)
                )

            if any(
                pid > 0 and not self._should_skip_pid(pid) and self._pid_alive(pid)
                for pid in candidate_pids
            ):
                live_tids.append(full_tid)

        return sorted(set(live_tids))

    def _list_active_worker_records(self) -> list[dict[str, object]]:
        context_path = self.context.root
        exit_code, payload = worker_cmd.list_command(
            json_output=True,
            context_path=context_path,
        )
        if exit_code != 0 or not payload:
            return []

        try:
            records = json.loads(payload)
        except json.JSONDecodeError:
            return []

        active_records: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("status") != "active":
                continue
            active_records.append(record)
        return active_records

    def _cleanup_worker_records(self) -> dict[str, dict[str, object]]:
        worker_records: dict[str, dict[str, object]] = {}
        for record in self._list_active_worker_records():
            tid = record.get("tid")
            if isinstance(tid, str):
                self.register_worker_tid(tid)
                worker_records[tid] = record
        for tid in self._registered_worker_tids:
            worker_records.setdefault(tid, {"tid": tid})
        return worker_records

    def _send_worker_stop(self, tid: str, *, record: dict[str, object]) -> None:
        ctrl_in = record.get("ctrl_in")
        queue_name = (
            ctrl_in if isinstance(ctrl_in, str) and ctrl_in else f"T{tid}.ctrl_in"
        )
        pid = record.get("pid")
        if isinstance(pid, int):
            self.register_pid(pid, kind="owner")
        queue = Queue(
            queue_name,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            queue.write(CONTROL_STOP)
        finally:
            queue.close()

    def _send_task_stop(self, tid: str) -> None:
        task_cmd._send_control(self.context, tid, CONTROL_STOP)

    def _stop_active_workers(
        self,
        *,
        force: bool = True,
        drain_registry: bool = True,
        stop_tasks: bool = True,
    ) -> None:
        context_path = self.context.root
        issued_task_stops: set[str] = set()
        worker_records = self._cleanup_worker_records()
        stop_timeout = max(6.0, min(self._manager_timeout, 10.0))

        for tid, record in sorted(worker_records.items()):
            self._send_worker_stop(tid, record=record)

        for _ in range(3):
            self._collect_pid_mappings()
            if stop_tasks:
                task_tids = sorted(set(self._registered_tids) - issued_task_stops)
                if task_tids:
                    for tid in task_tids:
                        self._send_task_stop(tid)
                    issued_task_stops.update(task_tids)
            live_pids = self._wait_for_registered_pids_to_exit(timeout=1.0)
            if not live_pids:
                break

        if force and issued_task_stops:
            task_cmd.kill_tasks(sorted(issued_task_stops), context_path=context_path)
        if force:
            for tid in sorted(worker_records):
                worker_cmd.stop_command(
                    tid=tid,
                    force=True,
                    timeout=stop_timeout,
                    context_path=context_path,
                    stop_if_absent=True,
                )

        if issued_task_stops or worker_records:
            self._collect_pid_mappings()
            self._wait_for_registered_pids_to_exit()
            time.sleep(0.1)
        if drain_registry:
            self._drain_registry_queue()

    def _cleanup_preserving_database(self) -> None:
        wait_budget = max(6.0, min(self._manager_timeout, 10.0))
        if os.name == "nt":
            # Windows can keep SQLite WAL/SHM handles around after the last
            # live PID exits, especially under CI load.
            wait_budget = max(wait_budget, 30.0)
        deadline = time.time() + wait_budget
        issued_worker_stops: set[str] = set()
        issued_task_stops: set[str] = set()
        quiescent_since: float | None = None
        lingering_task_deadline: float | None = None
        last_live_task_tids: list[str] = []
        last_live_pids: list[int] = []

        while time.time() < deadline:
            self._collect_pid_mappings()

            for tid in sorted(self._registered_worker_tids):
                if tid in issued_worker_stops:
                    continue
                self._send_worker_stop(tid, record={"tid": tid})
                issued_worker_stops.add(tid)

            last_live_task_tids = self._live_task_tids_from_mappings()
            last_live_pids = self._live_registered_pids()
            if last_live_task_tids:
                if lingering_task_deadline is None:
                    lingering_task_deadline = time.time() + 2.0
                elif time.time() >= lingering_task_deadline:
                    for tid in sorted(last_live_task_tids):
                        if tid in issued_task_stops:
                            continue
                        self._send_task_stop(tid)
                        issued_task_stops.add(tid)
            else:
                lingering_task_deadline = None
            if not last_live_task_tids and not last_live_pids:
                if quiescent_since is None:
                    quiescent_since = time.time()
                elif time.time() - quiescent_since >= 0.1:
                    if self._database_files_releasable():
                        return
            else:
                quiescent_since = None

            time.sleep(0.05)

        if last_live_task_tids:
            raise RuntimeError(
                "Cleanup left live task processes while preserve_database=True: "
                f"{last_live_task_tids}"
            )
        if last_live_pids:
            raise RuntimeError(
                f"Cleanup left live processes while preserve_database=True: {last_live_pids}"
            )
        if not self._database_files_releasable():
            locked_paths = [str(path) for path in self._database_candidate_paths()]
            raise RuntimeError(
                "Cleanup left database files in use while preserve_database=True: "
                f"{locked_paths}"
            )

    def _drain_registry_queue(self) -> None:
        queue = Queue(
            WEFT_WORKERS_REGISTRY_QUEUE,
            db_path=self.context.broker_target,
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            while queue.read_one() is not None:
                continue
        finally:
            queue.close()

    def _collect_pid_mappings(self) -> None:
        for data in self._load_tid_mapping_payloads():
            full_tid = data.get("full")
            if isinstance(full_tid, str) and full_tid:
                if self._mapping_role(data) == "manager":
                    self.register_worker_tid(full_tid)
                elif (
                    full_tid not in self._registered_worker_tids
                    and not self._mapping_has_safe_owner(data)
                ):
                    self.register_tid(full_tid)

            caller_pid = data.get("caller_pid")
            if isinstance(caller_pid, int):
                self._mark_safe_pid(caller_pid)

            for key in ("task_pid", "pid"):
                candidate = data.get(key)
                if isinstance(candidate, int):
                    self.register_pid(candidate, kind="owner")

            managed = data.get("managed_pids")
            if isinstance(managed, list):
                for value in managed:
                    if isinstance(value, int):
                        self.register_pid(value, kind="managed")

    def _terminate_registered_pids(self) -> None:
        for pid in list(self._registered_managed_pids):
            if self._should_skip_pid(pid):
                continue
            self._terminate_pid(pid)
        self._registered_owner_pids.clear()
        self._registered_managed_pids.clear()
        self._registered_pids.clear()

    def _live_registered_pids(self) -> list[int]:
        return [
            pid
            for pid in self._registered_pids
            if not self._should_skip_pid(pid) and self._pid_alive(pid)
        ]

    def _wait_for_registered_pids_to_exit(
        self, *, timeout: float | None = None
    ) -> list[int]:
        deadline = time.time() + (
            min(self._manager_timeout, 5.0) if timeout is None else timeout
        )
        while time.time() < deadline:
            live_pids = self._live_registered_pids()
            if not live_pids:
                return []
            time.sleep(0.05)
        return self._live_registered_pids()

    def _terminate_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        if not self._pid_alive(pid):
            return
        try:
            terminated = terminate_process_tree(pid, timeout=self._manager_timeout)
        except Exception:
            return
        if pid in terminated or not self._pid_alive(pid):
            return

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        return pid_is_live(pid)

    def _mark_safe_pid(self, pid: int) -> None:
        if pid > 0:
            self._safe_pids.add(pid)

    def _compute_safe_pid_set(self) -> set[int]:
        safe_pids: set[int] = {self._self_pid}

        override = os.environ.get("WEFT_TEST_SAFE_PIDS")
        if override:
            for token in override.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    safe_pids.add(int(token))
                except ValueError:
                    continue

        try:
            process = psutil.Process(self._self_pid)
        except psutil.Error:  # pragma: no cover - defensive
            return safe_pids

        for ancestor in process.parents():
            safe_pids.add(ancestor.pid)

        parent_pid = process.ppid()
        if parent_pid and parent_pid > 0:
            safe_pids.add(parent_pid)

        return safe_pids

    def _should_skip_pid(self, pid: int) -> bool:
        if pid <= 0:
            return True
        if pid in self._safe_pids:
            return True
        if self._is_ancestor_pid(pid):
            self._safe_pids.add(pid)
            return True
        return False

    def _is_ancestor_pid(self, pid: int) -> bool:
        if pid in {self._self_pid, 0}:
            return True
        try:
            process = psutil.Process(self._self_pid)
        except psutil.Error:  # pragma: no cover - defensive
            return False

        for ancestor in process.parents():
            if ancestor.pid == pid:
                return True
        return False

    def _remove_database_files(self) -> None:
        if self._context is None:
            return

        gc.collect()
        for path in self._database_candidate_paths():
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                time.sleep(0.05)
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    pass

    def _database_candidate_paths(self) -> list[Path]:
        if self._context is None:
            return []

        base = self.context.database_path
        if base is None:
            return []

        candidates = [base, Path(f"{base}-wal"), Path(f"{base}-shm")]
        return [path for path in candidates if path.exists()]

    def _database_files_releasable(self) -> bool:
        candidates = self._database_candidate_paths()
        if not candidates:
            return True

        gc.collect()
        if os.name != "nt":
            return True

        renamed: list[tuple[Path, Path]] = []
        token = f".weft-release-probe-{os.getpid()}-{time.time_ns()}"
        try:
            for path in candidates:
                probe = path.with_name(f"{path.name}{token}")
                try:
                    path.replace(probe)
                except FileNotFoundError:
                    continue
                except PermissionError:
                    return False
                renamed.append((path, probe))
            return True
        finally:
            restore_errors: list[str] = []
            for original, probe in reversed(renamed):
                try:
                    if probe.exists():
                        probe.replace(original)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    restore_errors.append(f"{probe} -> {original}: {exc}")
            if restore_errors:
                raise RuntimeError(
                    "Failed to restore database files after release probe: "
                    + "; ".join(restore_errors)
                )


__all__ = ["WeftTestHarness"]
