from __future__ import annotations

import gc
import json
import os
import signal
import tempfile
import time
from pathlib import Path

from simplebroker import Queue
from weft._constants import (
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.commands import worker as worker_cmd
from weft.context import WeftContext, build_context


class WeftTestHarness:
    """Isolated Weft test environment with automatic cleanup."""

    DEFAULT_DB_NAME = "weft-tests.db"

    def __init__(self, *, manager_timeout: float = 0.5) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="weft-harness-")
        self.root = Path(self._tempdir.name)
        self._manager_timeout = manager_timeout
        self._original_env: dict[str, str | None] = {}
        self._orig_cwd: Path | None = None
        self._context: WeftContext | None = None
        self._registered_pids: set[int] = set()
        self._registered_tids: set[str] = set()
        self._closed = False

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------
    def __enter__(self) -> WeftTestHarness:
        self._orig_cwd = Path.cwd()
        self._patch_environment()
        os.chdir(self.root)
        # Ensure context and database are initialised early.
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
        if pid > 0:
            self._registered_pids.add(pid)
        if tid:
            self._registered_tids.add(tid)

    def register_tid(self, tid: str) -> None:
        if tid:
            self._registered_tids.add(tid)

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

        for tid in all_tids:
            worker_cmd.stop_command(
                tid=tid,
                force=False,
                timeout=self._manager_timeout,
                context_path=context_path,
            )
            worker_cmd.stop_command(
                tid=tid,
                force=True,
                timeout=self._manager_timeout,
                context_path=context_path,
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
            pid = data.get("pid")
            if isinstance(pid, int):
                self._registered_pids.add(pid)
        queue.close()

    def _terminate_registered_pids(self) -> None:
        for pid in list(self._registered_pids):
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
