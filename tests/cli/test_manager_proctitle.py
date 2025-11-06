"""Test for manager process title updates."""

from __future__ import annotations

import json
import time

import psutil
import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness


def test_manager_proctitle_updates_to_running(weft_harness: WeftTestHarness) -> None:
    """Verify that the manager's process title updates from 'init' to 'running'."""
    # The harness starts a manager implicitly via `run_cli` if one isn't running.
    # We just need to trigger an action and then find the manager process.
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--verbose",
        "echo",
        "hello",
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, err
    time.sleep(1)  # Give the manager time to start and update its title

    # The output of run --verbose contains two JSON objects, one for the manager
    # and one for the task. We need to parse them to get the manager's PID.
    manager_pid = None
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict) and data.get("event") == "manager_started":
                manager_pid = data.get("pid")
                break
        except (json.JSONDecodeError, KeyError):
            continue
    assert manager_pid is not None, "Could not get manager PID from CLI output"

    try:
        manager_process = psutil.Process(manager_pid)
    except psutil.NoSuchProcess:
        pytest.fail("Manager process not found")

    # Now check that its title eventually becomes 'running'
    running_title_found = False
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            # In some environments, cmdline is what we need, in others it is name.
            title = manager_process.name()
            if "manager:" not in title:
                cmdline = manager_process.cmdline()
                if cmdline:
                    title = cmdline[0]
            if "manager:running" in title:
                running_title_found = True
                break
        except psutil.NoSuchProcess:
            break  # Process might have exited quickly in test env
        time.sleep(0.1)

    assert running_title_found, (
        f"Manager process title did not update to 'running'. Last seen: {manager_process.name()}"
    )
