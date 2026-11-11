"""Tests for the standalone manager-process entry point."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
import warnings
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Config, serialize_config
from tests.helpers.weft_harness import WeftTestHarness
from weft import manager_process
from weft._constants import (
    CONTROL_STOP,
    WEFT_SERVICES_REGISTRY_QUEUE,
    load_config,
    resolve_runtime_config,
)
from weft.client import WeftClient
from weft.core import manager_runtime as core_manager_runtime
from weft.core.control_messages import encode_control_message
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.taskspec import (
    TaskSpec,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)

pytestmark = [pytest.mark.shared]


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _encoded_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _args() -> list[str]:
    broker_target = json.dumps(
        {
            "backend_name": "sqlite",
            "target": "/tmp/weft-manager-process-test.db",
            "backend_options": {},
        }
    )
    return [
        "manager.path",
        _encoded(broker_target),
        _encoded("{}"),
        _encoded(serialize_config(load_config())),
        "0.1",
    ]


@pytest.mark.parametrize("failure", [OSError("unreadable cwd")])
def test_main_renders_supported_manager_argument_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise failure

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    assert manager_process.main(_args()) == 2
    assert capsys.readouterr().err == f"Invalid manager arguments: {failure}\n"


@pytest.mark.parametrize(
    ("argument_index", "malformed_value"),
    [
        (1, "a"),
        (1, _encoded_bytes(b"\xff")),
        (1, _encoded("{")),
        (2, "a"),
        (2, _encoded_bytes(b"\xff")),
        (3, "a"),
        (3, _encoded_bytes(b"\xff")),
        (4, "not-a-float"),
    ],
)
def test_main_renders_each_malformed_manager_argument_family(
    capsys: pytest.CaptureFixture[str],
    argument_index: int,
    malformed_value: str,
) -> None:
    args = _args()
    args[argument_index] = malformed_value

    assert manager_process.main(args) == 2
    assert capsys.readouterr().err.startswith("Invalid manager arguments: ")


def test_main_propagates_unexpected_manager_argument_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise RuntimeError("unexpected deserialize defect")

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    with pytest.raises(RuntimeError, match="unexpected deserialize defect"):
        manager_process.main(_args())


def test_main_propagates_manager_argument_type_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_deserialize(_payload: str) -> object:
        raise TypeError("unexpected deserialize type defect")

    monkeypatch.setattr(manager_process, "deserialize_broker_target", fail_deserialize)

    with pytest.raises(TypeError, match="unexpected deserialize type defect"):
        manager_process.main(_args())


def _valid_spec() -> TaskSpec:
    return validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "manager",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }
    )


@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    [
        ("malformed-json", "Invalid manager config:"),
        ("non-object", "object envelope"),
        ("invalid-field", "WEFT_TASK_MONITOR_BATCH_SIZE"),
        ("ambiguous-postgres", "Postgres backend configuration is ambiguous"),
    ],
)
def test_main_rejects_invalid_config_before_running_manager(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_kind: str,
    expected: str,
) -> None:
    config_payload = json.loads(serialize_config(load_config()))
    if failure_kind == "malformed-json":
        config_json = "{"
    elif failure_kind == "non-object":
        config_json = "[]"
    else:
        values = config_payload["values"]
        if failure_kind == "invalid-field":
            values["TASK_MONITOR_BATCH_SIZE"] = 0
        else:
            values.update(
                {
                    "BACKEND": "postgres",
                    "BACKEND_TARGET": "postgresql://user@target.example/db",
                    "BACKEND_HOST": "other.example",
                }
            )
        config_json = json.dumps(config_payload)
    args = _args()
    args[2] = _encoded(json.dumps(encode_taskspec_transport_payload(_valid_spec())))
    args[3] = _encoded(config_json)
    calls: list[object] = []

    def capture_run(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(manager_process, "run_manager_process", capture_run)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert manager_process.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Invalid manager config: ")
    assert expected in captured.err
    assert "Traceback" not in captured.err
    assert calls == []


def test_main_propagates_unexpected_config_resolver_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolve(_payload: str) -> Config:
        raise RuntimeError("unexpected config resolver defect")

    monkeypatch.setattr(manager_process, "resolve_runtime_config", fail_resolve)
    with pytest.raises(RuntimeError, match="unexpected config resolver defect"):
        manager_process.main(_args())


@pytest.mark.parametrize(
    "failure",
    [TypeError("invalid config value type"), ValueError("invalid config value")],
)
def test_main_renders_supported_config_resolver_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    def fail_resolve(_payload: str) -> Config:
        raise failure

    monkeypatch.setattr(manager_process, "resolve_runtime_config", fail_resolve)
    assert manager_process.main(_args()) == 2
    assert capsys.readouterr().err == f"Invalid manager config: {failure}\n"


def test_main_preserves_parent_config_over_ambient_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config({"WEFT_CACHE_MB": 17, "WEFT_TASK_MONITOR_BATCH_SIZE": 37})
    args = _args()
    args[2] = _encoded(json.dumps(encode_taskspec_transport_payload(_valid_spec())))
    args[3] = _encoded(serialize_config(config))
    monkeypatch.setenv("WEFT_CACHE_MB", "23")
    monkeypatch.setenv("WEFT_TASK_MONITOR_BATCH_SIZE", "53")
    captured: list[Mapping[str, Any] | None] = []

    def capture_run(
        _task_cls_path: str,
        _broker_target: object,
        _spec: TaskSpec,
        decoded: Mapping[str, Any] | None,
        _poll_interval: float | None,
        *,
        hard_exit_on_return: bool,
    ) -> None:
        assert hard_exit_on_return is True
        captured.append(decoded)

    monkeypatch.setattr(manager_process, "run_manager_process", capture_run)
    assert manager_process.main(args) == 0
    decoded = captured[0]
    assert isinstance(decoded, Config)
    assert decoded.prefix == "WEFT"
    assert decoded["CACHE_MB"] == 17
    assert decoded["TASK_MONITOR_BATCH_SIZE"] == 37


@pytest.mark.parametrize(
    ("token", "expected"),
    [("none", None), ("0.125", 0.125)],
)
def test_main_preserves_explicit_poll_interval_transport(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    expected: float | None,
) -> None:
    args = _args()
    args[2] = _encoded(json.dumps(encode_taskspec_transport_payload(_valid_spec())))
    args[4] = token
    captured: list[float | None] = []

    def capture_run(
        _task_cls_path: str,
        _broker_target: object,
        _spec: TaskSpec,
        _config: Mapping[str, Any] | None,
        poll_interval: float | None,
        *,
        hard_exit_on_return: bool,
    ) -> None:
        assert hard_exit_on_return is True
        captured.append(poll_interval)

    monkeypatch.setattr(manager_process, "run_manager_process", capture_run)

    assert manager_process.main(args) == 0
    assert captured == [expected]


def test_main_renders_invalid_taskspec(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    assert manager_process.main(_args()) == 2
    assert capsys.readouterr().err.startswith("Invalid manager TaskSpec: ")


def test_main_propagates_unexpected_taskspec_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    def fail_validate(_payload: object) -> object:
        raise RuntimeError("unexpected validation defect")

    monkeypatch.setattr(
        manager_process,
        "decode_taskspec_transport_payload",
        fail_validate,
    )

    with pytest.raises(RuntimeError, match="unexpected validation defect"):
        manager_process.main(_args())


def test_main_decodes_canonical_taskspec_transport_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "manager-bundle"
    bundle_root.mkdir()
    taskspec = validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "manager",
            "spec": {
                "type": "function",
                "function_target": "manager_bundle:run",
            },
        },
        bundle_root=bundle_root,
    )
    args = _args()
    args[2] = _encoded(json.dumps(encode_taskspec_transport_payload(taskspec)))
    captured: list[TaskSpec] = []
    monkeypatch.setattr(
        manager_process,
        "deserialize_broker_target",
        lambda _payload: object(),
    )

    def capture_run(
        _task_cls_path: str,
        _broker_target: object,
        spec: TaskSpec,
        _config: object,
        _poll_interval: float | None,
        *,
        hard_exit_on_return: bool,
    ) -> None:
        assert hard_exit_on_return is True
        captured.append(spec)

    monkeypatch.setattr(manager_process, "run_manager_process", capture_run)

    assert manager_process.main(args) == 0
    decoded = captured[0]
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert "_weft_bundle_root" not in decoded.model_dump(mode="json")


def test_run_manager_process_preserves_bundle_provenance_at_task_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "foreground-manager-bundle"
    bundle_root.mkdir()
    taskspec = validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "manager",
            "spec": {
                "type": "function",
                "function_target": "manager_bundle:run",
            },
        },
        bundle_root=bundle_root,
    )
    captured_json: list[str] = []
    captured_config: list[str] = []

    def capture_entry(
        _task_cls_path: str,
        _broker_target: object,
        spec_json: str,
        config_json: str,
        _poll_interval: float | None,
        _hard_exit_on_return: bool,
    ) -> None:
        captured_json.append(spec_json)
        captured_config.append(config_json)

    monkeypatch.setattr(manager_process, "_task_process_entry", capture_entry)
    monkeypatch.setenv("WEFT_CACHE_MB", "23")

    manager_process.run_manager_process(
        "weft.core.manager.Manager",
        "unused.db",
        taskspec,
        {"CACHE_MB": 17, "TASK_MONITOR_BATCH_SIZE": 37},
        0.1,
        hard_exit_on_return=True,
    )

    payload = json.loads(captured_json[0])
    assert payload["_weft_bundle_root"] == str(bundle_root.resolve())
    decoded = manager_process.decode_taskspec_transport_payload(payload)
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert "_weft_bundle_root" not in taskspec.model_dump(mode="json")
    assert isinstance(captured_config[0], str)
    config = resolve_runtime_config(captured_config[0])
    assert config.prefix == "WEFT"
    assert config["CACHE_MB"] == 17
    assert config["TASK_MONITOR_BATCH_SIZE"] == 37


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGSTOP drain hold")
def test_submission_during_manager_stop_drain_starts_replacement_without_stopping_new_work(
    tmp_path: Path,
) -> None:
    """Work submitted while M1 is draining starts M2 and completes while M1 is still draining ([WT-2a.14], [MA-1.4])."""
    ready_file = tmp_path / "hold-ready.txt"
    release_file = tmp_path / "hold-release.txt"
    hold_script = tmp_path / "hold_drain.py"
    hold_script.write_text(
        (
            "import os, time\n"
            "from pathlib import Path\n"
            f"ready = Path({str(ready_file)!r})\n"
            f"release = Path({str(release_file)!r})\n"
            "partial = ready.with_suffix('.tmp')\n"
            "partial.write_text(str(os.getppid()), encoding='utf-8')\n"
            "partial.replace(ready)\n"
            "while not release.exists():\n"
            "    time.sleep(0.05)\n"
        ),
        encoding="utf-8",
    )

    with WeftTestHarness() as harness:
        m1_record, _started, m1_proc = core_manager_runtime.start_manager(
            harness.context
        )
        m1_tid = str(m1_record["tid"])
        harness.register_manager_tid(m1_tid)
        client = WeftClient(harness.context)
        consumer_pid: int | None = None
        try:
            hold_task = client.submit_command([sys.executable, "-u", str(hold_script)])
            harness.register_tid(hold_task.tid)
            deadline = time.monotonic() + 15.0
            while not ready_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready_file.exists()
            consumer_pid = int(ready_file.read_text(encoding="utf-8").strip())
            os.kill(consumer_pid, signal.SIGSTOP)

            with harness.context.queue(
                f"T{m1_tid}.ctrl_in", persistent=False
            ) as ctrl_in:
                ctrl_in.write(encode_control_message(CONTROL_STOP))

            with harness.context.queue(
                WEFT_SERVICES_REGISTRY_QUEUE, persistent=True
            ) as registry:
                monitor = QueueChangeMonitor([registry], config=harness.context.config)
                try:
                    drain_deadline = time.monotonic() + 2.0
                    m1_status = None
                    while time.monotonic() < drain_deadline:
                        snap = core_manager_runtime._snapshot_registry(
                            harness.context, prune_stale=False
                        )
                        rec = snap.get(m1_tid)
                        m1_status = rec.get("status") if rec is not None else None
                        if m1_status == "draining":
                            break
                        monitor.wait(max(0.0, drain_deadline - time.monotonic()))
                    assert m1_status == "draining"
                finally:
                    monitor.close()

            new_task = client.submit_command(
                [sys.executable, "-c", "print('DURING_DRAIN_OK')"]
            )
            harness.register_tid(new_task.tid)
            res = new_task.result(timeout=10.0)
            assert res.status == "completed"
            assert "DURING_DRAIN_OK" in (res.stdout or str(res.value))

            follow_task = client.submit_command(
                [sys.executable, "-c", "print('FOLLOW_DRAIN_OK')"]
            )
            harness.register_tid(follow_task.tid)
            events = list(follow_task.follow(timeout=10.0))
            assert any(ev.event_type == "result" for ev in events)

            snap_after = core_manager_runtime._snapshot_registry(
                harness.context, prune_stale=False
            )
            for mgr_tid in snap_after:
                harness.register_manager_tid(str(mgr_tid))
            assert snap_after.get(m1_tid, {}).get("status") == "draining"
            assert any(
                tid != m1_tid and rec.get("status") == "active"
                for tid, rec in snap_after.items()
            )
        finally:
            release_file.write_text("release", encoding="utf-8")
            if consumer_pid is not None:
                with suppress(OSError):
                    os.kill(consumer_pid, signal.SIGCONT)
            if m1_proc is not None and m1_proc.poll() is None:
                with suppress(subprocess.TimeoutExpired, OSError):
                    m1_proc.wait(timeout=5.0)
