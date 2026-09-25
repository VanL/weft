"""Focused tests for Django request-local Weft client ownership.

Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-3.1], [DJ-13.2].
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Self, cast

import pytest
from asgiref.local import Local
from django.dispatch import Signal

from tests.helpers.test_backend import prepare_project_root

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from weft_django import lifecycle


class _FakeClient:
    def __init__(self, *, close_failures: list[BaseException] | None = None) -> None:
        self.close_failures = close_failures or []
        self.enter_count = 0
        self.close_count = 0

    def __enter__(self) -> Self:
        self.enter_count += 1
        return self

    def close(self) -> None:
        self.close_count += 1
        if self.close_failures:
            raise self.close_failures.pop(0)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(lifecycle, "_owner_pid", lifecycle.os.getpid())
    monkeypatch.setattr(lifecycle, "_generation", 0)
    monkeypatch.setattr(lifecycle, "_generation_lock", lifecycle.threading.Lock())
    monkeypatch.setattr(lifecycle, "_local", Local(thread_critical=True))
    yield


def _factory(client: _FakeClient) -> Callable[[], Any]:
    return lambda: client


def _lookup(factory: Callable[[], _FakeClient]) -> _FakeClient:
    return cast(_FakeClient, lifecycle.get_current_client(cast(Any, factory)))


def test_empty_request_signals_do_not_construct_client() -> None:
    created = 0

    def factory() -> _FakeClient:
        nonlocal created
        created += 1
        return _FakeClient()

    lifecycle.request_started_receiver(sender=object())
    lifecycle.request_finished_receiver(sender=object())

    assert created == 0
    assert _lookup(factory) is not None
    assert created == 1


def test_sync_request_lazily_reuses_and_closes_one_client() -> None:
    first = _FakeClient()
    second = _FakeClient()
    clients = iter((first, second))

    def factory() -> _FakeClient:
        return next(clients)

    lifecycle.request_started_receiver(sender=object())
    assert _lookup(factory) is first
    assert _lookup(factory) is first
    assert first.enter_count == 1

    lifecycle.request_finished_receiver(sender=object())
    assert first.close_count == 1
    assert _lookup(factory) is second
    assert second.enter_count == 0


def test_direct_async_lookup_is_one_shot_during_active_request() -> None:
    clients: list[_FakeClient] = []

    def factory() -> _FakeClient:
        client = _FakeClient()
        clients.append(client)
        return client

    lifecycle.request_started_receiver(sender=object())

    async def lookup_twice() -> tuple[_FakeClient, _FakeClient]:
        return _lookup(factory), _lookup(factory)

    first, second = asyncio.run(lookup_twice())
    assert first is not second
    assert [client.enter_count for client in clients] == [0, 0]


def test_request_finish_logs_and_retries_one_ordinary_close_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeClient(close_failures=[RuntimeError("busy")])
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(client))

    with caplog.at_level(logging.WARNING, logger=lifecycle.__name__):
        lifecycle.request_finished_receiver(sender=object())

    assert client.close_count == 2
    assert "retrying once" in caplog.text
    assert not lifecycle._has_current_record()


def test_repeated_finish_failure_parks_entry_and_blocks_replacement(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeClient(
        close_failures=[RuntimeError("busy-1"), RuntimeError("busy-2")]
    )
    replacement = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(client))

    with caplog.at_level(logging.WARNING, logger=lifecycle.__name__):
        lifecycle.request_finished_receiver(sender=object())

    assert lifecycle._has_current_record()
    assert "process recycling" in caplog.text

    client.close_failures.append(RuntimeError("busy-3"))
    with pytest.raises(RuntimeError, match="busy-3"):
        lifecycle.get_current_client(_factory(replacement))
    assert replacement.enter_count == 0

    assert lifecycle.get_current_client(_factory(replacement)) is replacement
    assert not lifecycle._has_current_record()


def test_nonordinary_finish_cleanup_failure_propagates() -> None:
    client = _FakeClient(close_failures=[KeyboardInterrupt()])
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(client))

    with pytest.raises(KeyboardInterrupt):
        lifecycle.request_finished_receiver(sender=object())

    assert client.close_count == 1
    assert lifecycle._has_current_record()


def test_request_start_refuses_new_scope_while_stale_cleanup_fails() -> None:
    client = _FakeClient(close_failures=[RuntimeError("still busy")])
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(client))

    with pytest.raises(RuntimeError, match="still busy"):
        lifecycle.request_started_receiver(sender=object())

    assert not lifecycle._request_is_active()
    assert lifecycle._has_current_record()


@pytest.mark.parametrize("setting", ["WEFT_DJANGO", "BASE_DIR"])
def test_relevant_setting_change_rotates_current_client_and_preserves_request(
    setting: str,
) -> None:
    first = _FakeClient()
    second = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    assert lifecycle.get_current_client(_factory(first)) is first

    lifecycle.setting_changed_receiver(sender=object(), setting=setting, value=object())

    assert first.close_count == 1
    assert lifecycle.get_current_client(_factory(second)) is second
    assert second.enter_count == 1


def test_unrelated_setting_change_does_not_rotate_client() -> None:
    client = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    assert lifecycle.get_current_client(_factory(client)) is client

    lifecycle.setting_changed_receiver(sender=object(), setting="DEBUG", value=False)

    assert lifecycle.get_current_client(_factory(client)) is client
    assert client.close_count == 0


def test_setting_cleanup_failure_parks_entry_until_owner_access(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeClient(close_failures=[RuntimeError("busy")])
    replacement = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(client))

    with caplog.at_level(logging.WARNING, logger=lifecycle.__name__):
        lifecycle.setting_changed_receiver(
            sender=object(), setting="WEFT_DJANGO", value={}
        )

    assert "cleanup-pending" in caplog.text
    assert lifecycle.get_current_client(_factory(replacement)) is replacement
    assert client.close_count == 2
    assert replacement.enter_count == 1


def test_pid_change_discards_inherited_entry_before_new_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_client = _FakeClient()
    child_client = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    lifecycle.get_current_client(_factory(parent_client))
    old_lock = lifecycle._generation_lock
    child_pid = lifecycle._owner_pid + 1
    monkeypatch.setattr(lifecycle.os, "getpid", lambda: child_pid)

    assert lifecycle.get_current_client(_factory(child_client)) is child_client
    assert parent_client.close_count == 1
    assert child_client.enter_count == 0
    assert lifecycle._generation_lock is not old_lock


def test_request_clients_are_isolated_by_owner_thread() -> None:
    main_client = _FakeClient()
    thread_client = _FakeClient()
    lifecycle.request_started_receiver(sender=object())
    assert lifecycle.get_current_client(_factory(main_client)) is main_client

    def use_thread_client() -> None:
        lifecycle.request_started_receiver(sender=object())
        assert lifecycle.get_current_client(_factory(thread_client)) is thread_client
        lifecycle.request_finished_receiver(sender=object())

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(use_thread_client).result(timeout=5.0)

    assert thread_client.close_count == 1
    assert lifecycle.get_current_client(_factory(main_client)) is main_client
    lifecycle.request_finished_receiver(sender=object())
    assert main_client.close_count == 1


def test_signal_registration_uses_stable_dispatch_uids() -> None:
    started = Signal()
    finished = Signal()
    changed = Signal()

    lifecycle.register_lifecycle_signals(
        request_started_signal=started,
        request_finished_signal=finished,
        setting_changed_signal=changed,
    )
    lifecycle.register_lifecycle_signals(
        request_started_signal=started,
        request_finished_signal=finished,
        setting_changed_signal=changed,
    )

    started.send(sender=object())
    assert lifecycle._request_is_active()
    changed.send(sender=object(), setting="BASE_DIR", value=Path("/tmp"))
    assert lifecycle._generation == 1
    finished.send(sender=object())
    assert not lifecycle._request_is_active()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_real_fork_replaces_held_parent_lock_before_registry_access(
    tmp_path: Path,
) -> None:
    fixture_root = PACKAGE_ROOT / "tests" / "fixture_project"
    project_root = PACKAGE_ROOT.parents[1]
    env = dict(os.environ)
    pythonpath = [str(project_root), str(PACKAGE_ROOT), str(fixture_root)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fixture_project.lifecycle_fork_probe",
            str(prepare_project_root(tmp_path / "registry-fork")),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "child_closed_inherited": True,
        "child_replaced_lock": True,
        "child_submitted": True,
        "child_used_one_shot": True,
        "parent_still_owned": True,
    }
