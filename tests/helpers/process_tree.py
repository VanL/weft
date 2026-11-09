"""Spawn real process trees for descendant-enumeration and monitor tests.

The tree script runs ``depth`` levels below its root. Every non-leaf spawns
one child from its main thread and, with ``fanout=2``, a second child from a
worker thread that stays alive (Linux records a child under the thread that
forked it). Leaves may hold ``leaf_memory_mb`` of incompressible memory.
Each process publishes its PID by atomic rename into ``pid_dir`` once its own
children exist, so a complete ``pid_dir`` means the whole tree is up.

Spec: docs/specifications/06-Resource_Management.md [RM-5.1]
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

_TREE_SCRIPT = """
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    pid_dir = Path(sys.argv[1])
    depth = int(sys.argv[2])
    leaf_memory_mb = int(sys.argv[3])
    fanout = int(sys.argv[4])
    held: list[object] = []
    if depth > 0:
        argv = [sys.executable, __file__, str(pid_dir), str(depth - 1)]
        argv += [str(leaf_memory_mb), str(fanout)]
        held.append(subprocess.Popen(argv))
        if fanout > 1:
            spawned = threading.Event()

            def spawn_and_hold() -> None:
                held.append(subprocess.Popen(argv))
                spawned.set()
                time.sleep(120)

            threading.Thread(target=spawn_and_hold, daemon=True).start()
            spawned.wait()
    elif leaf_memory_mb:
        held.append(os.urandom(leaf_memory_mb * 1024 * 1024))
    staged = pid_dir / f".{os.getpid()}"
    staged.write_text(str(depth), encoding="utf-8")
    staged.rename(pid_dir / str(os.getpid()))
    time.sleep(120)


if __name__ == "__main__":
    main()
"""


def tree_command(
    tmp_path: Path, *, depth: int, fanout: int, leaf_memory_mb: int = 0
) -> tuple[list[str], Path]:
    """Return the argv that starts a tree and the directory it reports into."""

    script = tmp_path / "process_tree_fixture.py"
    script.write_text(_TREE_SCRIPT, encoding="utf-8")
    pid_dir = tmp_path / "tree_pids"
    pid_dir.mkdir(exist_ok=True)
    argv = [sys.executable, str(script), str(pid_dir), str(depth)]
    return [*argv, str(leaf_memory_mb), str(fanout)], pid_dir


def tree_size(*, depth: int, fanout: int) -> int:
    """Return the number of processes in a tree, root included."""

    return sum(fanout**level for level in range(depth + 1))


def wait_for_tree_pids(pid_dir: Path, *, count: int, timeout: float = 30.0) -> set[int]:
    """Return the published PIDs once ``count`` processes have reported."""

    deadline = time.monotonic() + timeout
    while True:
        pids = {int(path.name) for path in pid_dir.iterdir() if path.name.isdigit()}
        if len(pids) >= count:
            return pids
        if time.monotonic() >= deadline:
            raise AssertionError(f"only {len(pids)} of {count} tree processes started")
        time.sleep(0.02)


def kill_pids(pids: set[int]) -> None:
    """Kill every listed process that still exists and wait for it to go."""

    processes: list[psutil.Process] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.kill()
        except psutil.NoSuchProcess:
            continue
        processes.append(process)
    psutil.wait_procs(processes, timeout=5.0)


@dataclass
class SpawnedTree:
    """A running tree started by the test itself."""

    root: subprocess.Popen[bytes]
    pids: set[int]

    @property
    def descendant_pids(self) -> set[int]:
        return self.pids - {self.root.pid}

    def kill(self) -> None:
        kill_pids(self.descendant_pids)
        self.root.kill()
        self.root.wait(timeout=5.0)


def spawn_tree(
    tmp_path: Path, *, depth: int, fanout: int, leaf_memory_mb: int = 0
) -> SpawnedTree:
    """Start a tree under this test process and wait until it is complete."""

    argv, pid_dir = tree_command(
        tmp_path, depth=depth, fanout=fanout, leaf_memory_mb=leaf_memory_mb
    )
    root = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
    try:
        pids = wait_for_tree_pids(pid_dir, count=tree_size(depth=depth, fanout=fanout))
    except BaseException:
        started = {int(p.name) for p in pid_dir.iterdir() if p.name.isdigit()}
        kill_pids(started - {root.pid})
        root.kill()
        root.wait(timeout=5.0)
        raise
    return SpawnedTree(root=root, pids=pids)
