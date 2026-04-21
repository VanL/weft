"""Broker-backed integration tests for the Django package."""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = PROJECT_ROOT / "integrations" / "weft_django"
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixture_project"
TEST_ROOT = Path(tempfile.mkdtemp(prefix="weft-django-tests-"))

for path in (PROJECT_ROOT, PACKAGE_ROOT, FIXTURE_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

pythonpath_parts = [str(PROJECT_ROOT), str(PACKAGE_ROOT), str(FIXTURE_ROOT)]
existing_pythonpath = os.environ.get("PYTHONPATH")
if existing_pythonpath:
    pythonpath_parts.append(existing_pythonpath)
os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fixture_project.settings")
os.environ.setdefault("WEFT_DJANGO_FIXTURE_BASE_DIR", str(TEST_ROOT))
os.environ.setdefault("WEFT_DJANGO_FIXTURE_DB_PATH", str(TEST_ROOT / "django.sqlite3"))
os.environ.setdefault("WEFT_DJANGO_FIXTURE_WEFT_CONTEXT", str(TEST_ROOT))

import django

django.setup()

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, override_settings
from fixture_project import authz as fixture_authz
from fixture_project import request_id_provider
from testapp.models import EventRecord
from testapp.weft_tasks import echo_current_request_id, echo_task

from weft.client import SpecNotFound
from weft.core.taskspec import TaskSpec
from weft_django import (
    WeftSubmission,
    enqueue_on_commit,
    submit_pipeline_reference,
    submit_pipeline_reference_on_commit,
    submit_spec_reference,
    submit_spec_reference_on_commit,
    submit_taskspec,
    submit_taskspec_on_commit,
)
from weft_django.client import get_core_client
from weft_django.conf import (
    get_realtime_transport,
    resolve_context_override,
)
from weft_django.registry import TaskRegistry, is_registered

call_command("migrate", run_syncdb=True, verbosity=0)


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    EventRecord.objects.all().delete()


def _fixture_weft_settings(**overrides: Any) -> dict[str, Any]:
    settings_dict: dict[str, Any] = {
        "CONTEXT": str(TEST_ROOT),
        "AUTHZ": "fixture_project.authz:authorize",
        "AUTODISCOVER_MODULE": "weft_tasks",
        "REQUEST_ID_PROVIDER": "fixture_project.request_id_provider:get_current",
        "DEFAULT_TASK": {
            "runner": "host",
            "timeout": None,
            "memory_mb": 256,
            "cpu_percent": None,
            "stream_output": False,
            "metadata": {},
        },
        "REALTIME": {
            "TRANSPORT": "sse",
        },
    }
    for key, value in overrides.items():
        if key in {"DEFAULT_TASK", "REALTIME"} and isinstance(value, dict):
            merged = dict(settings_dict[key])
            merged.update(value)
            settings_dict[key] = merged
            continue
        settings_dict[key] = value
    return settings_dict


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _native_taskspec() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "name": "native-echo",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(TEST_ROOT),
            },
            "metadata": {},
        },
        context={"template": True, "auto_expand": False},
    )


def _spec_reference_path(name: str = "native-reference") -> Path:
    return _write_json(
        TEST_ROOT / ".weft" / "tasks" / f"{name}.json",
        {
            "name": name,
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(TEST_ROOT),
            },
            "metadata": {},
        },
    )


def _pipeline_reference_path(
    *,
    pipeline_name: str = "native-pipeline",
    stage_name: str = "native-stage",
) -> Path:
    _spec_reference_path(stage_name)
    return _write_json(
        TEST_ROOT / ".weft" / "pipelines" / pipeline_name / "pipeline.json",
        {
            "name": pipeline_name,
            "stages": [{"name": "only", "task": stage_name}],
        },
    )


def _wait_for_snapshot(tid: str, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_core_client().task(tid).snapshot() is not None:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for snapshot for task {tid}")


@pytest.mark.shared
def test_registry_autodiscovers_fixture_tasks() -> None:
    assert is_registered("testapp.echo_task") is True
    assert is_registered("testapp.fetch_record_value") is True


@pytest.mark.shared
def test_task_registry_rejects_duplicate_names() -> None:
    registry = TaskRegistry()
    first = type("Registered", (), {"name": "dup", "callable_ref": "one:task"})()
    second = type("Registered", (), {"name": "dup", "callable_ref": "two:task"})()

    registry.register(first)

    with pytest.raises(RuntimeError, match="Duplicate weft task name"):
        registry.register(second)


@pytest.mark.shared
def test_decorated_task_enqueue_returns_richer_submission_handle() -> None:
    task = echo_task.enqueue("hello")
    assert isinstance(task, WeftSubmission)
    assert task.name == "testapp.echo_task"

    task_result = task.wait(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == "hello"
    assert task.status() == "completed"


@pytest.mark.shared
def test_submission_wrapper_stop_and_kill_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = echo_task.enqueue("delegation")
    stop_calls = {"count": 0}
    kill_calls = {"count": 0}

    def _stop(_self: Any) -> None:
        stop_calls["count"] += 1

    def _kill(_self: Any) -> None:
        kill_calls["count"] += 1

    monkeypatch.setattr(type(task.task), "stop", _stop)
    monkeypatch.setattr(type(task.task), "kill", _kill)

    task.stop()
    task.kill()

    assert stop_calls["count"] == 1
    assert kill_calls["count"] == 1


@pytest.mark.shared
def test_enqueue_on_commit_defers_submission_until_commit() -> None:
    with transaction.atomic():
        record = EventRecord.objects.create(key="k", value="from-db")
        deferred = enqueue_on_commit("testapp.fetch_record_value", record.id)
        assert deferred.task is None
        with pytest.raises(RuntimeError, match="transaction to commit"):
            deferred.result()

    assert deferred.task is not None
    task_result = deferred.task.result(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == "from-db"


@pytest.mark.shared
def test_enqueue_on_commit_rollbacks_do_not_bind() -> None:
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            deferred = echo_task.enqueue_on_commit("rollback")
            raise RuntimeError("rollback")

    assert deferred.task is None


@pytest.mark.shared
def test_deferred_submit_rejects_wait_true() -> None:
    spec_reference = _spec_reference_path("wait-spec")
    pipeline_reference = _pipeline_reference_path(
        pipeline_name="wait-pipeline",
        stage_name="wait-stage",
    )

    with pytest.raises(ValueError, match="wait=True"):
        enqueue_on_commit("testapp.echo_task", "x", _overrides={"wait": True})
    with pytest.raises(ValueError, match="wait=True"):
        submit_taskspec_on_commit(_native_taskspec(), payload="x", wait=True)
    with pytest.raises(ValueError, match="wait=True"):
        submit_spec_reference_on_commit(spec_reference, payload="x", wait=True)
    with pytest.raises(ValueError, match="wait=True"):
        submit_pipeline_reference_on_commit(pipeline_reference, payload="x", wait=True)


@pytest.mark.shared
def test_deferred_native_submission_validates_unknown_overrides_before_commit() -> None:
    with pytest.raises(TypeError, match="Unknown submit override"):
        with transaction.atomic():
            EventRecord.objects.create(key="bad-override", value="committed?")
            submit_taskspec_on_commit(_native_taskspec(), payload="x", bogus=True)

    assert not EventRecord.objects.filter(key="bad-override").exists()


@pytest.mark.shared
def test_deferred_spec_reference_validates_missing_reference_before_commit() -> None:
    with pytest.raises(SpecNotFound):
        with transaction.atomic():
            EventRecord.objects.create(key="bad-spec", value="committed?")
            submit_spec_reference_on_commit("missing-spec-reference", payload="x")

    assert not EventRecord.objects.filter(key="bad-spec").exists()


@pytest.mark.shared
def test_deferred_native_payload_is_snapshotted_at_registration() -> None:
    payload = {"value": "before"}

    with transaction.atomic():
        deferred = submit_taskspec_on_commit(_native_taskspec(), payload=payload)
        payload["value"] = "after"

    assert deferred.task is not None
    task_result = deferred.task.result(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == "{'value': 'before'}"


@pytest.mark.shared
def test_deferred_decorated_payload_is_snapshotted_at_registration() -> None:
    payload = {"value": "before"}

    with transaction.atomic():
        deferred = echo_task.enqueue_on_commit(payload)
        payload["value"] = "after"

    assert deferred.task is not None
    task_result = deferred.task.result(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == {"value": "before"}


@pytest.mark.shared
def test_native_taskspec_submission_helper_accepts_payload() -> None:
    task = submit_taskspec(_native_taskspec(), payload="native")
    task_result = task.result(timeout=30.0)

    assert isinstance(task, WeftSubmission)
    assert task.name == "native-echo"
    assert task_result.status == "completed"
    assert task_result.value == "native"


@pytest.mark.shared
def test_native_spec_and_pipeline_helpers_accept_payload() -> None:
    spec_reference = _spec_reference_path("payload-spec")
    pipeline_reference = _pipeline_reference_path(
        pipeline_name="payload-pipeline",
        stage_name="payload-stage",
    )

    spec_task = submit_spec_reference(spec_reference, payload="spec-input")
    pipeline_task = submit_pipeline_reference(pipeline_reference, payload="pipe-input")

    assert spec_task.result(timeout=30.0).value == "spec-input"
    assert pipeline_task.result(timeout=30.0).value == "pipe-input"


@pytest.mark.shared
def test_native_helpers_reject_legacy_payload_names() -> None:
    spec_reference = _spec_reference_path("legacy-spec")
    pipeline_reference = _pipeline_reference_path(
        pipeline_name="legacy-pipeline",
        stage_name="legacy-stage",
    )

    with pytest.raises(TypeError, match="payload=.*work_payload"):
        submit_taskspec(_native_taskspec(), work_payload="x")
    with pytest.raises(TypeError, match="payload=.*input"):
        submit_spec_reference(spec_reference, input="x")
    with pytest.raises(TypeError, match="payload=.*input"):
        submit_pipeline_reference(pipeline_reference, input="x")


@pytest.mark.shared
def test_request_id_is_captured_at_enqueue_time_for_on_commit_submission() -> None:
    request_id_provider.set_current("req-abc-123")
    with transaction.atomic():
        deferred = echo_current_request_id.enqueue_on_commit()
        request_id_provider.set_current(None)

    assert deferred.task is not None
    task_result = deferred.task.result(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == "req-abc-123"


@pytest.mark.shared
def test_as_taskspec_for_call_applies_public_submit_overrides() -> None:
    payload = echo_task.as_taskspec_for_call(
        "hello",
        _overrides={
            "description": "override description",
            "tags": ("one", "two"),
            "memory_mb": 512,
            "cpu_percent": 25,
        },
    )

    assert payload["metadata"]["description"] == "override description"
    assert payload["metadata"]["tags"] == ["one", "two"]
    assert payload["spec"]["limits"]["memory_mb"] == 512
    assert payload["spec"]["limits"]["cpu_percent"] == 25


@pytest.mark.shared
def test_context_override_ignores_unrelated_weft_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = TEST_ROOT / "base-dir-context"
    other_dir = TEST_ROOT / "other-dir-context"
    base_dir.mkdir(parents=True, exist_ok=True)
    other_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEFT_DEBUG", "1")
    monkeypatch.chdir(other_dir)

    with override_settings(
        BASE_DIR=base_dir,
        WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None),
    ):
        assert resolve_context_override() == str(base_dir)
        assert get_core_client().context.root == base_dir.resolve()


@pytest.mark.shared
def test_explicit_context_wins_even_with_core_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_root = TEST_ROOT / "explicit-context"
    explicit_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEFT_BACKEND_TARGET", "postgresql://ignored")

    with override_settings(
        WEFT_DJANGO=_fixture_weft_settings(CONTEXT=str(explicit_root))
    ):
        assert resolve_context_override() == str(explicit_root)


@pytest.mark.shared
def test_http_detail_and_sse_views_are_read_only_diagnostics() -> None:
    task = echo_task.enqueue("http")
    _wait_for_snapshot(task.tid)
    client = Client()

    detail_response = client.get(f"/weft/tasks/{task.tid}/")
    assert detail_response.status_code == 200
    assert detail_response.json()["name"] == "testapp.echo_task"

    stream_response = client.get(f"/weft/tasks/{task.tid}/events/")
    assert stream_response.status_code == 200
    chunks = b"".join(stream_response.streaming_content).decode("utf-8")
    assert "event: snapshot" in chunks
    assert "event: state" in chunks
    assert "event: result" in chunks
    assert "event: end" in chunks
    assert task.result(timeout=30.0).value == "http"


@pytest.mark.shared
def test_sse_stream_emits_stdout_and_stderr_events_without_consuming_result() -> None:
    core_task = get_core_client().submit_command(
        [
            sys.executable,
            "-c",
            "import sys; print('stdout-line'); print('stderr-line', file=sys.stderr)",
        ],
        stream_output=True,
    )
    _wait_for_snapshot(core_task.tid)

    client = Client()
    stream_response = client.get(f"/weft/tasks/{core_task.tid}/events/")
    assert stream_response.status_code == 200
    chunks = b"".join(stream_response.streaming_content).decode("utf-8")

    assert "event: stdout" in chunks
    assert "stdout-line" in chunks
    assert "event: stderr" in chunks
    assert "stderr-line" in chunks
    assert "event: result" in chunks
    assert "event: end" in chunks

    task_result = core_task.result(timeout=30.0)
    assert task_result.status == "completed"


@pytest.mark.shared
def test_http_views_handle_invalid_and_unknown_tids_cleanly() -> None:
    client = Client()
    unknown_tid = str(time.time_ns() + 1_000_000)

    assert client.get("/weft/tasks/not-a-tid/").status_code == 404
    assert client.get("/weft/tasks/not-a-tid/events/").status_code == 404
    assert client.get(f"/weft/tasks/{unknown_tid}/").status_code == 404
    assert client.get(f"/weft/tasks/{unknown_tid}/events/").status_code == 404


@pytest.mark.shared
def test_http_views_enforce_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    task = echo_task.enqueue("authz")
    _wait_for_snapshot(task.tid)
    monkeypatch.setattr(fixture_authz, "authorize", lambda request, tid, action: False)

    client = Client()
    assert client.get(f"/weft/tasks/{task.tid}/").status_code == 403
    assert client.get(f"/weft/tasks/{task.tid}/events/").status_code == 403


@pytest.mark.shared
def test_task_events_view_respects_transport_setting() -> None:
    task = echo_task.enqueue("transport")
    _wait_for_snapshot(task.tid)

    with override_settings(
        WEFT_DJANGO=_fixture_weft_settings(REALTIME={"TRANSPORT": "none"})
    ):
        client = Client()
        assert client.get(f"/weft/tasks/{task.tid}/events/").status_code == 404


@pytest.mark.shared
def test_url_import_requires_authz_setting() -> None:
    sys.modules.pop("weft_django.urls", None)
    with override_settings(WEFT_DJANGO=_fixture_weft_settings(AUTHZ=None)):
        with pytest.raises(ImproperlyConfigured, match="WEFT_DJANGO\\['AUTHZ'\\]"):
            importlib.import_module("weft_django.urls")


@pytest.mark.shared
def test_transport_validation() -> None:
    with override_settings(
        WEFT_DJANGO=_fixture_weft_settings(REALTIME={"TRANSPORT": "bogus"})
    ):
        with pytest.raises(ImproperlyConfigured, match="TRANSPORT"):
            get_realtime_transport()


@pytest.mark.shared
def test_channels_module_matches_install_state() -> None:
    sys.modules.pop("weft_django.channels", None)
    try:
        import channels  # noqa: F401
    except ImportError:
        with pytest.raises(ImproperlyConfigured, match="channels' extra"):
            importlib.import_module("weft_django.channels")
    else:
        module = importlib.import_module("weft_django.channels")
        assert hasattr(module, "websocket_urlpatterns")


@pytest.mark.shared
def test_channels_connect_returns_before_follow_stream_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("weft_django.channels", None)
    try:
        module = importlib.import_module("weft_django.channels")
    except ImproperlyConfigured:
        pytest.skip("Channels extra is not installed")

    accepted = threading.Event()
    started = threading.Event()
    stopped = threading.Event()
    sent: list[dict[str, Any]] = []

    class FakeTask:
        tid = "1776000000000000001"

        def snapshot(self) -> object:
            return object()

    class FakeCoreClient:
        def task(self, tid: str) -> FakeTask:
            assert tid == FakeTask.tid
            return FakeTask()

    def _payloads(
        task: FakeTask,
        *,
        follow: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        assert follow is True
        started.set()
        try:
            while cancel_event is None or not cancel_event.is_set():
                yield {
                    "tid": task.tid,
                    "event_type": "state",
                    "timestamp": 1,
                    "payload": {"status": "running"},
                }
                time.sleep(0.01)
        finally:
            stopped.set()

    async def _accept(self: Any) -> None:
        accepted.set()

    async def _send_json(self: Any, payload: dict[str, Any]) -> None:
        sent.append(payload)

    async def _close(self: Any, code: int | None = None) -> None:
        del code

    monkeypatch.setattr(module, "get_realtime_transport", lambda: "channels")
    monkeypatch.setattr(module, "_authorize_scope", lambda scope, tid, action: True)
    monkeypatch.setattr(module, "get_core_client", lambda: FakeCoreClient())
    monkeypatch.setattr(module, "iter_task_event_payloads", _payloads)
    monkeypatch.setattr(module.TaskEventsConsumer, "accept", _accept)
    monkeypatch.setattr(module.TaskEventsConsumer, "send_json", _send_json)
    monkeypatch.setattr(module.TaskEventsConsumer, "close", _close)

    async def _run() -> None:
        consumer = module.TaskEventsConsumer()
        consumer.scope = {
            "path": f"/ws/weft/tasks/{FakeTask.tid}/",
            "url_route": {"kwargs": {"tid": FakeTask.tid}},
        }

        await consumer.connect()

        assert accepted.is_set()
        assert consumer._stream_task is not None
        assert await asyncio.to_thread(started.wait, 1.0)
        assert not consumer._stream_task.done()

        await consumer.disconnect(1000)

        assert await asyncio.to_thread(stopped.wait, 1.0)
        assert sent

    asyncio.run(_run())


@pytest.mark.shared
def test_management_commands_wrap_the_same_client_surface() -> None:
    task = echo_task.enqueue("management")
    assert task.result(timeout=30.0).status == "completed"

    stdout = io.StringIO()
    call_command("weft_task_status", task.tid, stdout=stdout)
    assert task.tid in stdout.getvalue()

    missing_stdout = io.StringIO()
    with pytest.raises(CommandError):
        call_command("weft_task_status", "9999999999999999999", stdout=missing_stdout)

    with pytest.raises(CommandError, match="Failed to stop"):
        call_command("weft_task_stop", "1776000000000000001")
    with pytest.raises(CommandError, match="Failed to kill"):
        call_command("weft_task_kill", "1776000000000000001")
