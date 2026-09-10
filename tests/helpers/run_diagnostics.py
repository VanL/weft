"""Opt-in pytest crash artifacts outside disposable harness roots.

Set WEFT_TEST_DIAGNOSTICS_DIR to an absolute run-owned directory. Each process
writes its own event and fatal-stack files; xdist's controller also records
worker exits. No watchdog, signal interception, or test outcome policy is added.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO

import pytest


class _RunDiagnostics:
    def __init__(self, directory: Path, worker: str) -> None:
        self.worker = worker
        self.pid = os.getpid()
        stem = f"{worker}-{self.pid}"
        self.events_path = directory / f"{stem}.jsonl"
        self.stack_file: TextIO = (directory / f"{stem}.stacks.log").open(
            "a", encoding="utf-8"
        )
        self.enabled = False
        self.was_enabled = False
        self.original_stderr_fd: int | None = None

    def _record(self, event: str, **details: object) -> None:
        try:
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": event,
                            "worker": self.worker,
                            "pid": self.pid,
                            "time_ns": time.time_ns(),
                            **details,
                        }
                    )
                    + "\n"
                )
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics must never replace the test's own outcome.

    def pytest_sessionstart(self) -> None:
        # Pytest's built-in faulthandler configures first. Restore stderr before
        # closing our file; pytest still owns its original restoration at exit.
        try:
            self.was_enabled = faulthandler.is_enabled()
            stderr = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
            self.original_stderr_fd = stderr.fileno()
            faulthandler.enable(file=self.stack_file, all_threads=True)
            self.enabled = True
        except (OSError, ValueError, RuntimeError):
            pass
        self._record("session_start")

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        self._record("test_start", nodeid=nodeid)

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        self._record("test_finish", nodeid=nodeid)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: object) -> None:
        self._record(
            "worker_down",
            worker_id=node.gateway.id,
            worker_pid=getattr(node, "workerinfo", {}).get("pid"),
            error=str(error) if error is not None else None,
        )

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self._record("session_finish", exitstatus=int(exitstatus))
        self._close()

    def pytest_unconfigure(self) -> None:
        self._close()

    def _close(self) -> None:
        if self.enabled:
            faulthandler.disable()
            self.enabled = False
            if self.was_enabled and self.original_stderr_fd is not None:
                try:
                    faulthandler.enable(file=self.original_stderr_fd)
                except (OSError, ValueError, RuntimeError):
                    pass
        try:
            self.stack_file.close()
        except OSError:
            pass


def pytest_configure(config: pytest.Config) -> None:
    directory = os.environ.get("WEFT_TEST_DIAGNOSTICS_DIR")
    if not directory:
        return
    try:
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        workerinput = getattr(config, "workerinput", {})
        worker = str(workerinput.get("workerid", "controller"))
        worker = "".join(char for char in worker if char.isalnum() or char == "-")
        plugin = _RunDiagnostics(root, worker)
    except (OSError, ValueError, RuntimeError):
        return
    config.pluginmanager.register(plugin, "weft-run-diagnostics")
