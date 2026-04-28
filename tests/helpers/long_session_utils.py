"""Shared helpers for long-session integration scenarios.

These helpers keep the long-lived mixed-workload scenario aligned across the
normal CLI integration test and any dev-only benchmark harnesses.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.helpers import iter_queue_json_entries

BULK_ROUNDS = 96
PERSISTENT_WORK_ITEMS = 12
INTERACTIVE_INTERVAL = 16
ALIAS_INTERVAL = 8
SESSION_TIMEOUT_SECONDS = 240.0

INTERACTIVE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py"
)


@dataclass(frozen=True, slots=True)
class SubmittedTask:
    """Expected transcript entry for one no-wait task submission."""

    label: str
    tid: str
    expected: str
    name: str
    tags: tuple[str, ...]


def session_env() -> dict[str, str]:
    """Return environment overrides for long-session runs."""

    env = os.environ.copy()
    env["WEFT_MANAGER_REUSE_ENABLED"] = "1"
    env["WEFT_MANAGER_LIFETIME_TIMEOUT"] = "300"
    return env


def wait_for_terminal_statuses(
    harness: WeftTestHarness,
    tids: set[str],
    *,
    timeout: float,
) -> dict[str, str]:
    """Wait until every task in *tids* reaches a terminal state."""

    remaining = set(tids)
    statuses: dict[str, str] = {}
    terminal_statuses = {"completed", "failed", "timeout", "cancelled", "killed"}

    log_queue = harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    deadline = time.monotonic() + timeout
    last_seen: int | None = None
    try:
        while remaining and time.monotonic() < deadline:
            next_last_seen = last_seen
            for payload, timestamp in iter_queue_json_entries(
                log_queue,
                since_timestamp=last_seen,
            ):
                if last_seen is not None and timestamp <= last_seen:
                    continue
                next_last_seen = timestamp
                tid = payload.get("tid")
                if tid not in remaining:
                    continue
                status = payload.get("status")
                if isinstance(status, str) and status in terminal_statuses:
                    statuses[str(tid)] = status
                    remaining.discard(str(tid))
            last_seen = next_last_seen
            if remaining:
                # Same polling pattern as WeftTestHarness.wait_for_completion;
                # SimpleBroker has no blocking read-with-timeout API.
                time.sleep(0.05)
    finally:
        log_queue.close()

    for tid in list(remaining):
        outbox = harness.context.queue(f"T{tid}.outbox", persistent=True)
        try:
            if outbox.peek_one() is not None:
                statuses[tid] = "completed"
                remaining.discard(tid)
        finally:
            outbox.close()

    if remaining:
        raise TimeoutError(f"Timed out waiting for terminal tasks: {sorted(remaining)}")
    return statuses


def write_command_script(path: Path) -> None:
    """Create the command target used by the long-session scenario."""

    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import os",
                "import sys",
                "import time",
                "",
                "",
                "def main() -> int:",
                "    label = sys.argv[1]",
                "    delay = float(sys.argv[2])",
                "    prefix = os.environ['SESSION_PREFIX']",
                "    time.sleep(delay)",
                "    print(f'{prefix}:{label}:{int(round(delay * 1000))}')",
                "    return 0",
                "",
                "",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_persistent_spec(path: Path, *, workdir: Path, inbox: str) -> None:
    """Create the persistent spec used by the long-session scenario."""

    spec_payload = {
        "tid": "1763000000000000001",
        "name": "session-persistent-surround",
        "version": "1.0",
        "spec": {
            "type": "function",
            "persistent": True,
            "function_target": "tests.tasks.sample_targets:surround_payload",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": inbox},
            "outputs": {"outbox": "session.persistent.outbox"},
            "control": {
                "ctrl_in": "session.persistent.ctrl_in",
                "ctrl_out": "session.persistent.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")


def interactive_expected(label: str) -> str:
    """Return the canonical interactive transcript for *label*."""

    return "\n".join(
        [
            f"echo: {label}-a",
            f"echo: {label}-b",
            "goodbye",
        ]
    )
