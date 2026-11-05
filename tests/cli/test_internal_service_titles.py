"""Supervised internal tasks use ordinary process titles [OBS.4]."""

from __future__ import annotations

import json
import os
import time

import psutil
import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness

pytestmark = [
    pytest.mark.shared,
    pytest.mark.skipif(
        os.name == "nt", reason="Windows does not expose setproctitle updates"
    ),
]


def test_internal_services_carry_process_titles(weft_harness: WeftTestHarness) -> None:
    rc, out, err = run_cli(
        "manager", "start", cwd=weft_harness.root, stdin="", harness=weft_harness
    )
    assert rc == 0, (out, err)
    deadline = time.monotonic() + 15
    while True:
        rc, out, err = run_cli(
            "status", "--json", cwd=weft_harness.root, stdin="", harness=weft_harness
        )
        assert rc == 0, (out, err)
        services = [
            s for s in json.loads(out)["services"] if s.get("status") == "running"
        ]
        titled = {}
        for service in services:
            pid = service["pid"]
            process = psutil.Process(pid)
            title = process.name()
            if ":" not in title:
                title = process.cmdline()[0]
            titled[service["name"]] = title
        if any("heartbeat" in name for name in titled) and all(
            f":{name}:" in title for name, title in titled.items()
        ):
            break
        assert time.monotonic() < deadline, titled
        time.sleep(0.05)
