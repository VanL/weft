"""Isolated real-fork probe for retained WeftClient submission sessions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from weft.client import WeftClient
from weft.commands import submission
from weft.core import manager_runtime


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


def main(root: Path) -> int:
    submission.ensure_manager_after_submission = _ready
    client = WeftClient(path=root)
    client.__enter__()
    client.submit(_spec(root, "parent-first"))
    inherited = client._submission_session
    assert inherited is not None
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            client.submit(_spec(root, "child-bounded"))
            child_bounded = client._submission_session is None
            child_closed = inherited._released
            with client:
                client.submit(_spec(root, "child-retained"))
                child_session = client._submission_session
                child_retained = (
                    child_session is not None and child_session is not inherited
                )
            payload = json.dumps(
                {
                    "child_bounded_after_submit": child_bounded,
                    "child_closed_inherited": child_closed,
                    "child_retained_fresh_session": child_retained,
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
    client.submit(_spec(root, "parent-second"))
    parent_reused = client._submission_session is inherited
    client.close()
    print(
        json.dumps(
            {
                **child_report,
                "parent_session_still_open": parent_still_open,
                "parent_session_still_reused": parent_reused,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
