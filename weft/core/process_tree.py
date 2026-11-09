"""Descendant enumeration proportional to a process's own tree.

``psutil.Process.children(recursive=True)`` answers "which processes descend
from this one?" by reading the parent PID of every process on the host
(one ``sysctl`` per process on macOS, one ``/proc/<pid>/stat`` read per
process on Linux). Its cost therefore grows with the host's process count,
not with the task's tree. :func:`descendant_processes` keeps that function's
contract and replaces only the enumeration with narrow platform leaves that
ask the kernel for one parent's children at a time:

- macOS: libproc ``proc_listpids(PROC_PPID_ONLY, ppid, ...)``.
- Linux: ``/proc/<pid>/task/<tid>/children`` for every thread of the parent
  (the kernel records a child under the thread that forked it).
- Elsewhere, or when the platform source is unavailable: psutil itself.

Spec references:
- docs/specifications/06-Resource_Management.md [RM-5.1]
- docs/program-theory.md [WT-4.4] (one path on all platforms; platform
  branches are narrow adapter leaves)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Final

import psutil

from weft.core.deferred import get_ctypes

logger = logging.getLogger(__name__)

_PROC_PPID_ONLY: Final[int] = 6
"""libproc ``proc_listpids`` type selecting processes by parent PID."""

_DARWIN_CHILD_PID_CAPACITY: Final[int] = 64
"""Initial child-PID buffer; a full buffer is grown and the call repeated."""

ChildPidReader = Callable[[int], list[int]]


def descendant_processes(root: psutil.Process) -> list[psutil.Process]:
    """Return every descendant of ``root``, as ``children(recursive=True)`` does.

    The contract matches psutil's: ``root`` must still be the process it was
    created for (``psutil.NoSuchProcess`` otherwise); a child is kept, and
    walked, only when it is not older than ``root``, which rejects a child
    whose PID was reused; a process that exits during the walk is skipped.
    Only the source of child PIDs differs.

    Spec: [RM-5.1]
    """
    read_child_pids = _child_pid_reader()
    if read_child_pids is None:
        return root.children(recursive=True)

    if not root.is_running():
        raise psutil.NoSuchProcess(root.pid)
    root_create_time = root.create_time()
    descendants: list[psutil.Process] = []
    seen: set[int] = set()
    stack = [root.pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for child_pid in read_child_pids(pid):
            try:
                child = psutil.Process(child_pid)
                if root_create_time <= child.create_time():
                    descendants.append(child)
                    stack.append(child_pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
    return descendants


@cache
def _child_pid_reader() -> ChildPidReader | None:
    """Select this platform's per-parent child-PID leaf, or ``None`` for psutil."""
    if sys.platform == "darwin":
        return _darwin_child_pid_reader()
    if sys.platform.startswith("linux"):
        return _linux_child_pid_reader()
    return None


def _darwin_child_pid_reader() -> ChildPidReader | None:
    try:
        ctypes = get_ctypes()
        list_pids = ctypes.CDLL(None, use_errno=True).proc_listpids
    except (ImportError, OSError, AttributeError):
        logger.warning(
            "libproc proc_listpids is unavailable; descendant enumeration uses psutil",
            exc_info=True,
        )
        return None
    list_pids.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    list_pids.restype = ctypes.c_int
    pid_size = ctypes.sizeof(ctypes.c_int)

    def read_child_pids(pid: int) -> list[int]:
        capacity = _DARWIN_CHILD_PID_CAPACITY
        while True:
            buffer = (ctypes.c_int * capacity)()
            filled = int(list_pids(_PROC_PPID_ONLY, pid, buffer, ctypes.sizeof(buffer)))
            if filled < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
            count = filled // pid_size
            if count < capacity:
                return [child for child in buffer[:count] if child > 0]
            capacity *= 2

    return read_child_pids


def _linux_child_pid_reader() -> ChildPidReader | None:
    own_children = Path(f"/proc/self/task/{threading.get_native_id()}/children")
    if not own_children.is_file():
        logger.warning(
            "/proc task children files are unavailable (CONFIG_PROC_CHILDREN); "
            "descendant enumeration uses psutil"
        )
        return None

    def read_child_pids(pid: int) -> list[int]:
        try:
            tasks = list(Path(f"/proc/{pid}/task").iterdir())
        except (FileNotFoundError, ProcessLookupError):
            return []
        child_pids: list[int] = []
        for task in tasks:
            try:
                text = (task / "children").read_text()
            except (FileNotFoundError, ProcessLookupError):
                continue
            child_pids.extend(int(token) for token in text.split())
        return child_pids

    return read_child_pids


__all__ = ["descendant_processes"]
