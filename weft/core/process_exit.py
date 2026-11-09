"""Kernel process-exit sources: one OS sentinel per host process.

:func:`open_process_exit_source` turns a host PID into a waitable kernel
object that becomes ready when that process exits, without reaping it. The
process need not be the caller's child. One narrow leaf per platform:

- Linux: a pidfd, through Python's ``os.pidfd_open`` or libc's binding.
- macOS and BSD: a kqueue holding a one-shot ``NOTE_EXIT`` process filter;
  the kqueue's own descriptor is what a caller waits on.
- Windows: a process handle opened with ``SYNCHRONIZE`` access.

``fileno()`` is the value a waiter passes on: a selectable descriptor on
POSIX, a wait handle on Windows (``multiprocessing.connection.wait`` treats a
plain integer as a handle there). The subprocess runner's POSIX readiness
adapter registers the source in its selector; the Manager's child-sentinel
adapter waits on it for a managed process that outlived its wrapper.

Spec references:
- docs/specifications/06-Resource_Management.md [RM-5.2]
- docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-3]
- docs/program-theory.md [WT-4.3] (OS sentinel adapters, never defensive
  polling), [WT-4.4] (one path on all platforms; narrow adapter leaves)
"""

from __future__ import annotations

import errno
import os
import select
import sys
from collections.abc import Callable
from typing import Protocol, cast

from weft._constants import WINDOWS_ERROR_INVALID_PARAMETER
from weft.core.deferred import get_ctypes

if sys.platform == "win32":
    import _winapi


class ProcessExitSource(Protocol):
    """An owned kernel object that becomes ready when one process exits."""

    def fileno(self) -> int: ...

    def close(self) -> None: ...


class _DescriptorExitSource:
    """An owned process-exit descriptor (a Linux pidfd)."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor

    def close(self) -> None:
        os.close(self._descriptor)


if sys.platform == "win32":

    class _HandleExitSource:
        """An owned Windows process handle, signalled when the process exits."""

        def __init__(self, handle: int) -> None:
            self._handle = handle

        def fileno(self) -> int:
            return self._handle

        def close(self) -> None:
            _winapi.CloseHandle(self._handle)


def _linux_pidfd_opener() -> Callable[[int], int]:
    """Select Python's pidfd wrapper or bind libc before classifying syscall errors.

    Dependency/loader acquisition must stay outside the syscall-only error
    handler in `open_process_exit_source`. The ctypes function retains its CDLL
    owner; each acquisition binds locally without shared mutable FFI state.

    Spec: docs/specifications/06-Resource_Management.md [RM-5.2].
    """
    python_opener = getattr(os, "pidfd_open", None)
    if python_opener is not None:
        return cast("Callable[[int], int]", python_opener)

    try:
        ctypes = get_ctypes()
        libc = ctypes.CDLL(None, use_errno=True)
        native = libc.pidfd_open
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "Process exit supervision cannot open pidfds: Python lacks "
            "os.pidfd_open and ctypes or libc's pidfd_open binding is unavailable "
            f"(Python {sys.version.split()[0]} at {sys.executable}): {exc}"
        ) from exc

    native.argtypes = [ctypes.c_int, ctypes.c_uint]
    native.restype = ctypes.c_int

    def open_native(pid: int) -> int:
        descriptor = cast("int", native(pid, 0))
        if descriptor == -1:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return descriptor

    return open_native


def open_process_exit_source(pid: int) -> ProcessExitSource | None:
    """Open a waitable kernel object that becomes ready when ``pid`` exits.

    Linux uses a pidfd through Python or libc; macOS and BSD use a kqueue
    holding a one-shot process-exit filter, whose own descriptor is the
    waitable; Windows opens the process handle with ``SYNCHRONIZE`` access.
    None reaps the process or touches ``Popen._waitpid_lock``. ``None`` means
    the process is already gone, which the caller treats as an immediate
    wake. The caller owns the returned source and closes it.

    Spec: [RM-5.2], [MA-1.6a]
    """
    if sys.platform == "linux":
        pidfd_open = _linux_pidfd_opener()
        try:
            return _DescriptorExitSource(pidfd_open(pid))
        except ProcessLookupError:
            return None
        except OSError as exc:
            if exc.errno not in (
                errno.ENOSYS,
                errno.EPERM,
                errno.EACCES,
            ):
                raise
            raise RuntimeError(
                "Process exit supervision requires Linux 5.3+ with pidfd_open "
                "supported by the kernel and permitted by the container/security policy: "
                f"{exc}"
            ) from exc
    if sys.platform == "win32":
        try:
            handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
        except OSError as exc:
            if exc.winerror == WINDOWS_ERROR_INVALID_PARAMETER:
                return None
            raise
        return _HandleExitSource(handle)
    kq = select.kqueue()
    try:
        kq.control(
            [
                select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
            ],
            0,
            0,
        )
    except ProcessLookupError:
        kq.close()
        return None
    except BaseException:
        kq.close()
        raise
    return kq
