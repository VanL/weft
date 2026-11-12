"""Isolated held-lock fork probe with real Django-facing submissions.

Modes ([DJ-13.2], [DJ-17.3]):

- ``live``: the request-scoped client is open when the host process forks.
- ``closed``: the request finishes, closing that client, before the fork.

In both modes another parent thread holds the registry lock across the fork,
so the child must replace it before any registry access. The child reports
whether its lookup and its submission succeeded; the caller decides which
backend may refuse the submission.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from tests.helpers.client_fork_probe import (
    PARENT_MODES,
    attempt,
    provenance,
    stored_task_ids,
)
from weft.client import WeftClient
from weft.commands import submission
from weft.core import manager_runtime
from weft_django import lifecycle
from weft_django.client import DjangoWeftClient


def _ready(*_args: object, **_kwargs: object) -> manager_runtime.ManagerEnsureResult:
    return manager_runtime.ManagerEnsureResult(
        outcome="ready",
        manager_record=None,
        started_here=False,
        process_handle=None,
        reason="fork-probe",
    )


def _spec(root: Path, name: str) -> dict[str, object]:
    return {
        "name": name,
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(root),
        },
    }


def main(root: Path, parent_mode: str) -> int:
    if parent_mode not in PARENT_MODES:
        raise SystemExit(f"unknown parent mode: {parent_mode}")
    submission.ensure_manager_after_submission = _ready

    def factory() -> DjangoWeftClient:
        return DjangoWeftClient(WeftClient(path=root))

    lifecycle.request_started_receiver(sender=object())
    parent_client = lifecycle.get_current_client(factory)
    parent_first = parent_client.submit_taskspec(
        _spec(root, "registry-parent-first")
    ).tid
    inherited_session = parent_client.core_client._submission_session
    assert inherited_session is not None
    if parent_mode == "closed":
        lifecycle.request_finished_receiver(sender=object())
        assert inherited_session._released
    inherited_lock = lifecycle._generation_lock
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_lock() -> None:
        with inherited_lock:
            lock_held.set()
            release_lock.wait(timeout=10)

    sibling = threading.Thread(target=hold_lock)
    sibling.start()
    assert lock_held.wait(timeout=5)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            # Lookup must succeed in every mode: it replaces the inherited
            # lock and opens no broker connection. Only the submission may
            # be refused by the backend.
            child_client = lifecycle.get_current_client(factory)
            child_submission = attempt(
                lambda: (
                    child_client.submit_taskspec(
                        _spec(root, "registry-child-bounded")
                    ).tid
                )
            )
            os.write(
                write_fd,
                json.dumps(
                    {
                        "child_closed_inherited": inherited_session._released,
                        "child_replaced_lock": (
                            lifecycle._generation_lock is not inherited_lock
                        ),
                        "child_submission": child_submission,
                        "child_used_one_shot": (
                            child_client.core_client._submission_session is None
                        ),
                        "child_provenance": provenance(),
                    }
                ).encode(),
            )
            os._exit(0)
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-378] exception
            os.write(write_fd, json.dumps({"error": repr(exc)}).encode())
            os._exit(1)

    os.close(write_fd)
    child_payload = os.read(read_fd, 65536)
    os.close(read_fd)
    _, status = os.waitpid(child_pid, 0)
    # The sibling holds the inherited lock until the child has been reaped.
    release_lock.set()
    sibling.join(timeout=5)
    child_report = json.loads(child_payload)
    if status != 0:
        raise RuntimeError(f"fork child failed: {child_report}")
    if parent_mode == "closed":
        lifecycle.request_started_receiver(sender=object())
        parent_client = lifecycle.get_current_client(factory)
    parent_second = parent_client.submit_taskspec(
        _spec(root, "registry-parent-second")
    ).tid
    parent_still_owned = (
        not inherited_session._released
        and parent_client.core_client._submission_session is inherited_session
    )
    lifecycle.request_finished_receiver(sender=object())
    # Count durable rows only after every process has closed its client.
    print(
        json.dumps(
            {
                **child_report,
                "parent_first": parent_first,
                "parent_second": parent_second,
                "parent_still_owned": parent_still_owned,
                "parent_provenance": provenance(),
                "stored_task_ids": stored_task_ids(root),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2]))
