"""Broker-backed integration tests for the Django package.

Fixture ownership: docs/specifications/08-Testing_Strategy.md [TS-0].
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from asgiref.testing import ApplicationCommunicator

if TYPE_CHECKING:
    from weft.context import WeftContext

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = PROJECT_ROOT / "integrations" / "weft_django"
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixture_project"

for path in (PROJECT_ROOT, PACKAGE_ROOT, FIXTURE_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from tests.helpers.test_backend import (
    active_test_backend,
    cleanup_prepared_roots,
    postgres_env_overrides_for_root,
    prepare_project_root,
)
from tests.helpers.weft_harness import WeftTestHarness

# Django captures these paths during setup. Allocate the owner now, but enter
# it only in the module fixture so collection never patches process state.
_HARNESS = WeftTestHarness()
TEST_ROOT = _HARNESS.root

pythonpath_parts = [str(PROJECT_ROOT), str(PACKAGE_ROOT), str(FIXTURE_ROOT)]
existing_pythonpath = os.environ.get("PYTHONPATH")
if existing_pythonpath:
    pythonpath_parts.append(existing_pythonpath)
os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fixture_project.settings")
os.environ["WEFT_DJANGO_FIXTURE_BASE_DIR"] = str(TEST_ROOT)
os.environ["WEFT_DJANGO_FIXTURE_DB_PATH"] = str(TEST_ROOT / "django.sqlite3")
os.environ["WEFT_DJANGO_FIXTURE_WEFT_CONTEXT"] = str(TEST_ROOT)

import django

django.setup()

from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.signals import request_finished, request_started
from django.db import connections, transaction
from django.test import AsyncClient, Client, TestCase, override_settings
from fixture_project import authz as fixture_authz
from fixture_project import lifecycle_views, request_id_provider
from testapp.models import EventRecord
from testapp.weft_tasks import declared_task, echo_current_request_id, echo_task

import weft.commands.submission as submission_commands
import weft_django
import weft_django.client as weft_django_client
from weft._constants import (
    SUBMIT_OVERRIDE_NAMES,
    WEFT_CONFIG_FIELDS,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.client import SpecNotFound, SubmissionValidationError, WeftClient
from weft.commands.types import TaskTerminalSnapshot
from weft.core.manager_runtime import ManagerEnsureResult
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import validate_taskspec_payload
from weft_django import (
    DjangoWeftClient,
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
from weft_django.conf import get_realtime_transport
from weft_django.registry import TaskRegistry, is_registered

weft_django_sse = importlib.import_module("weft_django.sse")

pytestmark = [pytest.mark.shared]

_bootstrap_context: WeftContext


@pytest.fixture(scope="module", autouse=True)
def _owned_runtime() -> Iterator[None]:
    """Close Django connections and owned runtimes before removing their root."""
    global _bootstrap_context
    with _HARNESS:
        try:
            _bootstrap_context = _HARNESS.context
            queue = _bootstrap_context.queue("weft.test.bootstrap", persistent=False)
            try:
                queue.generate_timestamp()
            finally:
                queue.close()
            call_command("migrate", run_syncdb=True, verbosity=0)
            yield
        finally:
            connections.close_all()


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    EventRecord.objects.all().delete()


@pytest.fixture(autouse=True)
def _owned_project_roots(tmp_path: Path) -> Iterator[None]:
    """Release PostgreSQL schemas used by context-only temporary projects."""

    try:
        yield
    finally:
        cleanup_prepared_roots(tmp_path)


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


_LEGACY_CONTEXT_SUFFIXES = (
    "BACKEND",
    "BACKEND_TARGET",
    "BACKEND_HOST",
    "BACKEND_PORT",
    "BACKEND_USER",
    "BACKEND_PASSWORD",
    "BACKEND_DATABASE",
    "BACKEND_SCHEMA",
    "DEFAULT_DB_LOCATION",
    "DEFAULT_DB_NAME",
    "PROJECT_SCOPE",
)


def _clear_context_selection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate external context inputs while retaining test-runtime policy."""

    for prefix in ("WEFT_", "BROKER_"):
        for suffix in _LEGACY_CONTEXT_SUFFIXES:
            if (
                prefix == "WEFT_"
                and suffix == "BACKEND_PASSWORD"
                and active_test_backend() == "postgres"
            ):
                # PG fixtures persist a passwordless target; the runner supplies
                # authentication separately from the context-selection inputs.
                continue
            monkeypatch.delenv(prefix + suffix, raising=False)
    for suffix in (
        "CONTEXT",
        "DIRECTORY_NAME",
        "PROJECT_CONFIG_PATH",
        "PROJECT_CONFIG_NAME",
    ):
        monkeypatch.delenv("WEFT_" + suffix, raising=False)


def _deferred_family(
    family: str,
    *,
    root: Path,
    declared_context: str | None = None,
    payload: Any = "captured",
) -> weft_django_client.WeftDeferredSubmission:
    """Prepare each public transaction helper through its real shared owner."""

    if family == "decorated":
        return enqueue_on_commit("testapp.echo_task", payload)
    template = {
        "name": "custody-task",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": declared_context,
        },
    }
    if family == "native":
        return submit_taskspec_on_commit(template, payload=payload)
    reference = _write_json(root / ".weft" / "tasks" / "custody-task.json", template)
    if family == "reference":
        return submit_spec_reference_on_commit(reference, payload=payload)
    assert family == "pipeline"
    pipeline = _write_json(
        root / "custody-pipeline.json",
        {
            "name": "custody-pipeline",
            "stages": [{"name": "only", "task": "custody-task"}],
        },
    )
    return submit_pipeline_reference_on_commit(pipeline, payload=payload)


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


def _ready_manager_result() -> ManagerEnsureResult:
    return ManagerEnsureResult(
        outcome="ready",
        manager_record=None,
        started_here=False,
        process_handle=None,
        reason="lifecycle-test-ready",
    )


@pytest.mark.parametrize(
    "path", ["/lifecycle/sync-burst/", "/lifecycle/sync-on-commit/"]
)
def test_sync_wsgi_burst_reuses_one_request_client(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    created: list[DjangoWeftClient] = []
    entered_threads: list[threading.Thread] = []
    closed_threads: list[threading.Thread] = []
    original_get_client = weft_django_client.get_client
    original_enter = DjangoWeftClient.__enter__
    original_close = DjangoWeftClient.close

    def tracked_get_client() -> DjangoWeftClient:
        client = original_get_client()
        created.append(client)
        return client

    def tracked_enter(client: DjangoWeftClient) -> DjangoWeftClient:
        entered_threads.append(threading.current_thread())
        return original_enter(client)

    def tracked_close(client: DjangoWeftClient) -> None:
        closed_threads.append(threading.current_thread())
        original_close(client)

    monkeypatch.setattr(weft_django_client, "get_client", tracked_get_client)
    monkeypatch.setattr(DjangoWeftClient, "__enter__", tracked_enter)
    monkeypatch.setattr(DjangoWeftClient, "close", tracked_close)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    lifecycle_views.REQUEST_THREADS.clear()

    response = Client().get(path)

    assert response.status_code == 200
    tids = response.json()["tids"]
    for tid in tids:
        _HARNESS.register_tid(tid)
    assert len(created) == 1
    assert entered_threads == lifecycle_views.REQUEST_THREADS
    assert closed_threads == lifecycle_views.REQUEST_THREADS


def test_direct_async_view_submissions_remain_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[DjangoWeftClient] = []
    entered: list[DjangoWeftClient] = []
    original_get_client = weft_django_client.get_client
    original_enter = DjangoWeftClient.__enter__

    def tracked_get_client() -> DjangoWeftClient:
        client = original_get_client()
        created.append(client)
        return client

    def tracked_enter(client: DjangoWeftClient) -> DjangoWeftClient:
        entered.append(client)
        return original_enter(client)

    monkeypatch.setattr(weft_django_client, "get_client", tracked_get_client)
    monkeypatch.setattr(DjangoWeftClient, "__enter__", tracked_enter)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )

    response = asyncio.run(AsyncClient().get("/lifecycle/async-burst/"))

    assert response.status_code == 200
    tids = response.json()["tids"]
    for tid in tids:
        _HARNESS.register_tid(tid)
    assert len(created) == 2
    assert created[0] is not created[1]
    assert entered == []


def test_streaming_response_retains_client_until_response_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[DjangoWeftClient] = []
    closed_threads: list[threading.Thread] = []
    original_get_client = weft_django_client.get_client
    original_close = DjangoWeftClient.close

    def tracked_get_client() -> DjangoWeftClient:
        client = original_get_client()
        created.append(client)
        return client

    def tracked_close(client: DjangoWeftClient) -> None:
        closed_threads.append(threading.current_thread())
        original_close(client)

    monkeypatch.setattr(weft_django_client, "get_client", tracked_get_client)
    monkeypatch.setattr(DjangoWeftClient, "close", tracked_close)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    lifecycle_views.REQUEST_THREADS.clear()

    response = Client().get("/lifecycle/streaming-burst/")
    assert response.streaming
    assert len(created) == 1
    assert closed_threads == []

    body = b"".join(response.streaming_content)
    assert len(created) == 1

    for tid in json.loads(body)["tids"]:
        _HARNESS.register_tid(tid)
    assert len(lifecycle_views.REQUEST_THREADS) == 2
    assert lifecycle_views.REQUEST_THREADS[0] is lifecycle_views.REQUEST_THREADS[1]
    assert closed_threads == [lifecycle_views.REQUEST_THREADS[0]]


def test_exception_response_closes_request_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered_threads: list[threading.Thread] = []
    closed_threads: list[threading.Thread] = []
    original_enter = DjangoWeftClient.__enter__
    original_close = DjangoWeftClient.close

    def tracked_enter(client: DjangoWeftClient) -> DjangoWeftClient:
        entered_threads.append(threading.current_thread())
        return original_enter(client)

    def tracked_close(client: DjangoWeftClient) -> None:
        closed_threads.append(threading.current_thread())
        original_close(client)

    monkeypatch.setattr(DjangoWeftClient, "__enter__", tracked_enter)
    monkeypatch.setattr(DjangoWeftClient, "close", tracked_close)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    lifecycle_views.REQUEST_THREADS.clear()

    response = Client(raise_request_exception=False).get("/lifecycle/exception-burst/")

    assert response.status_code == 500
    assert entered_threads == lifecycle_views.REQUEST_THREADS
    assert closed_threads == lifecycle_views.REQUEST_THREADS


def test_production_asgi_sync_view_uses_one_owner_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_threads: dict[str, list[threading.Thread]] = {
        "started": [],
        "finished": [],
    }
    entered_threads: list[threading.Thread] = []
    closed_threads: list[threading.Thread] = []
    original_enter = DjangoWeftClient.__enter__
    original_close = DjangoWeftClient.close

    def record_started(sender: object, **kwargs: Any) -> None:
        del sender, kwargs
        signal_threads["started"].append(threading.current_thread())

    def record_finished(sender: object, **kwargs: Any) -> None:
        del sender, kwargs
        signal_threads["finished"].append(threading.current_thread())

    def tracked_enter(client: DjangoWeftClient) -> DjangoWeftClient:
        entered_threads.append(threading.current_thread())
        return original_enter(client)

    def tracked_close(client: DjangoWeftClient) -> None:
        closed_threads.append(threading.current_thread())
        original_close(client)

    monkeypatch.setattr(DjangoWeftClient, "__enter__", tracked_enter)
    monkeypatch.setattr(DjangoWeftClient, "close", tracked_close)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    lifecycle_views.REQUEST_THREADS.clear()
    request_started.connect(
        record_started, dispatch_uid="weft-test-asgi-started", weak=False
    )
    request_finished.connect(
        record_finished, dispatch_uid="weft-test-asgi-finished", weak=False
    )

    async def exercise() -> list[dict[str, Any]]:
        communicator = ApplicationCommunicator(
            ASGIHandler(),
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/lifecycle/sync-burst/",
                "raw_path": b"/lifecycle/sync-burst/",
                "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            },
        )
        await communicator.send_input(
            {"type": "http.request", "body": b"", "more_body": False}
        )
        messages: list[dict[str, Any]] = []
        while True:
            message = await communicator.receive_output(timeout=10)
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                break
        await communicator.wait(timeout=10)
        return messages

    try:
        messages = asyncio.run(exercise())
    finally:
        request_started.disconnect(dispatch_uid="weft-test-asgi-started")
        request_finished.disconnect(dispatch_uid="weft-test-asgi-finished")

    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    for tid in json.loads(body)["tids"]:
        _HARNESS.register_tid(tid)
    assert len(signal_threads["started"]) == 1
    assert len(signal_threads["finished"]) == 1
    owner = lifecycle_views.REQUEST_THREADS[0]
    assert signal_threads["started"] == [owner]
    assert signal_threads["finished"] == [owner]
    assert entered_threads == [owner]
    assert closed_threads == [owner]


def test_concurrent_production_asgi_requests_isolate_clients_and_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[DjangoWeftClient] = []
    entered: list[tuple[DjangoWeftClient, threading.Thread]] = []
    closed: list[tuple[DjangoWeftClient, threading.Thread]] = []
    original_get_client = weft_django_client.get_client
    original_enter = DjangoWeftClient.__enter__
    original_close = DjangoWeftClient.close

    def tracked_get_client() -> DjangoWeftClient:
        client = original_get_client()
        created.append(client)
        return client

    def tracked_enter(client: DjangoWeftClient) -> DjangoWeftClient:
        entered.append((client, threading.current_thread()))
        return original_enter(client)

    def tracked_close(client: DjangoWeftClient) -> None:
        closed.append((client, threading.current_thread()))
        original_close(client)

    monkeypatch.setattr(weft_django_client, "get_client", tracked_get_client)
    monkeypatch.setattr(DjangoWeftClient, "__enter__", tracked_enter)
    monkeypatch.setattr(DjangoWeftClient, "close", tracked_close)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    lifecycle_views.REQUEST_THREADS.clear()
    lifecycle_views.CONCURRENT_BARRIER = threading.Barrier(2)

    async def request_once(port: int) -> str:
        communicator = ApplicationCommunicator(
            ASGIHandler(),
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/lifecycle/concurrent-on-commit/",
                "raw_path": b"/lifecycle/concurrent-on-commit/",
                "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "client": ("127.0.0.1", port),
                "server": ("testserver", 80),
            },
        )
        await communicator.send_input(
            {"type": "http.request", "body": b"", "more_body": False}
        )
        body = b""
        while True:
            message = await communicator.receive_output(timeout=10)
            if message["type"] == "http.response.body":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        await communicator.wait(timeout=10)
        return str(json.loads(body)["tids"][0])

    async def exercise() -> tuple[str, str]:
        first, second = await asyncio.gather(request_once(12001), request_once(12002))
        return first, second

    try:
        tids = asyncio.run(exercise())
    finally:
        lifecycle_views.CONCURRENT_BARRIER = None

    for tid in tids:
        _HARNESS.register_tid(tid)
    assert len(created) == 2
    assert created[0] is not created[1]
    assert len(lifecycle_views.REQUEST_THREADS) == 2
    assert lifecycle_views.REQUEST_THREADS[0] is not lifecycle_views.REQUEST_THREADS[1]
    assert {client for client, _thread in entered} == set(created)
    assert {client for client, _thread in closed} == set(created)
    assert {thread for _client, thread in entered} == set(
        lifecycle_views.REQUEST_THREADS
    )
    assert {thread for _client, thread in closed} == set(
        lifecycle_views.REQUEST_THREADS
    )


def test_callbacks_captured_after_response_close_use_bounded_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission_states: list[str] = []
    original_submit_prepared = WeftClient._submit_prepared

    def tracked_submit_prepared(client: WeftClient, prepared: Any) -> Any:
        submission_states.append(client._lifecycle_state.name)
        return original_submit_prepared(client, prepared)

    monkeypatch.setattr(WeftClient, "_submit_prepared", tracked_submit_prepared)
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )

    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=True) as callbacks:
            response = Client().get("/lifecycle/sync-on-commit/")
            assert response.status_code == 200
        deferred_handles = [
            inspect.getclosurevars(callback).nonlocals["deferred"]
            for callback in callbacks
        ]
        transaction.set_rollback(True)

    assert len(callbacks) == 2
    assert submission_states == ["BOUNDED", "BOUNDED"]
    for deferred in deferred_handles:
        assert deferred.task is not None
        _HARNESS.register_tid(deferred.task.tid)


def test_settings_rotation_keeps_prepared_callbacks_on_captured_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager_result(),
    )
    template = {
        "name": "settings-rotation",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    with (
        WeftTestHarness() as first_root,
        WeftTestHarness() as second_root,
        override_settings(WEFT_DJANGO=_fixture_weft_settings(CONTEXT=first_root.root)),
    ):
        request_started.send(sender=object())
        try:
            with transaction.atomic():
                first = submit_taskspec_on_commit(template, payload="first")
                with override_settings(
                    WEFT_DJANGO=_fixture_weft_settings(CONTEXT=second_root.root)
                ):
                    second = submit_taskspec_on_commit(template, payload="second")
            assert first.task is not None
            assert second.task is not None
            first_root.register_tid(first.task.tid)
            second_root.register_tid(second.task.tid)
            assert first.task.task.context is not None
            assert second.task.task.context is not None
            assert first.task.task.context.root == first_root.root.resolve()
            assert second.task.task.context.root == second_root.root.resolve()
            assert (
                WeftClient(path=second_root.root).task(first.task.tid).snapshot()
                is None
            )
            assert (
                WeftClient(path=first_root.root).task(second.task.tid).snapshot()
                is None
            )
        finally:
            request_finished.send(sender=object())


def test_public_facades_route_through_request_client_selector() -> None:
    direct_facades = {
        "submit_registered_task",
        "submit_registered_task_on_commit",
        "submit_taskspec",
        "submit_taskspec_on_commit",
        "submit_spec_reference",
        "submit_spec_reference_on_commit",
        "submit_pipeline_reference",
        "submit_pipeline_reference_on_commit",
        "status",
        "terminal_snapshot",
        "snapshot",
        "result",
        "stop",
        "kill",
    }
    for name in direct_facades:
        source = inspect.getsource(getattr(weft_django_client, name))
        assert "_current_client()" in source, name

    assert "submit_registered_task(" in inspect.getsource(weft_django_client.enqueue)
    assert "submit_registered_task_on_commit(" in inspect.getsource(
        weft_django_client.enqueue_on_commit
    )


def test_status_uses_terminal_snapshot_not_diagnostic_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, bool] = {}

    class FakeTask:
        def terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot:
            observed["terminal_snapshot"] = True
            assert timeout == 0.0
            return TaskTerminalSnapshot(
                tid="1770000000000000000",
                status="completed",
                source="outbox",
                terminal=True,
            )

        def snapshot(self) -> object:
            raise AssertionError("diagnostic snapshot should not be used")

    class FakeClient:
        def task(self, tid: str) -> FakeTask:
            assert tid == "1770000000000000000"
            return FakeTask()

    monkeypatch.setattr(weft_django_client, "get_core_client", lambda: FakeClient())

    snapshot = weft_django.status("1770000000000000000")

    assert snapshot is not None
    assert snapshot.status == "completed"
    assert observed == {"terminal_snapshot": True}


def test_django_client_snapshot_preserves_invalid_tid_none_contract() -> None:
    class InvalidTidCore:
        def task(self, _tid: str) -> Any:
            raise ValueError("invalid tid")

    core: Any = InvalidTidCore()
    assert DjangoWeftClient(core).snapshot("bad") is None


def test_status_returns_monitor_store_terminal_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTask:
        def terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot:
            assert timeout == 0.0
            return TaskTerminalSnapshot(
                tid="1770000000000000001",
                status="failed",
                source="monitor_store",
                terminal=True,
                error="retired failure",
                metadata={"classification": "terminal_monitor_store"},
            )

        def snapshot(self) -> object:
            raise AssertionError("status should not use diagnostic snapshot")

    class FakeClient:
        def task(self, tid: str) -> FakeTask:
            assert tid == "1770000000000000001"
            return FakeTask()

    monkeypatch.setattr(weft_django_client, "get_core_client", lambda: FakeClient())

    snapshot = weft_django.status("1770000000000000001")

    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.source == "monitor_store"
    assert snapshot.error == "retired failure"
    assert snapshot.metadata["classification"] == "terminal_monitor_store"


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
    with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
        deferred = echo_task.enqueue_on_commit("rollback")
        raise RuntimeError("rollback")

    assert deferred.task is None


def test_readiness_degradation_in_middle_commit_callback_preserves_all_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness-only degradation binds its TID and does not stop later callbacks."""
    calls = 0

    def _availability(*_args: object, **_kwargs: object) -> ManagerEnsureResult:
        nonlocal calls
        calls += 1
        return ManagerEnsureResult(
            outcome="uncertain" if calls == 2 else "ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="manager_start_failed:read-only" if calls == 2 else "ready",
        )

    monkeypatch.setattr(
        submission_commands,
        "ensure_manager_after_submission",
        _availability,
    )
    with (
        WeftTestHarness() as harness,
        override_settings(WEFT_DJANGO=_fixture_weft_settings(CONTEXT=harness.root)),
    ):
        with transaction.atomic():
            deferred = [echo_task.enqueue_on_commit(str(index)) for index in range(3)]
            assert all(item.task is None for item in deferred)

        assert calls == 3
        assert all(item.task is not None for item in deferred)
        tids = [item.task.tid for item in deferred if item.task is not None]
        queue = harness.context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
        try:
            assert all(
                queue.peek_one(exact_timestamp=int(tid)) is not None for tid in tids
            )
        finally:
            queue.close()


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
    with (
        pytest.raises(TypeError, match="Unknown submit override"),
        transaction.atomic(),
    ):
        EventRecord.objects.create(key="bad-override", value="committed?")
        submit_taskspec_on_commit(_native_taskspec(), payload="x", bogus=True)

    assert not EventRecord.objects.filter(key="bad-override").exists()


@pytest.mark.shared
def test_deferred_spec_reference_validates_missing_reference_before_commit() -> None:
    with pytest.raises(SpecNotFound), transaction.atomic():
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
def test_native_spec_helper_executes_declared_spec_args() -> None:
    spec_reference = _write_json(
        TEST_ROOT / ".weft" / "tasks" / "declared-args.json",
        {
            "name": "declared-args",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(TEST_ROOT),
                "run_input": {
                    "adapter_ref": "weft.builtins.run_input:arguments_payload",
                    "arguments": {"prompt": {"type": "string"}},
                },
            },
            "metadata": {},
        },
    )

    task = submit_spec_reference(
        spec_reference,
        spec_args=("--prompt", "hello"),
    )

    assert task.result(timeout=30.0).value == "{'prompt': 'hello'}"


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


@pytest.mark.parametrize("suffix", (*_LEGACY_CONTEXT_SUFFIXES, "UNKNOWN_SETTING"))
def test_legacy_broker_env_does_not_redirect_django_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    _clear_context_selection_env(monkeypatch)
    base_dir = prepare_project_root(tmp_path / "django")
    cwd = prepare_project_root(tmp_path / "cwd")
    monkeypatch.chdir(cwd)
    with override_settings(
        BASE_DIR=base_dir, WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None)
    ):
        expected = get_core_client().context
        monkeypatch.setenv("BROKER_" + suffix, "ignored-invalid-value")
        actual = get_core_client().context

    assert actual.root == base_dir
    assert actual.broker_target == expected.broker_target
    assert actual.config == expected.config
    queue = actual.queue("weft.test.django.context", persistent=False)
    try:
        queue.write(suffix)
        assert queue.read() == suffix
    finally:
        queue.close()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEFT_DEBUG", "1"),
        ("WEFT_BACKEND", "sqlite"),
        ("WEFT_PROJECT_SCOPE", "0"),
        ("WEFT_PROJECT_SCOPE", "1"),
        ("WEFT_DEFAULT_DB_LOCATION", "/unused-broker-location"),
        ("WEFT_DEFAULT_DB_NAME", "configured.db"),
    ],
)
def test_broker_settings_do_not_replace_django_fallback_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    _clear_context_selection_env(monkeypatch)
    base_dir = prepare_project_root(tmp_path / "django")
    cwd = prepare_project_root(tmp_path / "cwd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv(name, value)
    with override_settings(
        BASE_DIR=base_dir, WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None)
    ):
        context = get_core_client().context

    assert context.root == base_dir
    if name == "WEFT_DEFAULT_DB_NAME" and context.database_path is not None:
        assert context.database_path.name == value


@pytest.mark.parametrize("explicit", [None, "", "string", "path"])
def test_django_requests_core_context_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    explicit: str | None,
) -> None:
    _clear_context_selection_env(monkeypatch)
    declared = prepare_project_root(tmp_path / "explicit")
    environment = prepare_project_root(tmp_path / "environment")
    fallback = prepare_project_root(tmp_path / "fallback")
    cwd = prepare_project_root(tmp_path / "cwd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("WEFT_CONTEXT", str(environment))
    setting = (
        declared
        if explicit == "path"
        else str(declared)
        if explicit == "string"
        else explicit
    )

    with override_settings(
        BASE_DIR=fallback, WEFT_DJANGO=_fixture_weft_settings(CONTEXT=setting)
    ):
        context = get_core_client().context

    assert context.root == (declared if explicit else environment)


@pytest.mark.parametrize("has_base_dir", [False, True])
def test_django_discovery_starts_at_base_dir_or_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    has_base_dir: bool,
) -> None:
    _clear_context_selection_env(monkeypatch)
    django_project = prepare_project_root(tmp_path / "django")
    cwd_project = prepare_project_root(tmp_path / "cwd")
    for root in (django_project, cwd_project):
        config_file = root / ".weft" / "broker.toml"
        if not config_file.exists():
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                'version = 1\nbackend = "sqlite"\ntarget = '
                + json.dumps(str(root / "project.db"))
                + "\n",
                encoding="utf-8",
            )
        (root / "web").mkdir()
    monkeypatch.chdir(cwd_project / "web")
    with override_settings(
        BASE_DIR=django_project / "web" if has_base_dir else None,
        WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None),
    ):
        context = get_core_client().context
    assert context.root == (django_project if has_base_dir else cwd_project)


@pytest.mark.parametrize("declared", [None, "relative-project", "~/project"])
def test_export_copies_only_explicit_context_declaration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared: str | None,
) -> None:
    _clear_context_selection_env(monkeypatch)
    monkeypatch.setenv("WEFT_CONTEXT", str(tmp_path / "environment"))
    with override_settings(
        BASE_DIR=tmp_path, WEFT_DJANGO=_fixture_weft_settings(CONTEXT=declared)
    ):
        exported = echo_task.as_taskspec_for_call("portable")
    assert exported["spec"]["weft_context"] == declared
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("family", ["decorated", "native", "reference", "pipeline"])
def test_deferred_families_keep_captured_context_after_settings_change(
    monkeypatch: pytest.MonkeyPatch,
    family: str,
) -> None:
    _HARNESS.ensure_foreground_manager()
    with WeftTestHarness() as other, monkeypatch.context() as runtime_env:
        other.ensure_foreground_manager()
        other_client = WeftClient.from_weft_context(other.context)
        # The old bridge can choose CWD; own that runtime too before any assertion.
        _write_json(
            other.root / ".weft" / "tasks" / "custody-task.json",
            {
                "name": "custody-task",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
        )
        runtime_env.delenv("WEFT_CONTEXT", raising=False)
        runtime_env.setenv("WEFT_BUSY_TIMEOUT", "3210")
        with (
            override_settings(
                BASE_DIR=TEST_ROOT, WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None)
            ),
            ExitStack() as changes,
        ):
            with transaction.atomic():
                deferred = _deferred_family(family, root=TEST_ROOT)
                assert deferred.task is None
                changes.enter_context(
                    override_settings(
                        BASE_DIR=other.root,
                        WEFT_DJANGO=_fixture_weft_settings(CONTEXT=other.root),
                    )
                )
                runtime_env.setenv("WEFT_CONTEXT", str(other.root))
                runtime_env.setenv("WEFT_BUSY_TIMEOUT", "9876")
                runtime_env.chdir(other.root)
            assert deferred.task is not None
            _HARNESS.register_tid(deferred.task.tid)
            other.register_tid(deferred.task.tid)
            result = deferred.task.result(timeout=30.0)
            assert result.status == "completed"
            assert deferred.task.task.context is not None
            assert deferred.task.task.context.root == TEST_ROOT.resolve()
            assert (
                deferred.task.task.context.broker_target
                == _bootstrap_context.broker_target
            )
            assert deferred.task.task.context.broker_config["BUSY_TIMEOUT"] == 3210
            assert other_client.task(deferred.task.tid).snapshot() is None


@pytest.mark.parametrize("family", ["decorated", "native", "reference", "pipeline"])
def test_deferred_family_rollback_publishes_no_spawn_request(family: str) -> None:
    with (
        WeftTestHarness() as harness,
        override_settings(WEFT_DJANGO=_fixture_weft_settings(CONTEXT=harness.root)),
    ):
        queue = harness.context.queue("weft.spawn.requests", persistent=False)
        try:
            with transaction.atomic():
                deferred = _deferred_family(family, root=harness.root)
                transaction.set_rollback(True)
            assert deferred.task is None
            assert list(queue.peek_generator()) == []
        finally:
            queue.close()


@pytest.mark.parametrize("family", ["decorated", "native", "reference", "pipeline"])
@pytest.mark.parametrize("failure", ["config", "payload"])
def test_deferred_failures_register_no_callback(
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    failure: str,
) -> None:
    if failure == "config":
        monkeypatch.setenv("WEFT_MAX_MESSAGE_SIZE", "invalid-integer")
    with transaction.atomic():
        connection = connections["default"]
        before = tuple(connection.run_on_commit)
        error = (
            SubmissionValidationError
            if failure == "payload" and family == "reference"
            else (TypeError, ValueError)
        )
        with (
            pytest.warns(UserWarning, match=r"\bWEFT_MAX_MESSAGE_SIZE=")
            if failure == "config"
            else nullcontext(),
            pytest.raises(error),
        ):
            _deferred_family(
                family,
                root=TEST_ROOT,
                payload=object() if failure == "payload" else "valid",
            )
        assert tuple(connection.run_on_commit) == before


@pytest.mark.parametrize("family", ["decorated", "native", "reference"])
@pytest.mark.parametrize("home_relative", [False, True])
def test_deferred_explicit_paths_bind_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    home_relative: bool,
) -> None:
    _HARNESS.ensure_foreground_manager()
    with WeftTestHarness() as other, monkeypatch.context() as runtime_env:
        origin = TEST_ROOT.resolve()
        declaration = ("~/" if home_relative else "") + origin.name
        runtime_env.chdir(origin.parent)
        runtime_env.setenv("HOME", str(origin.parent))
        runtime_env.setenv("USERPROFILE", str(origin.parent))
        runtime_env.delenv("WEFT_CONTEXT", raising=False)
        with override_settings(
            WEFT_DJANGO=_fixture_weft_settings(
                CONTEXT=declaration if family == "decorated" else other.root
            )
        ):
            with transaction.atomic():
                deferred = _deferred_family(
                    family, root=origin, declared_context=declaration
                )
                # Inspect captured work before commit: a regression must roll back
                # before it can start an unowned runtime at a misinterpreted path.
                callback = connections["default"].run_on_commit[-1][1]
                prepared = inspect.getclosurevars(callback).nonlocals["prepared"]
                assert prepared._request.taskspec.spec.weft_context == str(origin)
                runtime_env.chdir(other.root)
                runtime_env.setenv("HOME", str(other.root))
                runtime_env.setenv("USERPROFILE", str(other.root))
                runtime_env.setenv("WEFT_CONTEXT", str(other.root))
            assert deferred.task is not None
            _HARNESS.register_tid(deferred.task.tid)
            result = deferred.task.result(timeout=30)
            assert result.status == "completed"
            assert result.value == "captured"
            assert deferred.task.task.context is not None
            assert deferred.task.task.context.root == origin
            assert (
                WeftClient.from_weft_context(other.context)
                .task(deferred.task.tid)
                .snapshot()
                is None
            )


def test_default_django_enqueue_and_observation_use_base_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        WeftTestHarness,
        "DEFAULT_DB_NAME",
        WEFT_CONFIG_FIELDS["DEFAULT_DB_NAME"].default,
    )
    with WeftTestHarness() as harness, monkeypatch.context() as runtime_env:
        harness.ensure_foreground_manager()
        _clear_context_selection_env(runtime_env)
        runtime_env.chdir(tmp_path)
        with override_settings(
            BASE_DIR=harness.root,
            WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None),
        ):
            context = get_core_client().context
            assert context.root == harness.context.root
            assert context.broker_target == harness.context.broker_target
            task = echo_task.enqueue("fallback-runtime")
            harness.register_tid(task.tid)
            result = task.result(timeout=30)
            assert result.status == "completed"
            assert result.value == "fallback-runtime"
            observed = weft_django.status(task.tid)
            assert observed is not None
            assert observed.status == "completed"
            output = io.StringIO()
            call_command("weft_task_status", task.tid, stdout=output)
            assert task.tid in output.getvalue()
            assert task.task.context is not None
            assert task.task.context.root == harness.context.root
        assert not (tmp_path / ".weft").exists()


def test_bootstrap_and_export_ignore_hostile_runtime_configuration(
    tmp_path: Path,
) -> None:
    fallback = tmp_path / "read-only"
    fallback.mkdir()
    env = dict(os.environ)
    env.update(
        WEFT_DJANGO_FIXTURE_BASE_DIR=str(fallback),
        WEFT_CONTEXT=str(fallback),
        WEFT_BACKEND="not-a-backend",
        WEFT_MAX_MESSAGE_SIZE="invalid-integer",
    )
    env.pop("WEFT_DJANGO_FIXTURE_WEFT_CONTEXT", None)
    fallback.chmod(0o500)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; import django; django.setup(); from weft_django.registry import is_registered; from testapp.weft_tasks import echo_task; print(json.dumps({'registered': is_registered('testapp.echo_task'), 'context': echo_task.as_taskspec_for_call('x')['spec']['weft_context']}))",
            ],
            env=env,
            cwd=fallback,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        fallback.chmod(0o700)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"registered": True, "context": None}
    assert not list(fallback.iterdir())


@pytest.mark.skipif(
    active_test_backend() != "postgres", reason="PostgreSQL target proof"
)
def test_django_postgres_environment_target_keeps_base_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = WeftTestHarness()
    with monkeypatch.context() as runtime_env:
        for key, value in postgres_env_overrides_for_root(harness.root).items():
            runtime_env.setenv(key, value)
        runtime_env.delenv("WEFT_CONTEXT", raising=False)
        with harness, runtime_env.context() as cwd_env:
            config_file = harness.context.weft_dir / "broker.toml"
            project_config = config_file.read_text(encoding="utf-8")
            config_file.unlink()
            try:
                harness.ensure_foreground_manager()
                cwd_env.chdir(tmp_path)
                with override_settings(
                    BASE_DIR=harness.root,
                    WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None),
                ):
                    context = get_core_client().context
                    assert context.root == harness.context.root
                    assert context.database_path is None
                    assert context.config["BACKEND"] == "postgres"
                    task = echo_task.enqueue("postgres-environment")
                    harness.register_tid(task.tid)
                    result = task.result(timeout=30)
                    assert result.status == "completed"
                    assert result.value == "postgres-environment"
                    observed = weft_django.status(task.tid)
                    assert observed is not None
                    assert observed.status == "completed"
                assert not (tmp_path / ".weft").exists()
            finally:
                # Harness cleanup discovers its owned schema through this file.
                config_file.write_text(project_config, encoding="utf-8")
                config_file.chmod(0o600)


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
def test_asgi_sse_uses_async_streaming_content() -> None:
    task = echo_task.enqueue("asgi-http")
    _wait_for_snapshot(task.tid)

    async def collect() -> tuple[bool, bytes]:
        response = await AsyncClient().get(f"/weft/tasks/{task.tid}/events/")
        try:
            chunks = [chunk async for chunk in response.streaming_content]
            return response.is_async, b"".join(chunks)
        finally:
            response.close()

    is_async, payload = asyncio.run(collect())

    assert is_async is True
    assert b"event: snapshot" in payload
    assert b"event: end" in payload
    assert task.result(timeout=30.0).value == "asgi-http"


@pytest.mark.shared
def test_asgi_handler_cancellation_serializes_stream_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advance_started = threading.Event()
    release_advance = threading.Event()
    closed = threading.Event()
    owner_threads: list[int] = []

    class _Iterator:
        def __iter__(self) -> _Iterator:
            return self

        def __next__(self) -> bytes:
            owner_threads.append(threading.get_ident())
            advance_started.set()
            release_advance.wait(timeout=2.0)
            return b"event: state\ndata: {}\n\n"

        def close(self) -> None:
            owner_threads.append(threading.get_ident())
            closed.set()

    monkeypatch.setattr(
        weft_django_sse, "event_stream", lambda *args, **kwargs: _Iterator()
    )

    async def _run() -> None:
        response = weft_django_sse.sse_response("123", asynchronous=True)

        async def send(_message: dict[str, Any]) -> None:
            return

        asgi_handler = importlib.import_module(
            "django.core.handlers.asgi"
        ).ASGIHandler()
        send_task = asyncio.create_task(asgi_handler.send_response(response, send))
        assert await asyncio.to_thread(advance_started.wait, 1.0)
        send_task.cancel()
        await asyncio.sleep(0)
        assert not closed.is_set()
        release_advance.set()
        with pytest.raises(asyncio.CancelledError):
            await send_task
        response.close()
        assert await asyncio.to_thread(closed.wait, 1.0)

    asyncio.run(_run())

    assert len(owner_threads) == 2
    assert owner_threads[0] == owner_threads[1]


@pytest.mark.shared
def test_wsgi_response_early_close_stays_on_handler_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_threads: list[int] = []
    closed = threading.Event()

    def payloads(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        del args, kwargs
        try:
            owner_threads.append(threading.get_ident())
            yield {
                "tid": "123",
                "event_type": "state",
                "timestamp": 1,
                "payload": {},
            }
            yield {
                "tid": "123",
                "event_type": "state",
                "timestamp": 2,
                "payload": {},
            }
        finally:
            owner_threads.append(threading.get_ident())
            closed.set()

    monkeypatch.setattr(weft_django_sse, "iter_task_event_payloads", payloads)
    monkeypatch.setattr(
        weft_django_sse,
        "get_core_client",
        lambda: type("Client", (), {"task": lambda self, tid: object()})(),
    )
    response = weft_django_sse.sse_response("123", asynchronous=False)
    assert response.streaming
    assert next(iter(response.streaming_content)).startswith(b"event: state")
    response.close()

    assert closed.is_set()
    assert owner_threads == [threading.get_ident(), threading.get_ident()]


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
    with (
        override_settings(WEFT_DJANGO=_fixture_weft_settings(AUTHZ=None)),
        pytest.raises(ImproperlyConfigured, match="WEFT_DJANGO\\['AUTHZ'\\]"),
    ):
        importlib.import_module("weft_django.urls")


@pytest.mark.shared
def test_transport_validation() -> None:
    with (
        override_settings(
            WEFT_DJANGO=_fixture_weft_settings(REALTIME={"TRANSPORT": "bogus"})
        ),
        pytest.raises(ImproperlyConfigured, match="TRANSPORT"),
    ):
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
def test_channels_stream_cancellation_propagates_after_iterator_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("weft_django.channels", None)
    try:
        module = importlib.import_module("weft_django.channels")
    except ImproperlyConfigured:
        pytest.skip("Channels extra is not installed")

    cancel_event = threading.Event()
    advance_started = threading.Event()
    release_advance = threading.Event()
    closed = threading.Event()
    owner_threads: list[int] = []

    class _Iterator:
        def __iter__(self) -> _Iterator:
            return self

        def __next__(self) -> dict[str, Any]:
            owner_threads.append(threading.get_ident())
            advance_started.set()
            release_advance.wait(timeout=2.0)
            return {
                "tid": "1234567890123456789",
                "event_type": "state",
                "timestamp": 1,
                "payload": {"status": "running"},
            }

        def close(self) -> None:
            owner_threads.append(threading.get_ident())
            closed.set()

    iterator = _Iterator()
    monkeypatch.setattr(
        module,
        "iter_task_event_payloads",
        lambda *args, **kwargs: iterator,
    )

    async def _run() -> None:
        consumer = module.TaskEventsConsumer()
        stream_task = asyncio.create_task(
            consumer._stream_events(object(), cancel_event)
        )
        assert await asyncio.to_thread(advance_started.wait, 1.0)
        stream_task.cancel()
        asyncio.get_running_loop().call_later(0.05, release_advance.set)
        with pytest.raises(asyncio.CancelledError):
            await stream_task

    asyncio.run(_run())

    assert cancel_event.is_set()
    assert closed.is_set()
    assert len(owner_threads) == 2
    assert owner_threads[0] == owner_threads[1]


@pytest.mark.shared
def test_channels_disconnect_returns_while_serialized_cleanup_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("weft_django.channels", None)
    try:
        module = importlib.import_module("weft_django.channels")
    except ImproperlyConfigured:
        pytest.skip("Channels extra is not installed")

    async def _run() -> None:
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def stream() -> None:
            try:
                await asyncio.Future()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()

        async def immediate_timeout(
            futures: set[asyncio.Task[None]], *, timeout: float
        ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
            assert timeout == 1.0
            return set(), futures

        monkeypatch.setattr(module.asyncio, "wait", immediate_timeout)
        consumer = module.TaskEventsConsumer()
        consumer._stream_cancel = threading.Event()
        stream_task = asyncio.create_task(stream())
        consumer._stream_task = stream_task
        await asyncio.sleep(0)

        await consumer.disconnect(1000)

        assert consumer._stream_cancel.is_set()
        await cleanup_started.wait()
        assert not stream_task.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await stream_task

    asyncio.run(_run())


@pytest.mark.shared
def test_channels_detached_cleanup_failure_is_reported_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sys.modules.pop("weft_django.channels", None)
    try:
        module = importlib.import_module("weft_django.channels")
    except ImproperlyConfigured:
        pytest.skip("Channels extra is not installed")

    advance_started = threading.Event()
    release_advance = threading.Event()

    class _Iterator:
        def __iter__(self) -> _Iterator:
            return self

        def __next__(self) -> dict[str, Any]:
            advance_started.set()
            release_advance.wait(timeout=2.0)
            return {
                "tid": "123",
                "event_type": "state",
                "timestamp": 1,
                "payload": {},
            }

        def close(self) -> None:
            raise RuntimeError("delayed cleanup failed")

    monkeypatch.setattr(
        module,
        "iter_task_event_payloads",
        lambda *args, **kwargs: _Iterator(),
    )

    async def _run() -> None:
        async def immediate_timeout(
            futures: set[asyncio.Task[None]], *, timeout: float
        ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
            assert timeout == 1.0
            return set(), futures

        monkeypatch.setattr(module.asyncio, "wait", immediate_timeout)
        consumer = module.TaskEventsConsumer()
        cancel_event = threading.Event()
        consumer._stream_cancel = cancel_event
        stream_task = asyncio.create_task(
            consumer._stream_events(object(), cancel_event)
        )
        consumer._stream_task = stream_task
        assert await asyncio.to_thread(advance_started.wait, 1.0)

        await consumer.disconnect(1000)
        assert not stream_task.done()
        release_advance.set()
        while not stream_task.done():
            await asyncio.sleep(0)
        await asyncio.sleep(0)

    with caplog.at_level("ERROR"):
        asyncio.run(_run())

    matching = [
        record
        for record in caplog.records
        if "Django realtime stream cleanup failed" in record.getMessage()
    ]
    assert len(matching) == 1
    assert matching[0].name == "weft_django.channels"
    exc_info = matching[0].exc_info
    assert exc_info is not None
    assert isinstance(exc_info[1], RuntimeError)


@pytest.mark.shared
def test_async_iterator_owner_close_edges_and_stream_isolation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> None:
        never_created = False

        def should_not_create() -> Iterator[int]:
            nonlocal never_created
            never_created = True
            return iter(())

        unopened = weft_django_sse.AsyncIteratorOwner(
            should_not_create,
            cancel_event=threading.Event(),
        )
        await unopened.aclose()
        await unopened.aclose()
        assert not never_created

        failing = weft_django_sse.AsyncIteratorOwner(
            lambda: (_ for _ in ()).throw(RuntimeError("setup failed")),
            cancel_event=threading.Event(),
        )
        with pytest.raises(RuntimeError, match="setup failed"):
            await failing.__anext__()
        await failing.aclose()

        class _CloseFailure:
            def __init__(self) -> None:
                self._values = iter((3,))

            def __iter__(self) -> _CloseFailure:
                return self

            def __next__(self) -> int:
                return next(self._values)

            def close(self) -> None:
                raise RuntimeError("mixed close failed")

        mixed = weft_django_sse.AsyncIteratorOwner(
            _CloseFailure,
            cancel_event=threading.Event(),
        )
        assert await mixed.__anext__() == 3
        assert mixed.request_close() is not None
        with pytest.raises(RuntimeError, match="mixed close failed"):
            await mixed.aclose()

        release_first = threading.Event()
        first_started = threading.Event()

        def blocked() -> Iterator[int]:
            first_started.set()
            release_first.wait(timeout=2.0)
            yield 1

        first = weft_django_sse.AsyncIteratorOwner(
            blocked, cancel_event=threading.Event()
        )
        second = weft_django_sse.AsyncIteratorOwner(
            lambda: iter((2,)), cancel_event=threading.Event()
        )
        first_advance = asyncio.create_task(first.__anext__())
        assert await asyncio.to_thread(first_started.wait, 1.0)
        assert await second.__anext__() == 2
        await second.aclose()
        release_first.set()
        assert await first_advance == 1
        await first.aclose()

    with caplog.at_level("ERROR"):
        asyncio.run(_run())

    assert not any(record.name == "weft_django.realtime" for record in caplog.records)


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


_DECLARED_EXPORT_PATHS: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "description": ("metadata", "description"),
    "tags": ("metadata", "tags"),
    "env": ("spec", "env"),
    "working_dir": ("spec", "working_dir"),
    "stream_output": ("spec", "stream_output"),
    "timeout": ("spec", "timeout"),
    "memory_mb": ("spec", "limits", "memory_mb"),
    "cpu_percent": ("spec", "limits", "cpu_percent"),
    "runner": ("spec", "runner", "name"),
    "runner_options": ("spec", "runner", "options"),
    "metadata": ("metadata", "declared_key"),
}

_DECLARED_EXPORT_VALUES: dict[str, Any] = {
    "name": "testapp.declared_task",
    "description": "declared",
    "tags": ["declared"],
    "env": {"DECLARED": "1"},
    "working_dir": str(TEST_ROOT),
    "stream_output": True,
    "timeout": 30.0,
    "memory_mb": 256,
    "cpu_percent": 50,
    "runner": "host",
    "runner_options": {"declared": True},
    "metadata": "declared",
}


def _export_at_path(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        assert isinstance(current, dict)
        current = current[key]
    return current


@pytest.mark.shared
@pytest.mark.parametrize("override_name", sorted(SUBMIT_OVERRIDE_NAMES))
def test_as_taskspec_for_call_explicit_none_keeps_declared_values(
    override_name: str,
) -> None:
    """An explicit `None` override never clears a declared decorator value.

    Verifies:
    - The export matches the unoverridden export for every override name
    - The declared value is still present at that field's own path
    """

    baseline = declared_task.as_taskspec_for_call("v")
    with_none = declared_task.as_taskspec_for_call(
        "v",
        _overrides={override_name: None},
    )

    assert with_none == baseline
    path = _DECLARED_EXPORT_PATHS[override_name]
    assert _export_at_path(with_none, path) == _DECLARED_EXPORT_VALUES[override_name]


@pytest.mark.shared
def test_as_taskspec_for_call_rejects_unknown_and_wait() -> None:
    """Every `_overrides` key is a core override name; others raise TypeError.

    Verifies:
    - An unknown name, the submission-only `wait`, and `payload` all raise
    - The export applies no vocabulary of its own
    """

    for overrides in (
        {"unknown_flag": 1},
        {"wait": True},
        {"payload": {"x": 1}},
    ):
        with pytest.raises(TypeError, match="Unknown submit override"):
            declared_task.as_taskspec_for_call("v", _overrides=overrides)


@pytest.mark.shared
def test_as_taskspec_for_call_rejects_invalid_values() -> None:
    """Schema-invalid and reserved values fail locally before anything is built.

    Verifies:
    - `memory_mb=0` and `name=""` raise the TaskSpec validation error
    - The Django host-only rule and the reserved `_weft.` namespace raise
    """

    with pytest.raises(ValueError):
        declared_task.as_taskspec_for_call("v", _overrides={"memory_mb": 0})
    with pytest.raises(ValueError, match="runner='host'"):
        declared_task.as_taskspec_for_call("v", _overrides={"runner": "docker"})
    with pytest.raises(ValueError):
        declared_task.as_taskspec_for_call("v", _overrides={"name": ""})
    with pytest.raises(ValueError, match="reserved"):
        declared_task.as_taskspec_for_call("v", _overrides={"name": "_weft.x"})


@pytest.mark.shared
@pytest.mark.parametrize(
    ("override_name", "value", "expected"),
    [
        ("name", "renamed", "renamed"),
        ("description", "d", "d"),
        ("tags", ("a", "b"), ["a", "b"]),
        ("env", {"K": "v"}, {"DECLARED": "1", "K": "v"}),
        pytest.param("working_dir", str(TEST_ROOT), str(TEST_ROOT), id="working_dir"),
        ("stream_output", False, False),
        ("timeout", 5.0, 5.0),
        ("memory_mb", 512, 512),
        ("cpu_percent", 25, 25),
        ("runner", "host", "host"),
        ("runner_options", {"x": 1}, {"declared": True, "x": 1}),
        ("metadata", {"k": "v"}, "declared"),
    ],
)
def test_as_taskspec_for_call_applies_every_override_like_prepare(
    override_name: str,
    value: Any,
    expected: Any,
) -> None:
    """Every public override lands exactly as `prepare(...)` would place it.

    Verifies:
    - The exported value at each tabled path, with core merge semantics
    - The export is a full validated template carrying the call envelope
    """

    exported = declared_task.as_taskspec_for_call(
        "v",
        _overrides={override_name: value},
    )

    assert _export_at_path(exported, _DECLARED_EXPORT_PATHS[override_name]) == expected
    if override_name == "metadata":
        assert exported["metadata"]["k"] == "v"
    assert exported["tid"] is None
    assert validate_taskspec_payload(exported, template=True) is not None
    embedded = exported["spec"]["args"]
    assert len(embedded) == 1
    envelope = embedded[0]["payload"]
    assert envelope["call"]["args"] == ["v"]


@pytest.mark.shared
@pytest.mark.parametrize("declared_context", [None, "relative-project", "~/project"])
def test_as_taskspec_for_call_is_pure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared_context: str | None,
) -> None:
    """The export builds no context, reads no config, opens no broker, writes nothing.

    Verifies:
    - Only an explicit raw declaration is copied despite environment and BASE_DIR
    - Tripwires on `build_context`, `resolve_runtime_config`, `open_broker`, and
      `get_core_client` never fire, and nothing is written anywhere
    """

    with override_settings(
        WEFT_DJANGO=_fixture_weft_settings(CONTEXT=declared_context),
        BASE_DIR=tmp_path,
    ):
        _clear_context_selection_env(monkeypatch)
        monkeypatch.setenv("WEFT_CONTEXT", str(tmp_path / "environment"))

        def _forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("forbidden on the export path")

        monkeypatch.setattr("weft.client._client.build_context", _forbidden)
        monkeypatch.setattr("weft.context.resolve_runtime_config", _forbidden)
        monkeypatch.setattr("weft.context.open_broker", _forbidden)
        monkeypatch.setattr(weft_django_client, "get_core_client", _forbidden)

        weft_dir = TEST_ROOT / ".weft"
        listing_before = sorted(path.name for path in weft_dir.iterdir())
        database_path = _bootstrap_context.database_path
        db_stat_before = (
            database_path.stat()
            if database_path is not None and database_path.exists()
            else None
        )

        monkeypatch.chdir(tmp_path)
        tmp_path.chmod(0o500)
        try:
            exported = echo_task.as_taskspec_for_call(
                "v",
                _overrides={"timeout": None},
            )
        finally:
            tmp_path.chmod(0o700)

        assert list(tmp_path.iterdir()) == []
        assert not (tmp_path / ".weft").exists()
        assert sorted(path.name for path in weft_dir.iterdir()) == listing_before
        if db_stat_before is not None and database_path is not None:
            db_stat_after = database_path.stat()
            assert db_stat_after.st_size == db_stat_before.st_size
            assert db_stat_after.st_mtime == db_stat_before.st_mtime
        assert exported["spec"]["timeout"] == 30.0

        assert exported["spec"]["weft_context"] == declared_context


@pytest.mark.shared
def test_as_taskspec_for_call_export_runs_like_enqueue() -> None:
    """The exported definition is submittable and runs like `enqueue(...)`.

    Verifies:
    - `submit_taskspec(exported)` completes with the same value as `enqueue`
    - Both carry the same overridden name and metadata on their snapshots
    """

    overrides: dict[str, Any] = {
        "name": "renamed",
        "timeout": 5.0,
        "metadata": {"k": "v"},
    }
    exported = echo_task.as_taskspec_for_call("v", _overrides=overrides)

    exported_submission = submit_taskspec(exported)
    enqueued = echo_task.enqueue("v", _overrides=overrides)

    exported_result = exported_submission.result(timeout=30.0)
    enqueued_result = enqueued.result(timeout=30.0)

    assert exported_result.status == "completed"
    assert enqueued_result.status == "completed"
    assert exported_result.value == enqueued_result.value

    exported_snapshot = exported_submission.snapshot()
    enqueued_snapshot = enqueued.snapshot()
    assert exported_snapshot is not None
    assert enqueued_snapshot is not None
    assert exported_snapshot.name == "renamed"
    assert enqueued_snapshot.name == "renamed"
    assert exported_snapshot.metadata["k"] == "v"
    assert enqueued_snapshot.metadata["k"] == "v"
