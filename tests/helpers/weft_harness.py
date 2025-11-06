from __future__ import annotations

import gc
import json
import os
import signal
import tempfile
import time
from pathlib import Path

import psutil

from simplebroker import Queue
from weft._constants import (
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.commands import worker as worker_cmd
from weft.context import WeftContext, build_context


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
        self._registered_tids: set[str] = set()
        self._closed = False
        self._self_pid = os.getpid()
        self._safe_pids = self._compute_safe_pid_set()

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------
    def __enter__(self) -> WeftTestHarness:
        self._orig_cwd = Path.cwd()
        self._patch_environment()
        os.chdir(self.root)
        # Ensure context and database are initialized early.
        _ = self.context
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
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
    def register_pid(self, pid: int, tid: str | None = None) -> None:
        if pid > 0 and not self._should_skip_pid(pid):
            self._registered_pids.add(pid)
        if tid:
            self._registered_tids.add(tid)

    def register_tid(self, tid: str) -> None:
        if tid:
            self._registered_tids.add(tid)

    def registered_tids(self) -> set[str]:
        return set(self._registered_tids)

    def dump_debug_state(self) -> str:
        """Return a human-readable snapshot of harness state for debugging."""

        lines: list[str] = [
            "WeftTestHarness snapshot:",
            f"  root={self.root}",
            f"  registered_tids={sorted(self._registered_tids)}",
            f"  registered_pids={sorted(self._registered_pids)}",
        ]

        context = self._context
        if context is None:
            lines.append("  context=uninitialized")
            return "\n".join(lines)

        lines.append(f"  database_path={context.database_path}")

        def _peek_queue(name: str, *, persistent: bool, limit: int = 20) -> list[str]:
            queue = Queue(
                name,
                db_path=str(context.database_path),
                persistent=persistent,
                config=context.broker_config,
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
                snippet: str
                try:
                    decoded = (
                        json.loads(payload) if isinstance(payload, str) else payload
                    )
                    snippet = json.dumps(decoded, ensure_ascii=False)
                except Exception:
                    snippet = str(payload)
                formatted.append(f"    ts={ts}: {snippet}")
            return formatted

        registry_dump = _peek_queue(
            WEFT_WORKERS_REGISTRY_QUEUE, persistent=False, limit=10
        )
        lines.append("  registry_tail:")
        lines.extend(registry_dump or ["    <empty>"])

        log_dump = _peek_queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False, limit=10)
        lines.append("  log_tail:")
        lines.extend(log_dump or ["    <empty>"])

        return "\n".join(lines)

    def wait_for_completion(self, tid: str, timeout: float = 10.0) -> None:
        log_queue = Queue(
            WEFT_GLOBAL_LOG_QUEUE,
            db_path=str(self.context.database_path),
            persistent=False,
            config=self.context.broker_config,
        )
        deadline = time.time() + timeout
        last_seen: int | None = None
        try:
            while time.time() < deadline:
                records = log_queue.peek_many(limit=512, with_timestamps=True) or []
                for payload, ts in records:
                    if last_seen is not None and ts <= last_seen:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if data.get("tid") != tid:
                        continue
                    event = data.get("event")
                    if event == "work_completed":
                        return
                    if event in {"work_failed", "work_timeout"}:
                        raise RuntimeError(f"Task {tid} reported {event}")
                    last_seen = ts
                time.sleep(0.05)
        finally:
            log_queue.close()

        # Fallback: inspect task outbox directly in case log events were missed.
        outbox_name = f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"
        outbox_queue = Queue(
            outbox_name,
            db_path=str(self.context.database_path),
            persistent=True,
            config=self.context.broker_config,
        )
        try:
            message = outbox_queue.peek_one()
            if message is not None:
                return
        finally:
            outbox_queue.close()

        raise TimeoutError(f"Timed out waiting for task {tid}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        if self._closed:
            return

        try:
            self._stop_active_workers()
            self._collect_pid_mappings()
            self._terminate_registered_pids()
            self._remove_database_files()
        finally:
            self._restore_cwd()
            self._restore_environment()
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
            "WEFT_DEFAULT_DB_LOCATION": str(self.root),
            "WEFT_DEFAULT_DB_NAME": self.DEFAULT_DB_NAME,
            "BROKER_DEFAULT_DB_LOCATION": str(self.root),
            "BROKER_DEFAULT_DB_NAME": self.DEFAULT_DB_NAME,
        }
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

    def _stop_active_workers(self) -> None:
        context_path = self.context.root

        all_tids: set[str] = set(self._registered_tids)
        exit_code, payload = worker_cmd.list_command(
            json_output=True,
            context_path=context_path,
        )
        if exit_code == 0 and payload:
            try:
                records = json.loads(payload)
            except json.JSONDecodeError:
                records = []
            for record in records:
                tid = record.get("tid")
                if isinstance(tid, str):
                    all_tids.add(tid)

        stop_timeout = max(6.0, min(self._manager_timeout, 10.0))

        for tid in all_tids:
            rc, _ = worker_cmd.stop_command(
                tid=tid,
                force=False,
                timeout=stop_timeout,
                context_path=context_path,
                stop_if_absent=True,
            )
            if rc == 0:
                continue
            worker_cmd.stop_command(
                tid=tid,
                force=True,
                timeout=stop_timeout,
                context_path=context_path,
                stop_if_absent=True,
            )

        time.sleep(0.1)
        self._drain_registry_queue()

    def _drain_registry_queue(self) -> None:
        queue = Queue(
            WEFT_WORKERS_REGISTRY_QUEUE,
            db_path=str(self.context.database_path),
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            while queue.read_one() is not None:
                continue
        finally:
            queue.close()

    def _collect_pid_mappings(self) -> None:
        queue = Queue(
            WEFT_TID_MAPPINGS_QUEUE,
            db_path=str(self.context.database_path),
            persistent=False,
            config=self.context.broker_config,
        )
        try:
            records = queue.peek_many(limit=2048) or []
        except Exception:  # pragma: no cover - defensive
            queue.close()
            return

        for raw in records:
            payload = raw[0] if isinstance(raw, tuple) else raw
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            caller_pid = data.get("caller_pid")
            if isinstance(caller_pid, int):
                self._mark_safe_pid(caller_pid)

            pid_candidates: list[int] = []
            for key in ("task_pid", "pid"):
                candidate = data.get(key)
                if isinstance(candidate, int):
                    pid_candidates.append(candidate)

            managed = data.get("managed_pids")
            if isinstance(managed, list):
                for value in managed:
                    if isinstance(value, int):
                        pid_candidates.append(value)

            for candidate in pid_candidates:
                self.register_pid(candidate)
        queue.close()

    def _terminate_registered_pids(self) -> None:
        for pid in list(self._registered_pids):
            if self._should_skip_pid(pid):
                continue
            self._terminate_pid(pid)
        self._registered_pids.clear()

    def _terminate_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

        deadline = time.time() + self._manager_timeout
        while time.time() < deadline:
            if not self._pid_alive(pid):
                return
            time.sleep(0.05)

        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

        for _ in range(10):
            if not self._pid_alive(pid):
                return
            time.sleep(0.05)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

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
        base = self.context.database_path
        candidates = [base, Path(f"{base}-wal"), Path(f"{base}-shm")]
        for path in candidates:
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                time.sleep(0.05)
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    pass


__all__ = ["WeftTestHarness"]
