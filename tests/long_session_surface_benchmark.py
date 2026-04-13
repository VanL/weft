"""Benchmark the long-session workload through CLI and in-process API surfaces.

This harness is intentionally dev-only. It replays the same mixed long-session
scenario used by the shared CLI integration test, but lets callers choose the
surface:

- ``cli``: subprocess ``python -m weft.cli`` calls via ``tests.conftest.run_cli``
- ``api``: direct in-process calls to ``weft.commands`` functions

Typical usage:

    source .envrc
    uv run python -m tests.long_session_surface_benchmark --surfaces api
    uv run python -m tests.long_session_surface_benchmark \
        --surfaces cli api \
        --backends sqlite postgres \
        --pg-dsn postgresql://postgres:postgres@127.0.0.1:32834/simplebroker_test
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from weft._constants import WEFT_COMPLETED_RESULT_GRACE_SECONDS
from weft.commands import queue as queue_cmd
from weft.commands.manager import stop_command
from weft.commands.result import cmd_result
from weft.commands.run import cmd_run
from weft.commands.status import cmd_status
from weft.commands.tasks import stop_tasks

REPO_ROOT = Path(__file__).resolve().parents[1]

if __package__ in {None, ""}:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tests.conftest import run_cli  # type: ignore[no-redef]
    from tests.helpers.long_session_utils import (  # type: ignore[no-redef]
        ALIAS_INTERVAL,
        BULK_ROUNDS,
        INTERACTIVE_INTERVAL,
        INTERACTIVE_SCRIPT,
        PERSISTENT_WORK_ITEMS,
        SESSION_TIMEOUT_SECONDS,
        SubmittedTask,
        interactive_expected,
        session_env,
        wait_for_terminal_statuses,
        write_command_script,
        write_persistent_spec,
    )
    from tests.helpers.test_backend import (  # type: ignore[no-redef]
        POSTGRES_TEST_BACKEND,
    )
    from tests.helpers.weft_harness import WeftTestHarness  # type: ignore[no-redef]
else:
    from .conftest import run_cli
    from .helpers.long_session_utils import (
        ALIAS_INTERVAL,
        BULK_ROUNDS,
        INTERACTIVE_INTERVAL,
        INTERACTIVE_SCRIPT,
        PERSISTENT_WORK_ITEMS,
        SESSION_TIMEOUT_SECONDS,
        SubmittedTask,
        interactive_expected,
        session_env,
        wait_for_terminal_statuses,
        write_command_script,
        write_persistent_spec,
    )
    from .helpers.test_backend import POSTGRES_TEST_BACKEND
    from .helpers.weft_harness import WeftTestHarness

SQLITE_BACKEND = "sqlite"
SURFACES = ("cli", "api")


class SessionSurface(Protocol):
    """Interface implemented by the CLI and API benchmark surfaces."""

    name: str

    def run_task(
        self,
        workdir: Path,
        harness: WeftTestHarness,
        env: Mapping[str, str],
        *,
        name: str | None,
        tags: Sequence[str],
        command: Sequence[str] = (),
        function: str | None = None,
        args: Sequence[str] = (),
        kwargs: Sequence[str] = (),
        env_vars: Sequence[str] = (),
        timeout: float | None = None,
        interactive: bool = False,
        spec: Path | None = None,
        wait: bool,
        stdin: str | None = None,
    ) -> tuple[int, str, str]:
        """Execute a task submission through the surface."""

    def queue_alias_add(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
        target: str,
    ) -> tuple[int, str, str]:
        """Create a queue alias."""

    def queue_alias_remove(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
    ) -> tuple[int, str, str]:
        """Remove a queue alias."""

    def queue_write(
        self,
        workdir: Path,
        env: Mapping[str, str],
        queue_name: str,
        message: str,
    ) -> tuple[int, str, str]:
        """Write one message to a queue."""

    def status_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        *,
        all_tasks: bool = False,
    ) -> tuple[int, str]:
        """Return a JSON status payload."""

    def result_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str]:
        """Return one task result as JSON."""

    def result_all_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
    ) -> tuple[int, str]:
        """Return all results as JSON."""

    def stop_task(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
    ) -> tuple[int, str, str]:
        """Stop one task."""

    def stop_worker(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str, str]:
        """Stop one manager."""


class _TTYStringIO(io.StringIO):
    """StringIO variant that reports itself as a TTY."""

    def isatty(self) -> bool:
        return True


@dataclass(frozen=True)
class BenchmarkSettings:
    """Settings that shape one benchmark run."""

    backends: tuple[str, ...]
    surfaces: tuple[str, ...]
    pg_dsn: str | None = None
    json_output: bool = False

    def validate(self) -> None:
        if not self.backends:
            raise ValueError("at least one backend is required")
        if not self.surfaces:
            raise ValueError("at least one surface is required")
        if any(surface not in SURFACES for surface in self.surfaces):
            raise ValueError(f"surfaces must be drawn from {SURFACES!r}")
        if POSTGRES_TEST_BACKEND in self.backends and not self.pg_dsn:
            raise ValueError(
                "Postgres benchmarks require --pg-dsn or SIMPLEBROKER_PG_TEST_DSN"
            )


@dataclass(frozen=True)
class BenchmarkResult:
    """One completed long-session benchmark run."""

    backend: str
    surface: str
    elapsed_seconds: float
    bulk_task_count: int
    interactive_count: int
    persistent_result_count: int


@contextmanager
def _backend_env(backend: str, pg_dsn: str | None) -> dict[str, str]:
    """Provide both process env and helper env for one backend."""

    keys = ("BROKER_TEST_BACKEND", "SIMPLEBROKER_PG_TEST_DSN")
    previous = {key: os.environ.get(key) for key in keys}
    env = {"BROKER_TEST_BACKEND": backend}
    if backend == POSTGRES_TEST_BACKEND:
        assert pg_dsn is not None
        env["SIMPLEBROKER_PG_TEST_DSN"] = pg_dsn

    try:
        os.environ["BROKER_TEST_BACKEND"] = backend
        if backend == POSTGRES_TEST_BACKEND:
            os.environ["SIMPLEBROKER_PG_TEST_DSN"] = pg_dsn or ""
        else:
            os.environ.pop("SIMPLEBROKER_PG_TEST_DSN", None)
        yield env
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _api_call_environment(
    workdir: Path,
    env: Mapping[str, str],
    *,
    stdin: str | None = None,
) -> None:
    """Temporarily patch env and stdin for in-process API calls."""

    previous_env = {key: os.environ.get(key) for key in [*env.keys(), "WEFT_CONTEXT"]}
    previous_pythonpath = os.environ.get("PYTHONPATH")
    previous_encoding = os.environ.get("PYTHONIOENCODING")
    previous_stdin = sys.stdin

    for key, value in env.items():
        os.environ[key] = value
    os.environ["WEFT_CONTEXT"] = str(workdir)
    pythonpath_parts = [str(REPO_ROOT)]
    if previous_pythonpath:
        pythonpath_parts.append(previous_pythonpath)
    os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    os.environ["PYTHONIOENCODING"] = "utf-8"

    if stdin is None:
        sys.stdin = _TTYStringIO("")
    else:
        sys.stdin = io.StringIO(stdin)

    try:
        yield
    finally:
        sys.stdin = previous_stdin
        if previous_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous_pythonpath
        if previous_encoding is None:
            os.environ.pop("PYTHONIOENCODING", None)
        else:
            os.environ["PYTHONIOENCODING"] = previous_encoding
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CliSurface:
    """Long-session benchmark surface backed by subprocess CLI calls."""

    name = "cli"

    def run_task(
        self,
        workdir: Path,
        harness: WeftTestHarness,
        env: Mapping[str, str],
        *,
        name: str | None,
        tags: Sequence[str],
        command: Sequence[str] = (),
        function: str | None = None,
        args: Sequence[str] = (),
        kwargs: Sequence[str] = (),
        env_vars: Sequence[str] = (),
        timeout: float | None = None,
        interactive: bool = False,
        spec: Path | None = None,
        wait: bool,
        stdin: str | None = None,
    ) -> tuple[int, str, str]:
        argv: list[object] = ["run"]
        if spec is not None:
            argv.extend(["--spec", spec])
        elif function is not None:
            argv.extend(["--function", function])
        elif interactive:
            argv.append("--interactive")

        if name is not None:
            argv.extend(["--name", name])
        for tag in tags:
            argv.extend(["--tag", tag])
        for item in args:
            argv.extend(["--arg", item])
        for item in kwargs:
            argv.extend(["--kw", item])
        for item in env_vars:
            argv.extend(["--env", item])
        if timeout is not None:
            argv.extend(["--timeout", str(timeout)])
        if not wait:
            argv.append("--no-wait")
        if spec is None and function is None and command:
            argv.append("--")
            argv.extend(command)

        return run_cli(
            *argv,
            cwd=workdir,
            harness=harness,
            env=dict(env),
            stdin=stdin,
            timeout=40.0 if interactive else 30.0,
        )

    def queue_alias_add(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
        target: str,
    ) -> tuple[int, str, str]:
        return run_cli(
            "queue",
            "alias",
            "add",
            alias,
            target,
            cwd=workdir,
            env=dict(env),
        )

    def queue_alias_remove(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
    ) -> tuple[int, str, str]:
        return run_cli(
            "queue",
            "alias",
            "remove",
            alias,
            cwd=workdir,
            env=dict(env),
        )

    def queue_write(
        self,
        workdir: Path,
        env: Mapping[str, str],
        queue_name: str,
        message: str,
    ) -> tuple[int, str, str]:
        return run_cli(
            "queue",
            "write",
            queue_name,
            message,
            cwd=workdir,
            env=dict(env),
        )

    def status_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        *,
        all_tasks: bool = False,
    ) -> tuple[int, str]:
        args: list[object] = ["status", "--json"]
        if all_tasks:
            args.append("--all")
        rc, out, err = run_cli(
            *args,
            cwd=workdir,
            env=dict(env),
            timeout=30.0,
        )
        if rc != 0:
            raise RuntimeError(err)
        return rc, out

    def result_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str]:
        rc, out, err = run_cli(
            "result",
            tid,
            "--timeout",
            str(timeout),
            "--json",
            cwd=workdir,
            env=dict(env),
            timeout=max(30.0, timeout + 10.0),
        )
        if rc != 0:
            raise RuntimeError(err)
        return rc, out

    def result_all_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
    ) -> tuple[int, str]:
        rc, out, err = run_cli(
            "result",
            "--all",
            "--json",
            cwd=workdir,
            env=dict(env),
            timeout=60.0,
        )
        if rc != 0:
            raise RuntimeError(err)
        return rc, out

    def stop_task(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
    ) -> tuple[int, str, str]:
        return run_cli(
            "task",
            "stop",
            tid,
            cwd=workdir,
            env=dict(env),
            timeout=30.0,
        )

    def stop_worker(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str, str]:
        return run_cli(
            "manager",
            "stop",
            tid,
            "--timeout",
            str(timeout),
            cwd=workdir,
            env=dict(env),
            timeout=max(30.0, timeout + 20.0),
        )


class ApiSurface:
    """Long-session benchmark surface backed by direct Python command calls."""

    name = "api"

    def _invoke_run(
        self,
        workdir: Path,
        env: Mapping[str, str],
        *,
        command: Sequence[str],
        spec: Path | None,
        function: str | None,
        args: Sequence[str],
        kwargs: Sequence[str],
        env_vars: Sequence[str],
        name: str | None,
        interactive: bool,
        timeout: float | None,
        tags: Sequence[str],
        wait: bool,
        stdin: str | None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with _api_call_environment(workdir, env, stdin=stdin):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cmd_run(
                    tuple(command),
                    spec=spec,
                    pipeline=None,
                    pipeline_input=None,
                    function=function,
                    args=tuple(args),
                    kwargs=tuple(kwargs),
                    env=tuple(env_vars),
                    name=name,
                    interactive=interactive,
                    stream_output=None,
                    timeout=timeout,
                    memory=None,
                    cpu=None,
                    tags=tuple(tags),
                    context_dir=workdir,
                    wait=wait,
                    json_output=False,
                    verbose=False,
                    monitor=False,
                    persistent_override=None,
                    autostart_enabled=True,
                )
        return rc, stdout.getvalue().strip(), stderr.getvalue().strip()

    def run_task(
        self,
        workdir: Path,
        harness: WeftTestHarness,
        env: Mapping[str, str],
        *,
        name: str | None,
        tags: Sequence[str],
        command: Sequence[str] = (),
        function: str | None = None,
        args: Sequence[str] = (),
        kwargs: Sequence[str] = (),
        env_vars: Sequence[str] = (),
        timeout: float | None = None,
        interactive: bool = False,
        spec: Path | None = None,
        wait: bool,
        stdin: str | None = None,
    ) -> tuple[int, str, str]:
        _ = harness
        return self._invoke_run(
            workdir,
            env,
            command=command,
            spec=spec,
            function=function,
            args=args,
            kwargs=kwargs,
            env_vars=env_vars,
            name=name,
            interactive=interactive,
            timeout=timeout,
            tags=tags,
            wait=wait,
            stdin=stdin,
        )

    def queue_alias_add(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
        target: str,
    ) -> tuple[int, str, str]:
        with _api_call_environment(workdir, env):
            rc, out, err = queue_cmd.alias_add_command(
                alias, target, spec_context=str(workdir)
            )
        return rc, out.strip(), err.strip()

    def queue_alias_remove(
        self,
        workdir: Path,
        env: Mapping[str, str],
        alias: str,
    ) -> tuple[int, str, str]:
        with _api_call_environment(workdir, env):
            rc, out, err = queue_cmd.alias_remove_command(
                alias, spec_context=str(workdir)
            )
        return rc, out.strip(), err.strip()

    def queue_write(
        self,
        workdir: Path,
        env: Mapping[str, str],
        queue_name: str,
        message: str,
    ) -> tuple[int, str, str]:
        with _api_call_environment(workdir, env):
            rc, out, err = queue_cmd.write_command(queue_name, message)
        return rc, out.strip(), err.strip()

    def status_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        *,
        all_tasks: bool = False,
    ) -> tuple[int, str]:
        with _api_call_environment(workdir, env):
            rc, payload = cmd_status(
                json_output=True,
                include_terminal=all_tasks,
                spec_context=str(workdir),
            )
        if rc != 0:
            raise RuntimeError(payload or "status command failed")
        return rc, (payload or "").strip()

    def result_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str]:
        with _api_call_environment(workdir, env):
            rc, payload = cmd_result(
                tid=tid,
                all_results=False,
                peek=False,
                timeout=timeout,
                stream=False,
                json_output=True,
                show_stderr=False,
                context_path=str(workdir),
            )
        if rc != 0:
            raise RuntimeError(payload or f"result command failed for {tid}")
        return rc, (payload or "").strip()

    def result_all_json(
        self,
        workdir: Path,
        env: Mapping[str, str],
    ) -> tuple[int, str]:
        with _api_call_environment(workdir, env):
            rc, payload = cmd_result(
                tid=None,
                all_results=True,
                peek=False,
                timeout=None,
                stream=False,
                json_output=True,
                show_stderr=False,
                context_path=str(workdir),
            )
        if rc != 0:
            raise RuntimeError(payload or "result --all failed")
        return rc, (payload or "").strip()

    def stop_task(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
    ) -> tuple[int, str, str]:
        with _api_call_environment(workdir, env):
            count = stop_tasks([tid], context_path=str(workdir))
        if count != 1:
            return 1, "", f"expected to stop 1 task, stopped {count}"
        return 0, "Stopped 1 task(s)", ""

    def stop_worker(
        self,
        workdir: Path,
        env: Mapping[str, str],
        tid: str,
        *,
        timeout: float,
    ) -> tuple[int, str, str]:
        with _api_call_environment(workdir, env):
            rc, payload = stop_command(
                tid=tid,
                force=False,
                timeout=timeout,
                context_path=workdir,
            )
        return rc, (payload or "").strip(), ""


def _status_payload(
    surface: SessionSurface,
    workdir: Path,
    env: Mapping[str, str],
    *,
    all_tasks: bool = False,
) -> dict[str, Any]:
    _rc, out = surface.status_json(workdir, env, all_tasks=all_tasks)
    return json.loads(out)


def _assert_single_active_manager(
    surface: SessionSurface,
    workdir: Path,
    env: Mapping[str, str],
) -> str:
    payload = _status_payload(surface, workdir, env)
    managers = payload["managers"]
    if len(managers) != 1:
        raise RuntimeError(f"Expected exactly one active manager, got {payload}")
    manager = managers[0]
    if manager["status"] != "active":
        raise RuntimeError(f"Expected active manager, got {payload}")
    return str(manager["tid"])


def _submit_no_wait_task(
    surface: SessionSurface,
    workdir: Path,
    harness: WeftTestHarness,
    env: Mapping[str, str],
    *,
    label: str,
    expected: str,
    name: str,
    tags: Sequence[str],
    command: Sequence[str] = (),
    function: str | None = None,
    args: Sequence[str] = (),
    kwargs: Sequence[str] = (),
    env_vars: Sequence[str] = (),
    timeout: float | None = None,
) -> SubmittedTask:
    rc, out, err = surface.run_task(
        workdir,
        harness,
        env,
        name=name,
        tags=tags,
        command=command,
        function=function,
        args=args,
        kwargs=kwargs,
        env_vars=env_vars,
        timeout=timeout,
        wait=False,
    )
    if rc != 0:
        raise RuntimeError(err or f"Task submission failed for {name}")
    tid = out.strip()
    if len(tid) != 19 or not tid.isdigit():
        raise RuntimeError(f"Unexpected task id payload for {name}: {out!r}")
    harness.register_tid(tid)
    return SubmittedTask(
        label=label,
        tid=tid,
        expected=expected,
        name=name,
        tags=tuple(tags),
    )


def _run_long_session(
    surface: SessionSurface,
    workdir: Path,
    harness: WeftTestHarness,
    env: Mapping[str, str],
) -> BenchmarkResult:
    command_script = workdir / "session_command.py"
    write_command_script(command_script)

    persistent_inbox = "session.persistent.jobs"
    alias_name = "session.jobs"
    alias_ref = f"@{alias_name}"
    persistent_spec_path = workdir / "session_persistent.json"
    write_persistent_spec(
        persistent_spec_path,
        workdir=workdir,
        inbox=persistent_inbox,
    )

    start = time.perf_counter()

    rc, out, err = surface.run_task(
        workdir,
        harness,
        env,
        name=None,
        tags=(),
        spec=persistent_spec_path,
        wait=False,
    )
    if rc != 0:
        raise RuntimeError(err or "failed to start persistent spec")
    persistent_tid = out.strip()
    harness.register_tid(persistent_tid)

    rc, out, err = surface.queue_alias_add(
        workdir,
        env,
        alias_name,
        persistent_inbox,
    )
    if rc != 0:
        raise RuntimeError(err or "failed to add alias")
    if out != "":
        raise RuntimeError(f"unexpected alias-add stdout: {out!r}")

    with harness.context.broker() as broker:
        aliases = dict(broker.list_aliases())
        if aliases.get(alias_name) != persistent_inbox:
            raise RuntimeError(f"alias add did not persist: {aliases}")

    manager_tid: str | None = None
    bulk_tasks: list[SubmittedTask] = []
    interactive_outputs: list[str] = []
    interactive_expected_outputs: list[str] = []
    persistent_results: list[str] = []
    persistent_expected: list[str] = []
    sample_tasks: list[SubmittedTask] = []

    for round_index in range(BULK_ROUNDS):
        function_label = f"fn-{round_index:03d}"
        function_task = _submit_no_wait_task(
            surface,
            workdir,
            harness,
            env,
            label=function_label,
            expected=f"{function_label}::fn",
            name=f"session-function-{round_index:03d}",
            tags=("kind:function", "session:long"),
            function="tests.tasks.sample_targets:echo_payload",
            args=(function_label,),
            kwargs=('suffix="::fn"',),
        )
        bulk_tasks.append(function_task)

        work_delay_ms = (round_index % 4) * 2
        work_delay = f"{work_delay_ms / 1000:.3f}"
        work_label = f"wrk-{round_index:03d}"
        work_expected = f"{work_label}@{work_delay_ms}ms"
        work_task = _submit_no_wait_task(
            surface,
            workdir,
            harness,
            env,
            label=work_label,
            expected=work_expected,
            name=f"session-work-{round_index:03d}",
            tags=("kind:work", "session:long"),
            function="tests.tasks.sample_targets:simulate_work",
            kwargs=(f"duration={work_delay}", f'result="{work_expected}"'),
            timeout=15.0,
        )
        bulk_tasks.append(work_task)

        command_delay_ms = (round_index % 5) * 3
        command_delay = f"{command_delay_ms / 1000:.3f}"
        command_label = f"cmd-{round_index:03d}"
        command_task = _submit_no_wait_task(
            surface,
            workdir,
            harness,
            env,
            label=command_label,
            expected=f"cmd:{command_label}:{command_delay_ms}",
            name=f"session-command-{round_index:03d}",
            tags=("kind:command", "session:long"),
            command=(
                sys.executable,
                str(command_script),
                command_label,
                command_delay,
            ),
            env_vars=("SESSION_PREFIX=cmd",),
            timeout=15.0,
        )
        bulk_tasks.append(command_task)

        if round_index == 0 or round_index == BULK_ROUNDS - 1:
            sample_tasks.extend([function_task, work_task, command_task])

        if round_index in {0, 31, 63, BULK_ROUNDS - 1}:
            current_manager_tid = _assert_single_active_manager(surface, workdir, env)
            if manager_tid is None:
                manager_tid = current_manager_tid
            elif current_manager_tid != manager_tid:
                raise RuntimeError(
                    f"manager changed from {manager_tid} to {current_manager_tid}"
                )

        if (round_index + 1) % ALIAS_INTERVAL == 0:
            alias_label = f"alias-{len(persistent_expected):02d}"
            expected_alias_result = f"<{alias_label}>"
            rc, out, err = surface.queue_write(
                workdir,
                env,
                alias_ref,
                json.dumps(
                    {
                        "args": [alias_label],
                        "kwargs": {"prefix": "<", "suffix": ">"},
                    }
                ),
            )
            if rc != 0 or out != "":
                raise RuntimeError(err or f"unexpected alias write stdout: {out!r}")

            _rc, payload_text = surface.result_json(
                workdir,
                env,
                persistent_tid,
                timeout=20.0,
            )
            payload = json.loads(payload_text)
            persistent_results.append(payload["result"])
            persistent_expected.append(expected_alias_result)

        if (round_index + 1) % INTERACTIVE_INTERVAL == 0:
            interactive_label = (
                f"interactive-{(round_index + 1) // INTERACTIVE_INTERVAL:02d}"
            )
            rc, out, err = surface.run_task(
                workdir,
                harness,
                env,
                name=f"session-{interactive_label}",
                tags=("kind:interactive", "session:long"),
                command=(
                    sys.executable,
                    "-u",
                    str(INTERACTIVE_SCRIPT),
                ),
                interactive=True,
                wait=True,
                stdin=f"{interactive_label}-a\n{interactive_label}-b\nquit\n",
            )
            if rc != 0:
                raise RuntimeError(
                    err or f"interactive task failed: {interactive_label}"
                )
            interactive_outputs.append(out)
            interactive_expected_outputs.append(interactive_expected(interactive_label))

    if manager_tid is None:
        raise RuntimeError("manager was never observed during long session")
    if len(persistent_results) != PERSISTENT_WORK_ITEMS:
        raise RuntimeError(
            f"persistent result count mismatch: {len(persistent_results)}"
        )
    if len(interactive_outputs) != BULK_ROUNDS // INTERACTIVE_INTERVAL:
        raise RuntimeError(
            f"interactive run count mismatch: {len(interactive_outputs)}"
        )

    statuses = wait_for_terminal_statuses(
        harness,
        {task.tid for task in bulk_tasks},
        timeout=SESSION_TIMEOUT_SECONDS,
    )
    if set(statuses.values()) != {"completed"}:
        raise RuntimeError(f"unexpected terminal statuses: {statuses}")

    time.sleep(WEFT_COMPLETED_RESULT_GRACE_SECONDS + 0.15)

    _rc, result_text = surface.result_all_json(workdir, env)
    result_payload = json.loads(result_text)
    result_map = {item["tid"]: item["result"] for item in result_payload["results"]}
    if set(result_map) != {task.tid for task in bulk_tasks}:
        raise RuntimeError("result --all payload did not cover all bulk tasks")

    actual_transcript = {
        "persistent": persistent_results,
        "interactive": interactive_outputs,
        "bulk": [
            {"label": task.label, "result": result_map[task.tid]}
            for task in sorted(bulk_tasks, key=lambda task: task.label)
        ],
    }
    expected_transcript = {
        "persistent": persistent_expected,
        "interactive": interactive_expected_outputs,
        "bulk": [
            {"label": task.label, "result": task.expected}
            for task in sorted(bulk_tasks, key=lambda task: task.label)
        ],
    }
    if actual_transcript != expected_transcript:
        raise RuntimeError("long-session transcript mismatch")

    all_status_payload = _status_payload(surface, workdir, env, all_tasks=True)
    task_records = {
        entry["tid"]: entry
        for entry in all_status_payload["tasks"]
        if isinstance(entry, dict) and "tid" in entry
    }
    for task in sample_tasks:
        record = task_records[task.tid]
        if record["name"] != task.name:
            raise RuntimeError(f"task name mismatch for {task.tid}: {record}")
        if record["metadata"]["tags"] != list(task.tags):
            raise RuntimeError(f"task tags mismatch for {task.tid}: {record}")

    rc, out, err = surface.stop_task(workdir, env, persistent_tid)
    if rc != 0:
        raise RuntimeError(err or f"failed to stop persistent task: {out}")

    persistent_statuses = wait_for_terminal_statuses(
        harness,
        {persistent_tid},
        timeout=30.0,
    )
    if persistent_statuses[persistent_tid] not in {"cancelled", "completed"}:
        raise RuntimeError(f"unexpected persistent status: {persistent_statuses}")

    with harness.context.broker() as broker:
        aliases = dict(broker.list_aliases())
        if aliases.get(alias_name) != persistent_inbox:
            raise RuntimeError(f"alias disappeared before removal: {aliases}")

    rc, out, err = surface.queue_alias_remove(workdir, env, alias_name)
    if rc != 0 or out != "":
        raise RuntimeError(err or f"failed to remove alias: {out}")

    with harness.context.broker() as broker:
        if alias_name in dict(broker.list_aliases()):
            raise RuntimeError(f"alias still present after removal: {alias_name}")

    rc, out, err = surface.stop_worker(workdir, env, manager_tid, timeout=10.0)
    if rc != 0:
        raise RuntimeError(err or f"failed to stop manager: {out}")

    final_status_payload = _status_payload(surface, workdir, env)
    if final_status_payload["managers"] != []:
        raise RuntimeError(f"managers still present after stop: {final_status_payload}")
    if final_status_payload["tasks"] != []:
        raise RuntimeError(f"tasks still present after stop: {final_status_payload}")

    elapsed = time.perf_counter() - start
    return BenchmarkResult(
        backend=env["BROKER_TEST_BACKEND"],
        surface=surface.name,
        elapsed_seconds=elapsed,
        bulk_task_count=len(bulk_tasks),
        interactive_count=len(interactive_outputs),
        persistent_result_count=len(persistent_results),
    )


def run_benchmarks(settings: BenchmarkSettings) -> list[BenchmarkResult]:
    """Run the configured long-session surface matrix."""

    settings.validate()
    surface_map: dict[str, SessionSurface] = {
        "cli": CliSurface(),
        "api": ApiSurface(),
    }
    results: list[BenchmarkResult] = []

    for backend in settings.backends:
        with _backend_env(backend, settings.pg_dsn) as backend_env:
            for surface_name in settings.surfaces:
                with WeftTestHarness() as harness:
                    env = session_env()
                    env.update(backend_env)
                    results.append(
                        _run_long_session(
                            surface_map[surface_name],
                            harness.root,
                            harness,
                            env,
                        )
                    )
    return results


def render_report(results: Sequence[BenchmarkResult]) -> str:
    """Render a compact human-readable report."""

    lines = [
        "Weft Long-Session Surface Benchmark",
        "",
        "Backend   Surface  Seconds   Bulk tasks  Interactive  Persistent",
        "--------  -------  --------  ----------  -----------  ----------",
    ]
    for result in results:
        lines.append(
            "  ".join(
                [
                    result.backend.ljust(8),
                    result.surface.ljust(7),
                    f"{result.elapsed_seconds:8.2f}",
                    str(result.bulk_task_count).rjust(10),
                    str(result.interactive_count).rjust(11),
                    str(result.persistent_result_count).rjust(10),
                ]
            )
        )

    by_backend: dict[str, dict[str, BenchmarkResult]] = {}
    for result in results:
        by_backend.setdefault(result.backend, {})[result.surface] = result

    comparisons: list[str] = []
    for backend, surface_map in sorted(by_backend.items()):
        cli_result = surface_map.get("cli")
        api_result = surface_map.get("api")
        if cli_result is None or api_result is None:
            continue
        ratio = cli_result.elapsed_seconds / api_result.elapsed_seconds
        comparisons.append(
            f"{backend}: cli/api = {ratio:.2f}x "
            f"({cli_result.elapsed_seconds:.2f}s vs {api_result.elapsed_seconds:.2f}s)"
        )

    if comparisons:
        lines.extend(["", "Surface Comparison"])
        lines.extend(comparisons)
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> BenchmarkSettings:
    parser = argparse.ArgumentParser(
        description="Measure the long-session workload through CLI and API surfaces."
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        default=[SQLITE_BACKEND],
        choices=[SQLITE_BACKEND, POSTGRES_TEST_BACKEND],
        help="Backends to benchmark",
    )
    parser.add_argument(
        "--surfaces",
        nargs="+",
        default=["api"],
        choices=list(SURFACES),
        help="Invocation surfaces to benchmark",
    )
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("SIMPLEBROKER_PG_TEST_DSN"),
        help="Postgres DSN used when --backends includes postgres",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit benchmark results as JSON",
    )
    args = parser.parse_args(argv)
    return BenchmarkSettings(
        backends=tuple(args.backends),
        surfaces=tuple(args.surfaces),
        pg_dsn=args.pg_dsn,
        json_output=bool(args.json),
    )


def main(argv: list[str] | None = None) -> int:
    settings = _parse_args(argv)
    try:
        results = run_benchmarks(settings)
    except Exception as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1

    if settings.json_output:
        payload = {
            "settings": asdict(settings),
            "results": [asdict(result) for result in results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_report(results))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual benchmark entry point
    raise SystemExit(main())
