"""Parity tests for command runners across host and external runtimes."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_TID_MAPPINGS_QUEUE
from weft._runner_plugins import require_runner_plugin
from weft.commands import status as status_cmd
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core.launcher import launch_task_process
from weft.core.tasks import Consumer
from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import (
    IOSection,
    RunnerSection,
    SpecSection,
    StateSection,
    TaskSpec,
)
from weft.helpers import kill_process_tree

pytestmark = [pytest.mark.shared]

DOCKER_HELLO_IMAGE = "hello-world"
DOCKER_PYTHON_IMAGE = "python:3.13-alpine"
RUNNER_NAMES = ("host", "docker", "macos-sandbox")


@pytest.fixture
def sandbox_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "allow-default.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    return profile


def _write_probe_script(tmp_path: Path) -> Path:
    script = tmp_path / "runner_probe.py"
    script.write_text(
        """
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    print(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "stdin": sys.stdin.read(),
                "cwd": os.getcwd(),
                "env": os.environ.get("WEFT_PARITY_MARKER"),
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


def _skip_unavailable_runner(
    runner_name: str,
    *,
    sandbox_profile: Path | None = None,
    image: str | None = None,
) -> None:
    if runner_name == "host":
        return

    try:
        plugin = require_runner_plugin(runner_name)
    except RuntimeError as exc:
        pytest.skip(str(exc))

    options: dict[str, Any] = {}
    process_target = sys.executable
    if runner_name == "docker":
        options["image"] = image or DOCKER_PYTHON_IMAGE
        process_target = "python3"
    elif runner_name == "macos-sandbox":
        assert sandbox_profile is not None
        options["profile"] = str(sandbox_profile)

    try:
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "process_target": process_target,
                    "runner": {
                        "name": runner_name,
                        "options": options,
                    },
                }
            },
            preflight=True,
        )
    except Exception as exc:
        pytest.skip(f"{runner_name} runner unavailable: {exc}")


def _runner_command(
    runner_name: str,
    *,
    sandbox_profile: Path,
    process_target: str | None = None,
    args: list[str] | None = None,
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
) -> TaskRunner:
    if runner_name == "host":
        target = process_target or sys.executable
        runner_options: dict[str, Any] = {}
    elif runner_name == "docker":
        target = process_target or "python3"
        runner_options = {"image": DOCKER_PYTHON_IMAGE}
    elif runner_name == "macos-sandbox":
        target = process_target or sys.executable
        runner_options = {"profile": str(sandbox_profile)}
    else:  # pragma: no cover - test guard
        raise AssertionError(f"Unsupported runner {runner_name}")

    return TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target=target,
        agent=None,
        args=args,
        kwargs=None,
        env=env or {},
        working_dir=working_dir,
        timeout=timeout,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        runner_name=runner_name,
        runner_options=runner_options,
    )


def _drain(queue: Queue) -> list[str]:
    items: list[str] = []
    while True:
        raw = queue.read_one()
        if raw is None:
            break
        items.append(raw)
    return items


def _build_consumer_spec(
    tid: str,
    *,
    runner_name: str,
    sandbox_profile: Path,
    working_dir: Path,
) -> TaskSpec:
    if runner_name == "host":
        process_target = sys.executable
        runner_options: dict[str, Any] = {}
    elif runner_name == "docker":
        process_target = "python3"
        runner_options = {"image": DOCKER_PYTHON_IMAGE}
    elif runner_name == "macos-sandbox":
        process_target = sys.executable
        runner_options = {"profile": str(sandbox_profile)}
    else:  # pragma: no cover - test guard
        raise AssertionError(f"Unsupported runner {runner_name}")

    return TaskSpec(
        tid=tid,
        name=f"{runner_name}-command",
        spec=SpecSection(
            type="command",
            process_target=process_target,
            args=["runner_probe.py", "--base-arg"],
            timeout=20.0,
            working_dir=str(working_dir),
            env={"WEFT_PARITY_MARKER": f"marker-{runner_name}"},
            runner=RunnerSection(name=runner_name, options=runner_options),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _build_long_running_command_spec(
    tid: str,
    *,
    runner_name: str,
    sandbox_profile: Path,
    working_dir: Path,
) -> TaskSpec:
    if runner_name == "host":
        process_target = sys.executable
        runner_options: dict[str, Any] = {}
    elif runner_name == "docker":
        process_target = "python3"
        runner_options = {"image": DOCKER_PYTHON_IMAGE}
    elif runner_name == "macos-sandbox":
        process_target = sys.executable
        runner_options = {"profile": str(sandbox_profile)}
    else:  # pragma: no cover - test guard
        raise AssertionError(f"Unsupported runner {runner_name}")

    return TaskSpec(
        tid=tid,
        name=f"{runner_name}-long-running",
        spec=SpecSection(
            type="command",
            process_target=process_target,
            args=["-c", "import time; time.sleep(30)"],
            timeout=60.0,
            working_dir=str(working_dir),
            runner=RunnerSection(name=runner_name, options=runner_options),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _wait_for_running_snapshot(
    root: Path,
    tid: str,
    *,
    timeout: float = 15.0,
) -> status_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: status_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            if (
                snapshot.status == "running"
                and snapshot.runtime_handle is not None
                and snapshot.runtime is not None
            ):
                return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for running snapshot with runtime handle for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _wait_for_terminal_snapshot(
    root: Path,
    tid: str,
    *,
    expected_status: str,
    timeout: float = 15.0,
) -> status_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: status_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            if snapshot.status == expected_status:
                return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for {expected_status} snapshot for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _wait_for_process_exit(process: Any, *, timeout: float = 10.0) -> bool:
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        join = getattr(process, "join", None)
        if callable(join):
            join(timeout=0.05)
        exitcode = getattr(process, "exitcode", None)
        if exitcode is not None:
            return True
        is_alive = getattr(process, "is_alive", None)
        if callable(is_alive) and not bool(is_alive()):
            return True
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


def _launch_running_task(
    tmp_path: Path,
    *,
    runner_name: str,
    sandbox_profile: Path,
) -> tuple[Path, Any, str]:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = _build_long_running_command_spec(
        tid,
        runner_name=runner_name,
        sandbox_profile=sandbox_profile,
        working_dir=root,
    )
    process = launch_task_process(Consumer, ctx.broker_target, spec, config=ctx.config)
    inbox = ctx.queue(spec.io.inputs["inbox"], persistent=True)
    inbox.write(json.dumps({}))
    _wait_for_running_snapshot(root, tid)
    return root, process, tid


def test_docker_runner_executes_hello_world_image(sandbox_profile: Path) -> None:
    _skip_unavailable_runner(
        "docker",
        sandbox_profile=sandbox_profile,
        image=DOCKER_HELLO_IMAGE,
    )
    runner = TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="/hello",
        agent=None,
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=20.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        runner_name="docker",
        runner_options={"image": DOCKER_HELLO_IMAGE},
    )

    outcome = runner.run({})

    assert outcome.ok
    assert outcome.returncode == 0
    assert "Hello from Docker!" in (outcome.stdout or "")
    assert outcome.runtime_handle is not None
    assert outcome.runtime_handle.runner_name == "docker"


def test_docker_runner_collects_metrics_and_classifies_memory_limit_violation(
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner("docker", sandbox_profile=sandbox_profile)
    runner = TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="python3",
        agent=None,
        args=["-c", "x=[]\nwhile True: x.append('x' * 1024 * 1024)"],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=20.0,
        limits=SpecSection.model_validate(
            {
                "type": "command",
                "process_target": "python3",
                "limits": {"memory_mb": 64},
            }
        ).limits,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="docker",
        runner_options={"image": DOCKER_PYTHON_IMAGE},
    )

    outcome = runner.run({})

    assert outcome.status == "limit"
    assert outcome.error is not None
    assert "memory" in outcome.error.lower()
    assert outcome.metrics is not None
    assert outcome.metrics.memory_mb >= 0


def test_docker_runner_enforces_nofile_limit_with_ulimit(
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner("docker", sandbox_profile=sandbox_profile)
    runner = TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="python3",
        agent=None,
        args=[
            "-c",
            (
                "import json, resource; "
                "soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE); "
                "print(json.dumps({'soft': soft, 'hard': hard}))"
            ),
        ],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=20.0,
        limits=SpecSection.model_validate(
            {
                "type": "command",
                "process_target": "python3",
                "limits": {"max_fds": 64},
            }
        ).limits,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="docker",
        runner_options={"image": DOCKER_PYTHON_IMAGE},
    )

    outcome = runner.run({})

    assert outcome.ok
    payload = json.loads(outcome.value)
    assert payload == {"soft": 64, "hard": 64}


def test_docker_runner_disables_network_when_max_connections_is_zero(
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner("docker", sandbox_profile=sandbox_profile)
    runner = TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="python3",
        agent=None,
        args=[
            "-c",
            (
                "from pathlib import Path; "
                "print(Path('/proc/net/route').read_text(), end='')"
            ),
        ],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=20.0,
        limits=SpecSection.model_validate(
            {
                "type": "command",
                "process_target": "python3",
                "limits": {"max_connections": 0},
            }
        ).limits,
        monitor_class=None,
        monitor_interval=0.1,
        runner_name="docker",
        runner_options={"image": DOCKER_PYTHON_IMAGE},
    )

    outcome = runner.run({})

    assert outcome.ok
    assert outcome.value.splitlines() == [
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
    ]


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_command_runners_execute_python_probe_equivalently(
    runner_name: str,
    tmp_path: Path,
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    script = _write_probe_script(tmp_path)
    runner = _runner_command(
        runner_name,
        sandbox_profile=sandbox_profile,
        args=[script.name, "--base-arg"],
        env={"WEFT_PARITY_MARKER": f"marker-{runner_name}"},
        working_dir=str(tmp_path.resolve()),
        timeout=20.0,
    )

    outcome = runner.run(
        {
            "args": ["--work-item-arg"],
            "stdin": "hello parity",
        }
    )

    assert outcome.ok
    payload = json.loads(outcome.value)
    assert payload["argv"] == ["--base-arg", "--work-item-arg"]
    assert payload["stdin"] == "hello parity"
    assert payload["env"] == f"marker-{runner_name}"
    assert payload["cwd"] == str(tmp_path.resolve())
    assert outcome.runtime_handle is not None
    assert outcome.runtime_handle.runner_name == runner_name


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_command_runners_report_nonzero_exit_equivalently(
    runner_name: str,
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    process_target = "python3" if runner_name == "docker" else sys.executable
    runner = _runner_command(
        runner_name,
        sandbox_profile=sandbox_profile,
        process_target=process_target,
        args=["-c", "import sys; raise SystemExit(3)"],
        timeout=20.0,
    )

    outcome = runner.run({})

    assert outcome.status == "error"
    assert outcome.returncode == 3


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_command_runners_timeout_equivalently(
    runner_name: str,
    sandbox_profile: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    process_target = "python3" if runner_name == "docker" else sys.executable
    runner = _runner_command(
        runner_name,
        sandbox_profile=sandbox_profile,
        process_target=process_target,
        args=["-c", "import time; time.sleep(5)"],
        timeout=0.3,
    )

    outcome = runner.run({})

    assert outcome.status == "timeout"
    assert outcome.error == "Target execution timed out"


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_consumer_command_runners_share_basic_lifecycle(
    runner_name: str,
    broker_env,
    sandbox_profile: Path,
    tmp_path: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    broker_target, make_queue = broker_env
    _write_probe_script(tmp_path)
    tid = str(time.time_ns())
    spec = _build_consumer_spec(
        tid,
        runner_name=runner_name,
        sandbox_profile=sandbox_profile,
        working_dir=tmp_path,
    )
    task = Consumer(broker_target, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    _drain(log_queue)
    _drain(mapping_queue)

    try:
        inbox.write(
            json.dumps(
                {
                    "args": ["--work-item-arg"],
                    "stdin": "hello lifecycle",
                }
            )
        )

        task.process_once()

        assert task.taskspec.state.status == "completed"

        outbox_payload = json.loads(outbox.read_one() or "{}")
        assert outbox_payload["argv"] == ["--base-arg", "--work-item-arg"]
        assert outbox_payload["stdin"] == "hello lifecycle"
        assert outbox_payload["env"] == f"marker-{runner_name}"

        events = [json.loads(item) for item in _drain(log_queue)]
        event_names = [event["event"] for event in events]
        assert "work_spawning" in event_names
        assert "work_started" in event_names
        assert "work_completed" in event_names

        mappings = [json.loads(item) for item in _drain(mapping_queue)]
        assert mappings
        latest = mappings[-1]
        assert latest["runner"] == runner_name
        if runner_name == "host":
            assert latest["runtime_handle"]["runner_name"] == "host"
        else:
            assert latest["runtime_handle"]["runner_name"] == runner_name
    finally:
        task.stop(join=False)


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_running_command_runners_surface_runner_status(
    runner_name: str,
    sandbox_profile: Path,
    tmp_path: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    root, process, tid = _launch_running_task(
        tmp_path,
        runner_name=runner_name,
        sandbox_profile=sandbox_profile,
    )
    try:
        snapshot = _wait_for_running_snapshot(root, tid)
        assert snapshot.runner == runner_name
        assert snapshot.runtime_handle is not None
        assert snapshot.runtime_handle["runner_name"] == runner_name
        assert snapshot.runtime is not None
        assert snapshot.runtime["runner_name"] == runner_name
        assert snapshot.runtime["state"] == "running"
        if runner_name == "docker":
            metadata = snapshot.runtime["metadata"]
            assert metadata["image"] == DOCKER_PYTHON_IMAGE
            assert isinstance(metadata.get("container_id"), str)
            assert metadata["container_id"]
            assert "memory_usage_mb" in metadata

        snapshots = task_cmd.list_tasks(context_path=root)
        entry = next(item for item in snapshots if item.tid == tid)
        assert entry.runner == runner_name
        assert entry.runtime is not None
        assert entry.runtime["state"] == "running"
    finally:
        task_cmd.stop_tasks([tid], context_path=root)
        process.join(timeout=10)
        if process.is_alive():
            kill_process_tree(process.pid or -1)


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_command_runners_stop_cancel_equivalently(
    runner_name: str,
    sandbox_profile: Path,
    tmp_path: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    root, process, tid = _launch_running_task(
        tmp_path,
        runner_name=runner_name,
        sandbox_profile=sandbox_profile,
    )
    try:
        stopped = task_cmd.stop_tasks([tid], context_path=root)
        assert stopped == 1
        snapshot = _wait_for_terminal_snapshot(root, tid, expected_status="cancelled")
        assert snapshot.runner == runner_name
        assert _wait_for_process_exit(process, timeout=10)
    finally:
        if process.pid and not _wait_for_process_exit(process, timeout=0.1):
            kill_process_tree(process.pid)


@pytest.mark.parametrize("runner_name", RUNNER_NAMES)
def test_command_runners_kill_mark_killed_equivalently(
    runner_name: str,
    sandbox_profile: Path,
    tmp_path: Path,
) -> None:
    _skip_unavailable_runner(runner_name, sandbox_profile=sandbox_profile)
    root, process, tid = _launch_running_task(
        tmp_path,
        runner_name=runner_name,
        sandbox_profile=sandbox_profile,
    )
    try:
        killed = task_cmd.kill_tasks([tid], context_path=root)
        assert killed == 1
        snapshot = _wait_for_terminal_snapshot(root, tid, expected_status="killed")
        assert snapshot.runner == runner_name
        assert _wait_for_process_exit(process, timeout=10)
    finally:
        if process.pid and not _wait_for_process_exit(process, timeout=0.1):
            kill_process_tree(process.pid)
