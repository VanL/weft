"""Real-file result delivery and producer lifecycle proofs [MF-2.1]."""

from __future__ import annotations

import hashlib
import io
import json
import socket
import sys
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import typer

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft.cli.run import render_run_result
from weft.commands._output import render_text_output as _render_text_output
from weft.commands.types import RunExecutionResult

pytestmark = [pytest.mark.shared]


def _echo(text: str, nl: bool) -> None:
    typer.echo(text, nl=nl)


render_text_output = partial(_render_text_output, emit_text=_echo)


def _reference(path: Path, content: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "type": "large_output",
        "path": str(path),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "hostname": socket.gethostname(),
    }


@pytest.mark.parametrize("damage", ["size", "digest", "missing", "truncated"])
def test_spill_delivery_reports_independent_integrity_failures(
    tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "outputs" / "1.dat"
    reference = _reference(path, b"result bytes")
    if damage == "size":
        reference["size"] += 1
        assert reference["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == "digest":
        path.write_bytes(b"Result bytes")
        assert path.stat().st_size == reference["size"]
    elif damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"result")
    with pytest.raises(OSError, match="incomplete output") as failure:
        render_text_output(reference, outputs_root=str(path.parent))
    assert str(path) in str(failure.value)


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("symlink", [False, True])
def test_spill_recognition_checks_host_before_resolved_root(
    tmp_path: Path, foreign: bool, symlink: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "outputs"
    root.mkdir()
    reference = _reference(tmp_path / "elsewhere" / "file", b"secret")
    if symlink:
        alias = root / "alias"
        alias.symlink_to(reference["path"])
        reference["path"] = str(alias)
    if foreign:
        reference["hostname"] = "other-host"
        with pytest.raises(OSError, match="only available on host other-host"):
            render_text_output(reference, outputs_root=str(root))
        assert capsys.readouterr().out == ""
    else:
        render_text_output(reference, outputs_root=str(root))
        assert json.loads(capsys.readouterr().out) == reference


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("size", -1),
        ("size", "5"),
        ("sha256", "a" * 63),
        ("sha256", "z" * 64),
        ("hostname", ""),
        ("hostname", 123),
        ("path", 123),
    ],
)
def test_malformed_spill_is_ordinary_output(
    tmp_path: Path, field: str, value: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = _reference(tmp_path / "1.dat", b"data")
    reference[field] = value
    render_text_output(reference, outputs_root=str(tmp_path))
    assert json.loads(capsys.readouterr().out) == reference


def test_top_level_list_delivery_and_ordinary_list_preservation(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    reference = _reference(tmp_path / "1.dat", b"contents")
    render_text_output(
        ["before", reference, {"after": True}], outputs_root=str(tmp_path)
    )
    assert capsysbinary.readouterr().out == b'before\ncontents\n{"after": true}\n'
    render_text_output([reference, [reference]], outputs_root=str(tmp_path))
    captured = capsysbinary.readouterr().out
    assert captured == b"contents\n" + json.dumps([reference]).encode() + b"\n"
    render_text_output([1, {"plain": 2}], outputs_root=str(tmp_path))
    assert json.loads(capsysbinary.readouterr().out) == [1, {"plain": 2}]


def test_all_json_preserves_reference_and_file(weft_harness: WeftTestHarness) -> None:
    harness = weft_harness
    tid = "1793910000000000001"
    reference = _reference(
        harness.context.outputs_dir / tid / "1.dat", b"retained content"
    )
    queue = harness.context.queue(f"T{tid}.outbox", persistent=True)
    queue.write(json.dumps(reference))
    queue.close()
    # An ordinary result without task metadata is deliberately tested through --all;
    # single-result lifecycle coverage below uses actual command processes.
    rc, out, err = run_cli(
        "result", "--all", "--json", cwd=harness.context.root, harness=harness
    )
    assert rc == 0, err
    assert json.loads(out)["results"][0]["result"] == reference
    assert Path(reference["path"]).read_bytes() == b"retained content"


def test_waiting_run_delivers_twenty_megabytes(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    rc, out, err = run_cli(
        "run",
        sys.executable,
        "-c",
        "print('x' * (20 * 1024 * 1024))",
        cwd=tmp_path,
        harness=weft_harness,
        timeout=60,
    )
    assert rc == 0, err
    assert out == "x" * (20 * 1024 * 1024)


@pytest.mark.parametrize("stream", [False, True])
def test_no_wait_spill_is_delivered_after_exit_from_another_directory(
    weft_harness: WeftTestHarness, tmp_path: Path, stream: bool
) -> None:
    context = weft_harness.context
    rc, tid, err = run_cli(
        "run",
        "--no-wait",
        sys.executable,
        "-c",
        "print('y' * (11 * 1024 * 1024))",
        cwd=context.root,
        harness=weft_harness,
        timeout=60,
    )
    assert rc == 0, err
    weft_harness.wait_for_completion(tid)
    arguments: list[object] = ["result", tid, "--context", context.root]
    if stream:
        arguments.append("--stream")
    rc, out, err = run_cli(*arguments, cwd=tmp_path, harness=weft_harness, timeout=60)
    assert rc == 0, err
    assert out == "y" * (11 * 1024 * 1024)
    assert any(context.outputs_dir.joinpath(tid).glob("*.dat"))


def test_ordinary_result_preserves_cli_ansi_rendering() -> None:
    # Both callers supply the CLI emitter; the helper must not bypass it.
    captured = io.StringIO()
    with redirect_stdout(captured):
        assert (
            render_run_result(
                RunExecutionResult(
                    tid="1793910000000000001",
                    status="completed",
                    result_value="\x1b[31mred\x1b[0m",
                ),
                wait=True,
                json_output=False,
                verbose=False,
            )
            == 0
        )
    assert captured.getvalue() == "red\n"
