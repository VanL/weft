"""Utilities for constructing managed callables that execute external processes.

This module provides a `make_callable` factory that returns a small, reusable
wrapper around :mod:`subprocess` execution.  The wrapper captures useful
instrumentation (timings, output, return codes) so higher-level task runners can
observe and enforce policy around target processes.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManagedProcessResult:
    """Execution metadata produced by a managed callable."""

    command: tuple[str, ...]
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        """Total wall-clock duration in seconds."""
        return self.end_time - self.start_time

    @property
    def success(self) -> bool:
        """Whether the managed process exited with code 0."""
        return self.returncode == 0

    def check_returncode(self) -> None:
        """Raise :class:`CalledProcessError` if the process failed."""
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode,
                self.command,
                output=self.stdout,
                stderr=self.stderr,
            )


def make_callable(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    inherit_env: bool = True,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> Callable[..., ManagedProcessResult]:
    """Create a callable that executes *command* with instrumentation.

    Args:
        command: Sequence representing the command to execute. Each element is
            coerced to :class:`str` and the resulting tuple is reused for every
            invocation.
        cwd: Optional working directory for the process.
        env: Optional environment variable overrides applied to each run.
        inherit_env: When True (default) merge *env* with :data:`os.environ`;
            otherwise run with only the supplied *env* mapping.
        text: Mirror of :func:`subprocess.run`'s *text* parameter. Defaults to
            True so stdout/stderr are decoded to :class:`str`.
        encoding: Encoding used when *text* is True.
        errors: Error handling strategy used when *text* is True.

    Returns:
        Callable that accepts ``stdin`` (optional, defaults to ``None``),
        ``timeout`` (optional float), and ``check`` (bool) keyword arguments and
        returns a :class:`ManagedProcessResult`.
    """
    command_tuple = tuple(str(part) for part in command)
    if not command_tuple:
        raise ValueError("command must contain at least one element")

    working_dir = Path(cwd).expanduser().resolve() if cwd is not None else None
    env_overrides = dict(env) if env is not None else None

    def _build_env() -> MutableMapping[str, str] | None:
        if inherit_env:
            merged = os.environ.copy()
            if env_overrides:
                merged.update(env_overrides)
            return merged
        if env_overrides is not None:
            return dict(env_overrides)
        return {}

    def runner(
        *,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        check: bool = False,
    ) -> ManagedProcessResult:
        start_time = time.monotonic()
        completed = subprocess.run(
            command_tuple,
            input=stdin,
            capture_output=True,
            text=text,
            encoding=encoding if text else None,
            errors=errors if text else None,
            cwd=str(working_dir) if working_dir is not None else None,
            env=_build_env(),
            timeout=timeout,
        )
        end_time = time.monotonic()

        result = ManagedProcessResult(
            command=command_tuple,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            start_time=start_time,
            end_time=end_time,
        )

        if check:
            result.check_returncode()

        return result

    return runner


__all__ = ["ManagedProcessResult", "make_callable"]
