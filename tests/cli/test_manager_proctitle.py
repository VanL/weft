"""Test for manager process title updates."""

from __future__ import annotations

import json
import os
import time

import psutil
import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows process names do not expose setproctitle updates reliably",
)


def test_manager_proctitle_updates_to_running(weft_harness: WeftTestHarness) -> None:
    """Verify that the manager's process title updates from 'init' to 'running'."""
    # Starting explicitly keeps the observation independent of a short task.
    rc, out, err = run_cli(
        "manager",
        "start",
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    rc, out, err = run_cli(
        "manager",
        "list",
        "--json",
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)

    # Read the explicit manager's PID from its structured registry projection.
    manager_pid = None
    for data in json.loads(out):
        try:
            if isinstance(data, dict) and data.get("status") == "active":
                handle = data.get("runtime_handle")
                observations = (
                    handle.get("observations") if isinstance(handle, dict) else {}
                )
                host_pids = (
                    observations.get("host_pids")
                    if isinstance(observations, dict)
                    else []
                )
                assert isinstance(host_pids, list)
                manager_pid = next(
                    (pid for pid in host_pids if isinstance(pid, int) and pid > 0),
                    None,
                )
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
    title = "<not observed>"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
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
        f"Manager process title did not update to 'running'. Last seen: {title}"
    )
