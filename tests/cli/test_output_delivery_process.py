"""Process-boundary spill delivery and context handoff proofs [MF-2.1]."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, run_cli
from tests.helpers.weft_harness import WeftTestHarness
from tests.taskspec.fixtures import (
    create_valid_agent_taskspec,
    create_valid_function_taskspec,
)

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("spilled", [False, True])
def test_result_closed_stdout_keeps_exit_code_and_spill(
    weft_harness: WeftTestHarness, tmp_path: Path, spilled: bool
) -> None:
    context = weft_harness.context
    size = (11 if spilled else 1) * 1024 * 1024
    rc, tid, err = run_cli(
        "run",
        "--no-wait",
        sys.executable,
        "-c",
        f"print('p' * {size})",
        cwd=context.root,
        harness=weft_harness,
        stdin="",
        timeout=60,
    )
    assert rc == 0, err
    weft_harness.wait_for_completion(tid)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(REPO_ROOT), env.get("PYTHONPATH", "")))
    env.pop("__PYVENV_LAUNCHER__", None)
    diagnostic = tmp_path / "closed-pipe.stderr"
    with (
        diagnostic.open("wb") as error_file,
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "weft",
                "result",
                tid,
                "--context",
                str(context.root),
            ],
            cwd=tmp_path,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=error_file,
        ) as process,
    ):
        assert process.stdout is not None
        try:
            assert process.stdout.read(1) == b"p"
            process.stdout.close()
            return_code = process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    error = diagnostic.read_text()
    assert return_code == 1, error
    assert "Traceback" not in error
    assert "Exception ignored" not in error
    spills = list(context.outputs_dir.joinpath(tid).glob("*.dat"))
    if spilled:
        assert "incomplete output" in error
        assert len(spills) == 1
        assert spills[0].read_bytes() == b"p" * size
        assert str(spills[0]) in error
    else:
        assert spills == []


def test_waiting_stored_spec_uses_rebound_custom_outputs_root(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime with spaces"
    runtime_root.mkdir()
    spec = create_valid_function_taskspec(
        function_target="tests.tasks.sample_targets:large_output"
    ).model_dump(mode="json")
    spec["spec"].update(
        args=[],
        keyword_args={"size": 2 * 1024 * 1024},
        weft_context=str(runtime_root),
        output_size_limit_mb=1,
    )
    spec_path = tmp_path / "large-task.json"
    spec_path.write_text(json.dumps(spec))
    env = os.environ.copy()
    env["WEFT_DIRECTORY_NAME"] = ".custom-weft"
    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--context",
        weft_harness.context.root,
        cwd=tmp_path,
        harness=weft_harness,
        stdin="",
        env=env,
        timeout=60,
    )
    assert rc == 0, err
    assert out == "x" * (2 * 1024 * 1024)
    files = list((runtime_root / ".custom-weft" / "outputs").glob("*/*.dat"))
    assert len(files) == 1
    assert files[0].read_bytes() == out.encode()


def test_waiting_spill_resolves_symlinked_context_root(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    alias = tmp_path / "context-link"
    alias.symlink_to(weft_harness.context.root, target_is_directory=True)
    rc, out, err = run_cli(
        "run",
        sys.executable,
        "-c",
        "print('s' * (11 * 1024 * 1024))",
        "--context",
        alias,
        cwd=tmp_path,
        harness=weft_harness,
        stdin="",
        timeout=60,
    )
    assert rc == 0, err
    assert out == "s" * (11 * 1024 * 1024)


def test_agent_multiple_spills_deliver_in_order_without_overwriting(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    spec = create_valid_agent_taskspec().model_dump(mode="json")
    spec["spec"].update(
        output_size_limit_mb=1,
        weft_context=str(weft_harness.context.root),
    )
    spec["spec"]["agent"].update(
        output_mode="messages",
        model="weft-test-spill-pair-model",
        tools=[
            {
                "name": "echo",
                "kind": "python",
                "ref": "tests.tasks.sample_targets:echo_payload",
            }
        ],
    )
    spec_path = tmp_path / "agent-spill-pair.json"
    spec_path.write_text(json.dumps(spec))
    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=weft_harness.context.root,
        harness=weft_harness,
        stdin=json.dumps({"task": "__spill_pair__"}),
        timeout=60,
    )
    assert rc == 0, err
    messages = [json.loads(line) for line in out.splitlines()]
    assert messages == [
        {"role": "assistant", "content": "a" * (2 * 1024 * 1024)},
        {"role": "assistant", "content": "b" * (2 * 1024 * 1024)},
    ]
    spills = list(weft_harness.context.outputs_dir.glob("*/*.dat"))
    assert len(spills) == 2
    contents = [json.loads(path.read_text()) for path in sorted(spills)]
    assert contents == messages
