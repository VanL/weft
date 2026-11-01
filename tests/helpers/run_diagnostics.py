"""Opt-in pytest crash artifacts outside disposable harness roots.

Set WEFT_TEST_DIAGNOSTICS_DIR to an absolute run-owned directory. Each process
writes its own event and fatal-stack files; xdist's controller also records
worker exits. Worker terminal output is retained for pytest-timeout dumps.
No watchdog, signal interception, or test outcome policy is added.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import pytest


class _RetainedOutput:
    """Keep diagnostic I/O failure out of pytest-timeout's exit sequence."""

    def __init__(self, retained: TextIO, original: TextIO) -> None:
        self.retained = retained
        self.original = original
        self.failed = False

    def write(self, text: str) -> int:
        if not self.failed:
            try:
                return self.retained.write(text)
            except (OSError, ValueError):
                self.failed = True
        return self.original.write(text)

    def flush(self) -> None:
        if not self.failed:
            try:
                self.retained.flush()
                return
            except (OSError, ValueError):
                self.failed = True
        self.original.flush()


class _RunDiagnostics:
    def __init__(self, directory: Path, worker: str, config: pytest.Config) -> None:
        self.config = config
        self.worker = worker
        self.pid = os.getpid()
        stem = f"{worker}-{self.pid}"
        self.events_path = directory / f"{stem}.jsonl"
        self.stack_file: TextIO = (directory / f"{stem}.stacks.log").open(
            "a", encoding="utf-8"
        )
        self.terminal_file: TextIO | None = None
        try:
            if hasattr(config, "workerinput"):
                self.terminal_file = (directory / f"{stem}.terminal.log").open(
                    "a", encoding="utf-8", errors="backslashreplace", buffering=1
                )
        except (OSError, ValueError, RuntimeError):
            self.stack_file.close()
            raise
        self.terminal_writer: Any = None
        self.terminal_output: _RetainedOutput | None = None
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
        if self.terminal_file is not None:
            reporter = self.config.pluginmanager.get_plugin("terminalreporter")
            writer: Any = getattr(reporter, "_tw", None)
            original: Any = getattr(writer, "_file", None)
            if callable(getattr(original, "write", None)) and callable(
                getattr(original, "flush", None)
            ):
                # pytest 9.1/9.2 exposes no stream setter. Preserve its existing
                # writer/settings; restore this private seam before closing.
                self.terminal_writer = writer
                self.terminal_output = _RetainedOutput(self.terminal_file, original)
                writer._file = self.terminal_output
                self._record("terminal_sink_installed")
            else:
                self._record("terminal_sink_unavailable")
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
        output = self.terminal_output
        if output is not None:
            original = output.original
            if getattr(self.terminal_writer, "_file", None) is output:
                self.terminal_writer._file = original
            self.terminal_output = None
        if self.terminal_file is not None:
            try:
                self.terminal_file.close()
            except OSError:
                pass
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
    plugin: _RunDiagnostics | None = None
    try:
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        workerinput = getattr(config, "workerinput", {})
        worker = str(workerinput.get("workerid", "controller"))
        worker = "".join(char for char in worker if char.isalnum() or char == "-")
        plugin = _RunDiagnostics(root, worker, config)
        config.pluginmanager.register(plugin, "weft-run-diagnostics")
    except (OSError, ValueError, RuntimeError):
        if plugin is not None:
            plugin._close()
        return


def record_phase(config: pytest.Config, phase: str, **details: object) -> None:
    """Append opt-in test-owner progress through the existing event recorder."""
    plugin = config.pluginmanager.get_plugin("weft-run-diagnostics")
    if isinstance(plugin, _RunDiagnostics):
        plugin._record(
            "phase",
            phase=phase,
            nodeid=os.environ.get("PYTEST_CURRENT_TEST"),
            **details,
        )


@contextmanager
def phase(config: pytest.Config, name: str, **details: object) -> Iterator[None]:
    """Record `{name}:start` and `{name}:done` with elapsed time and error type."""
    record_phase(config, f"{name}:start", **details)
    started = time.monotonic()
    try:
        yield
    finally:
        exc = sys.exception()
        record_phase(
            config,
            f"{name}:done",
            elapsed=round(time.monotonic() - started, 3),
            error=None if exc is None else type(exc).__name__,
            **details,
        )
