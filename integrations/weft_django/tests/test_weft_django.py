"""Broker-backed integration tests for the Django package."""
# ruff: noqa: E402

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

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

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client
from fixture_project import request_id_provider
from testapp.models import EventRecord
from testapp.weft_tasks import echo_current_request_id, echo_task

from weft.core.taskspec import TaskSpec
from weft_django import (
    enqueue_on_commit,
    submit_taskspec,
    submit_taskspec_on_commit,
)
from weft_django.registry import is_registered

call_command("migrate", run_syncdb=True, verbosity=0)


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    EventRecord.objects.all().delete()


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


@pytest.mark.shared
def test_registry_autodiscovers_fixture_tasks() -> None:
    assert is_registered("testapp.echo_task") is True
    assert is_registered("testapp.fetch_record_value") is True


@pytest.mark.shared
def test_decorated_task_enqueue_executes_through_weft() -> None:
    task = echo_task.enqueue("hello")
    task_result = task.result(timeout=30.0)

    assert task_result.status == "completed"
    assert task_result.value == "hello"


@pytest.mark.shared
def test_enqueue_on_commit_defers_submission_until_commit() -> None:
    with transaction.atomic():
        record = EventRecord.objects.create(key="k", value="from-db")
        deferred = enqueue_on_commit("testapp.fetch_record_value", record.id)
        assert deferred.task is None

    assert deferred.task is not None
    task_result = deferred.task.result(timeout=30.0)
    assert task_result.status == "completed"
    assert task_result.value == "from-db"


@pytest.mark.shared
def test_deferred_submit_rejects_wait_true() -> None:
    with pytest.raises(ValueError, match="wait=True"):
        enqueue_on_commit("testapp.echo_task", "x", _overrides={"wait": True})
    with pytest.raises(ValueError, match="wait=True"):
        submit_taskspec_on_commit(_native_taskspec(), payload="x", wait=True)


@pytest.mark.shared
def test_native_taskspec_submission_helper_uses_core_client() -> None:
    task = submit_taskspec(_native_taskspec(), payload="native")
    task_result = task.result(timeout=30.0)

    assert task_result.status == "completed"
    assert task_result.value == "native"


@pytest.mark.shared
def test_http_detail_and_sse_views_are_read_only_diagnostics() -> None:
    task = echo_task.enqueue("http")
    task_result = task.result(timeout=30.0)
    assert task_result.status == "completed"

    client = Client()
    detail_response = client.get(f"/weft/tasks/{task.tid}/")
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "completed"

    stream_response = client.get(f"/weft/tasks/{task.tid}/events/")
    assert stream_response.status_code == 200
    first_chunk = next(stream_response.streaming_content).decode("utf-8")
    assert "event:" in first_chunk
    assert task.tid in first_chunk


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
