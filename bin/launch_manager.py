#!/usr/bin/env python3
"""Launch a standalone Weft Manager process for debugging."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.taskspec import TaskSpec


def _generate_tid(context: WeftContext) -> str:
    with BrokerDB(str(context.database_path)) as db:
        return str(db.generate_timestamp())


def _build_manager_spec(context: WeftContext, tid: str, name: str) -> TaskSpec:
    prefix = f"worker.{tid}"
    spec_dict: dict[str, Any] = {
        "tid": tid,
        "name": name,
        "spec": {
            "type": "function",
            "function_target": "weft.core.manager:Manager",
            "timeout": None,
            "weft_context": str(context.root),
        },
        "io": {
            "inputs": {"inbox": f"{prefix}.inbox"},
            "outputs": {"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            "control": {
                "ctrl_in": f"{prefix}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"{prefix}.{QUEUE_CTRL_OUT_SUFFIX}",
            },
        },
        "state": {},
        "metadata": {
            "role": "manager",
            "capabilities": [],
        },
    }
    return TaskSpec.model_validate(spec_dict, context={"auto_expand": True})


def _wait_for_registry(
    context: WeftContext, tid: str, timeout: float = 5.0
) -> dict[str, Any] | None:
    queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    deadline = time.time() + timeout
    latest: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            raw_entries = cast(
                Sequence[tuple[str, int]] | None,
                queue.peek_many(limit=1000, with_timestamps=True),
            )
        except Exception:
            raw_entries = None

        if raw_entries:
            for entry, _timestamp in raw_entries:
                try:
                    data = cast(dict[str, Any], json.loads(entry))
                except json.JSONDecodeError:
                    continue
                if data.get("tid") == tid:
                    latest = data
        if latest and latest.get("status") == "active":
            return latest
        time.sleep(0.1)
    return latest


def _spawn_manager_subprocess(
    context: WeftContext,
    spec: TaskSpec,
    *,
    verbose: bool,
) -> subprocess.Popen[bytes]:
    spec_json = spec.model_dump_json()
    config_json = json.dumps(context.config)
    spec_b64 = base64.b64encode(spec_json.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(config_json.encode("utf-8")).decode("ascii")

    cmd = [
        sys.executable,
        "-m",
        "weft.manager_process",
        "weft.core.manager.Manager",
        str(context.database_path),
        spec_b64,
        config_b64,
        "0.05",
    ]

    stdout = None if verbose else subprocess.DEVNULL
    stderr = None if verbose else subprocess.DEVNULL
    process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, close_fds=True)
    return process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch a standalone Weft Manager process"
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=None,
        help="Project root to use for the Weft context (defaults to cwd discovery)",
    )
    parser.add_argument(
        "--name",
        default="debug-manager",
        help="Human-friendly name to embed in the TaskSpec",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print additional diagnostic information",
    )
    args = parser.parse_args(argv)

    context = build_context(spec_context=args.context)
    tid = _generate_tid(context)
    manager_spec = _build_manager_spec(context, tid, args.name)

    process = _spawn_manager_subprocess(context, manager_spec, verbose=args.verbose)

    registry_record = _wait_for_registry(context, tid)

    inbox = manager_spec.io.inputs.get("inbox", f"worker.{tid}.{QUEUE_INBOX_SUFFIX}")
    ctrl_in = manager_spec.io.control.get(
        "ctrl_in", f"worker.{tid}.{QUEUE_CTRL_IN_SUFFIX}"
    )
    ctrl_out = manager_spec.io.control.get(
        "ctrl_out", f"worker.{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
    )

    print("Manager launched")
    print(f"  TID: {tid}")
    print(f"  PID: {process.pid}")
    print(f"  Inbox queue: {inbox}")
    print(f"  Control queue (in): {ctrl_in}")
    print(f"  Control queue (out): {ctrl_out}")
    if registry_record:
        print(f"  Registry status: {registry_record.get('status')}")
    else:
        print("  Registry status: <unconfirmed>")

    if args.verbose:
        print(f"  Database: {context.database_path}")
        print(f"  Context root: {context.root}")

    print("exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
