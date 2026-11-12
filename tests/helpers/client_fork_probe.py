"""Isolated real-fork probe for retained WeftClient submission sessions.

Modes ([PY-1], [PY-3]):

- ``live``: the parent's retained session is open when the process forks.
- ``closed``: the parent closes its client through the public path first.

The child attempts a bounded and an explicitly re-entered submission and
reports each outcome. Whether a backend refuses the child is the caller's
expectation, not this probe's: built-in SQLite refuses after a live parent
connection crossed the fork, and nothing here retries around that.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import simplebroker
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE
from weft.client import WeftClient
from weft.commands import submission
from weft.core import manager_runtime

PARENT_MODES = ("live", "closed")
UNSAFE_FORK_MESSAGE = "SQLite access is unsafe after fork"


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


def _origin(module_name: str) -> str | None:
    spec = importlib.util.find_spec(module_name)
    return None if spec is None else spec.origin


def provenance() -> dict[str, str | None]:
    """Report which broker distributions this process actually imported."""

    report: dict[str, str | None] = {
        "simplebroker_file": _origin("simplebroker"),
        "simplebroker_version": simplebroker.__version__,
        "simplebroker_pg_file": None,
        "simplebroker_pg_version": None,
    }
    if "simplebroker_pg" in sys.modules:
        report["simplebroker_pg_file"] = _origin("simplebroker_pg")
        report["simplebroker_pg_version"] = metadata.version("simplebroker-pg")
    return report


def attempt(action: Callable[[], str]) -> dict[str, str | None]:
    """Run one submission and report its task id or its broker refusal."""

    try:
        tid = action()
    except RuntimeError as exc:
        return {"tid": None, "error_type": type(exc).__name__, "error": str(exc)}
    return {"tid": tid, "error_type": None, "error": None}


def stored_task_ids(root: Path) -> list[str]:
    """Read durable spawn requests through a client that opened afterwards."""

    observer = WeftClient(path=root)
    try:
        rows = observer.queues.peek(WEFT_SPAWN_REQUESTS_QUEUE, all_messages=True)
        return [str(row.timestamp) for row in rows]
    finally:
        observer.close()


def main(root: Path, parent_mode: str) -> int:
    if parent_mode not in PARENT_MODES:
        raise SystemExit(f"unknown parent mode: {parent_mode}")
    submission.ensure_manager_after_submission = _ready
    client = WeftClient(path=root)
    client.__enter__()
    parent_first = client.submit(_spec(root, "parent-first")).tid
    inherited = client._submission_session
    assert inherited is not None
    if parent_mode == "closed":
        client.close()
        assert inherited._released
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            bounded = attempt(lambda: client.submit(_spec(root, "child-bounded")).tid)
            child_bounded = client._submission_session is None
            child_closed = inherited._released
            retained_is_fresh = False

            def submit_retained() -> str:
                nonlocal retained_is_fresh
                with client:
                    tid = client.submit(_spec(root, "child-retained")).tid
                    session = client._submission_session
                    retained_is_fresh = session is not None and session is not inherited
                    return tid

            retained = attempt(submit_retained)
            payload = json.dumps(
                {
                    "child_bounded": bounded,
                    "child_retained": retained,
                    "child_bounded_after_submit": child_bounded,
                    "child_closed_inherited": child_closed,
                    "child_retained_fresh_session": retained_is_fresh,
                    "child_provenance": provenance(),
                }
            ).encode()
            os.write(write_fd, payload)
            os._exit(0)
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-378] exception
            os.write(write_fd, json.dumps({"error": repr(exc)}).encode())
            os._exit(1)

    os.close(write_fd)
    child_payload = os.read(read_fd, 65536)
    os.close(read_fd)
    _, status = os.waitpid(child_pid, 0)
    child_report = json.loads(child_payload)
    if status != 0:
        raise RuntimeError(f"fork child failed: {child_report}")

    parent_still_open = not inherited._released
    parent_second = client.submit(_spec(root, "parent-second")).tid
    parent_reused = client._submission_session is inherited
    client.close()
    # Count durable rows only after every process has closed its client: an
    # acknowledged task id is not evidence that the row was stored.
    print(
        json.dumps(
            {
                **child_report,
                "parent_first": parent_first,
                "parent_second": parent_second,
                "parent_session_still_open": parent_still_open,
                "parent_session_still_reused": parent_reused,
                "parent_provenance": provenance(),
                "stored_task_ids": stored_task_ids(root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2]))
