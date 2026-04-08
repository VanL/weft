"""Prompt-mode integration tests for interactive CLI commands."""

from __future__ import annotations

import os
import select
import subprocess
import time
from pathlib import Path

import pytest

from tests.helpers.test_backend import active_test_backend, prepare_project_root

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="PTY-backed interactive CLI test requires Unix PTY support",
)

pty = pytest.importorskip("pty")

READ_SLICE = 0.05
DEFAULT_TIMEOUT = 10.0
EXIT_TIMEOUT = 30.0


@pytest.fixture
def tty_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spawn_with_pty(
    args: list[str], *, cwd: Path
) -> tuple[subprocess.Popen[bytes], int]:
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    backend = active_test_backend(env)
    # PTY-backed interactive tests run under xdist and can spend noticeable
    # time waiting for prompts and child shutdown. Keep the manager lifetime
    # long enough that these tests are exercising interactive quit behavior,
    # not an artificial idle-timeout race.
    env["WEFT_MANAGER_LIFETIME_TIMEOUT"] = "5.0"
    env["WEFT_MANAGER_REUSE_ENABLED"] = "0"
    env["WEFT_AUTOSTART_TASKS"] = "0"
    if backend == "sqlite":
        env["WEFT_DEFAULT_DB_LOCATION"] = str(cwd)
        env["WEFT_DEFAULT_DB_NAME"] = "tty-weft.db"
        env["BROKER_DEFAULT_DB_LOCATION"] = str(cwd)
        env["BROKER_DEFAULT_DB_NAME"] = "tty-weft.db"
    prepare_project_root(cwd, env=env)
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


def _read_until(
    fd: int,
    needle: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    proc: subprocess.Popen[bytes] | None = None,
) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            break
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


def _read_until_exit(
    proc: subprocess.Popen[bytes], fd: int, *, timeout: float = DEFAULT_TIMEOUT
) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while time.monotonic() < deadline:
        rlist, _, _ = select.select([fd], [], [], READ_SLICE)
        if fd in rlist:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if data:
                chunks.append(data.decode("utf-8", errors="replace"))
        if proc.poll() is not None:
            break
    return "".join(chunks)


def test_interactive_python_repl_outputs(tty_workdir: Path) -> None:
    cmd = ["weft", "run", "-i", "python"]
    proc, master_fd = _spawn_with_pty(cmd, cwd=tty_workdir)
    try:
        banner = _read_until(master_fd, "weft>", proc=proc)
        if "weft>" not in banner:
            rc = proc.poll()
            trailing = _read_until_exit(proc, master_fd, timeout=1.0)
            _shutdown(proc, master_fd)
            pytest.fail(
                "weft prompt not detected; "
                f"returncode={rc!r} banner={banner!r} trailing={trailing!r}"
            )

        _write_line(master_fd, "print(2+2)\n")
        output = _read_until(master_fd, "4", proc=proc)
        if "4" not in output:
            _shutdown(proc, master_fd)
            pytest.fail(f"expected python output, saw {output!r}")

        _write_line(master_fd, ":quit\n")
        trailing = _read_until_exit(proc, master_fd, timeout=EXIT_TIMEOUT)
        rc = proc.poll()
        if rc is None:
            _shutdown(proc, master_fd)
            pytest.fail(
                f"interactive session did not exit after :quit; trailing={trailing!r}"
            )
        assert rc == 0, trailing
        assert "SyntaxError" not in trailing
    finally:
        _shutdown(proc, master_fd)


def test_interactive_quit_stops_child_that_ignores_eof(tty_workdir: Path) -> None:
    script = tty_workdir / "ignore_eof.py"
    script.write_text(
        "import time\nprint('ready', flush=True)\nwhile True:\n    time.sleep(1)\n",
        encoding="utf-8",
    )

    cmd = ["weft", "run", "-i", "--", "python", str(script)]
    proc, master_fd = _spawn_with_pty(cmd, cwd=tty_workdir)
    try:
        banner = _read_until(master_fd, "weft>", proc=proc)
        if "weft>" not in banner:
            rc = proc.poll()
            trailing = _read_until_exit(proc, master_fd, timeout=1.0)
            _shutdown(proc, master_fd)
            pytest.fail(
                "weft prompt not detected; "
                f"returncode={rc!r} banner={banner!r} trailing={trailing!r}"
            )

        ready_output = _read_until(master_fd, "ready", proc=proc)
        if "ready" not in ready_output:
            _shutdown(proc, master_fd)
            pytest.fail(f"expected child readiness output, saw {ready_output!r}")

        _write_line(master_fd, ":quit\n")
        trailing = _read_until_exit(proc, master_fd, timeout=EXIT_TIMEOUT)
        rc = proc.poll()
        if rc is None:
            _shutdown(proc, master_fd)
            pytest.fail(
                f"interactive session did not exit after :quit; trailing={trailing!r}"
            )
        assert rc == 0
    finally:
        _shutdown(proc, master_fd)
