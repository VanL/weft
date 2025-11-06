"""TTY-backed integration tests for interactive CLI commands."""

from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from pathlib import Path

import pytest

READ_SLICE = 0.05
DEFAULT_TIMEOUT = 5.0


@pytest.fixture
def tty_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spawn_with_pty(
    args: list[str], *, cwd: Path
) -> tuple[subprocess.Popen[bytes], int]:
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env.setdefault("WEFT_MANAGER_LIFETIME_TIMEOUT", "1.0")
    env.setdefault("WEFT_MANAGER_REUSE_ENABLED", "0")
    env.setdefault("WEFT_AUTOSTART_TASKS", "0")
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=os.setsid,
        env=env,
    )
    os.close(slave_fd)
    return proc, master_fd


def _read_until(fd: int, needle: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while time.monotonic() < deadline:
        rlist, _, _ = select.select([fd], [], [], READ_SLICE)
        if fd not in rlist:
            continue
        data = os.read(fd, 4096)
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        if needle in text or needle in "".join(chunks):
            break
    return "".join(chunks)


def _write_line(fd: int, line: str) -> None:
    os.write(fd, line.encode("utf-8"))


def _shutdown(proc: subprocess.Popen[bytes], master_fd: int) -> None:
    for action in (proc.terminate, proc.kill):
        try:
            action()
            proc.wait(timeout=1)
            break
        except Exception:
            continue
    try:
        os.close(master_fd)
    except OSError:
        pass


def test_interactive_python_repl_outputs(tty_workdir: Path) -> None:
    cmd = ["weft", "run", "-i", "python"]
    proc, master_fd = _spawn_with_pty(cmd, cwd=tty_workdir)
    try:
        banner = _read_until(master_fd, "weft>")
        if "weft>" not in banner:
            _shutdown(proc, master_fd)
            pytest.fail(f"weft prompt not detected; got {banner!r}")

        _write_line(master_fd, "print(2+2)\n")
        output = _read_until(master_fd, "4")
        if "4" not in output:
            _shutdown(proc, master_fd)
            pytest.fail(f"expected python output, saw {output!r}")

        _write_line(master_fd, "quit()\n")
        _read_until(master_fd, "")
    finally:
        _shutdown(proc, master_fd)
