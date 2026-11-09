"""Real-file result delivery and producer lifecycle proofs [MF-2.1]."""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import typer

import weft.commands._output as output_module
from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft.cli.run import render_run_result
from weft.commands import tasks as task_commands
from weft.commands._output import render_text_output as _render_text_output
from weft.commands._output import write_command_stdout
from weft.commands.types import RunExecutionResult
from weft.helpers import closing_queue_iterator

pytestmark = [pytest.mark.shared]


def _echo(text: str, nl: bool) -> None:
    typer.echo(text, nl=nl)


def test_windows_closed_stdout_spill_silences_final_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "output.dat"
    path.write_bytes(b"payload")
    reference = _reference(path, b"payload")
    silenced: list[None] = []

    monkeypatch.setattr(output_module.sys, "platform", "win32")
    monkeypatch.setattr(
        output_module,
        "_write_stdout_bytes",
        lambda _data: (_ for _ in ()).throw(OSError(errno.EINVAL, "closed pipe")),
    )
    monkeypatch.setattr(
        output_module,
        "_silence_closed_stdout",
        lambda: silenced.append(None),
    )

    with pytest.raises(OSError, match="incomplete output"):
        _render_text_output(
            reference,
            outputs_root=str(tmp_path),
            emit_text=_echo,
        )

    assert silenced == [None]


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
        "--context",
        weft_harness.context.root,
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


@pytest.mark.parametrize(
    "case",
    [
        "inline",
        "whitespace",
        "empty",
        "crlf",
        "bare_cr",
        "json_scalar",
        "large",
        "escaping",
    ],
)
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("exit_code", [0, 4])
def test_captured_command_scalar_fidelity_and_wire_fit(
    weft_harness: WeftTestHarness, case: str, json_output: bool, exit_code: int
) -> None:
    cases = {
        "inline": ("'OUT\\n'", "OUT\n"),
        "whitespace": ("' \\n'", " \n"),
        "empty": ("''", ""),
        "crlf": ("'line\\r\\n'", "line\r\n"),
        "bare_cr": ("'line\\r'", "line\r"),
        "json_scalar": ("'1\\n'", "1\n"),
        "large": ("'x' * (11 * 1024 * 1024)", "x" * (11 * 1024 * 1024)),
        "escaping": ("'x\\n' * (4 * 1024 * 1024)", "x\n" * (4 * 1024 * 1024)),
    }
    expression, expected = cases[case]
    script = f"import sys; sys.stdout.buffer.write(({expression}).encode()); sys.stderr.write('ERR'); sys.exit({exit_code})"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    command = [sys.executable, "-m", "weft", "run"]
    if json_output:
        command.append("--json")
    command.extend([sys.executable, "-c", script])
    result = subprocess.run(
        command,
        cwd=weft_harness.context.root,
        env=env,
        input=b"",
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == (1 if exit_code else 0), result.stderr
    if json_output:
        payload = json.loads(result.stdout)
        assert payload["status"] == ("failed" if exit_code else "completed")
        if exit_code:
            assert payload["return_code"] == exit_code
            assert "ERR" in payload["error"]
        if case in {"large", "escaping"}:
            reference = payload["result"]
            assert reference["type"] == "large_output"
            assert Path(reference["path"]).read_bytes() == expected.encode()
        else:
            assert payload["result"] == expected
    else:
        assert result.stdout == expected.encode("utf-8")
        if exit_code:
            assert b"ERR" in result.stderr


@pytest.mark.parametrize("mode", ["single", "peek", "all"])
def test_background_failed_result_preserves_outcome_and_consumption(
    weft_harness: WeftTestHarness, mode: str
) -> None:
    rc, tid, err = run_cli(
        "run",
        "--no-wait",
        sys.executable,
        "-c",
        "import sys; print('background OUT'); sys.exit(5)",
        cwd=weft_harness.context.root,
        harness=weft_harness,
    )
    assert rc == 0, err
    # Single mode waits through ordinary startup discovery. Bulk modes inspect
    # only completed outputs, so wait for real failure evidence, not a sleep.
    if mode != "single":
        with closing_queue_iterator(
            task_commands.watch_task_status(
                tid, context=weft_harness.context, timeout=20
            )
        ) as snapshots:
            failure = next(
                snapshot for snapshot in snapshots if snapshot.status == "failed"
            )
        assert failure.return_code == 5
    args: list[object] = ["result", "--json"]
    if mode == "single":
        args.extend([tid, "--timeout", 20])
    else:
        args.append("--all")
        if mode == "peek":
            args.append("--peek")
    rc, out, err = run_cli(*args, cwd=weft_harness.context.root, harness=weft_harness)
    assert rc == 1, err
    payload = json.loads(out)
    item = (
        payload
        if mode == "single"
        else next(value for value in payload["results"] if value["tid"] == tid)
    )
    assert item["status"] == "failed"
    assert item["result"] == f"background OUT{os.linesep}"
    assert item["return_code"] == 5
    with weft_harness.context.queue(f"T{tid}.outbox") as queue:
        assert (queue.peek_one() is not None) is (mode == "peek")


@pytest.mark.parametrize("text", ["", "1\n", " \r\n\r"])
@pytest.mark.parametrize("json_output", [False, True])
def test_live_successful_command_preserves_stdout_text(
    weft_harness: WeftTestHarness, text: str, json_output: bool
) -> None:
    """The producer owns text and newline bytes, including JSON-looking text."""
    args = [sys.executable, "-m", "weft", "run", "--stream-output"]
    if json_output:
        args.append("--json")
    args.extend(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({text.encode()!r})",
        ]
    )
    result = subprocess.run(
        args,
        cwd=weft_harness.context.root,
        input=b"",
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if json_output:
        payload = json.loads(result.stdout)
        assert payload["result"] == text
        assert isinstance(payload["result"], str)
    else:
        assert result.stdout == text.encode("utf-8")


def test_command_text_writer_handles_short_file_writes_without_translation(
    tmp_path: Path,
) -> None:
    """A real unbuffered sink may accept a prefix; stdout must retain the rest."""

    class ShortFile(io.FileIO):
        def write(self, data: Any) -> int:
            return super().write(memoryview(data)[:7])

    path = tmp_path / "stdout.dat"
    text = "CRLF\r\nCR\rLF\nΩ\ud800" * 5
    with (
        ShortFile(path, "w") as binary,
        io.TextIOWrapper(binary, encoding="utf-8", newline="\r\n") as output,
        redirect_stdout(output),
    ):
        write_command_stdout(text)
    assert path.read_bytes() == text.encode("utf-8", errors="replace")


@pytest.mark.parametrize("exit_code", [0, 5])
@pytest.mark.parametrize("result_stream", [False, True])
def test_background_command_result_preserves_raw_stream_text(
    weft_harness: WeftTestHarness, exit_code: int, result_stream: bool
) -> None:
    text = " \r\nCR\r"
    rc, tid, error = run_cli(
        "run",
        "--no-wait",
        "--stream-output",
        sys.executable,
        "-c",
        f"import sys; sys.stdout.buffer.write({text.encode()!r}); sys.exit({exit_code})",
        cwd=weft_harness.context.root,
        harness=weft_harness,
    )
    assert rc == 0, error
    args = [sys.executable, "-m", "weft", "result", tid, "--timeout", "20"]
    if result_stream:
        args.append("--stream")
    result = subprocess.run(
        args,
        cwd=weft_harness.context.root,
        input=b"",
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        timeout=60,
        check=False,
    )
    assert result.returncode == (1 if exit_code else 0), result.stderr
    assert result.stdout == text.encode("utf-8")
