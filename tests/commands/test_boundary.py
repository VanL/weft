"""Public command error-boundary tests [PY-2]."""

from __future__ import annotations

import pytest

from weft._exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    CommandUsageError,
)
from weft.commands._boundary import typed_command_errors, typed_queue_command_errors

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("bad value"), CommandUsageError),
        (TimeoutError("too slow"), CommandTimeoutError),
        (RuntimeError("broken"), CommandExecutionError),
        (TypeError("programmer defect"), CommandExecutionError),
    ],
)
def test_typed_command_errors_translates_eager_failures(
    error: Exception,
    expected: type[Exception],
) -> None:
    @typed_command_errors
    def fail() -> None:
        raise error

    with pytest.raises(expected, match=str(error)):
        fail()


def test_queue_boundary_scopes_type_error_to_usage() -> None:
    @typed_queue_command_errors
    def fail() -> None:
        raise TypeError("bad queue argument")

    with pytest.raises(CommandUsageError, match="bad queue argument"):
        fail()


def test_typed_command_errors_translates_lazy_failure_and_closes_source() -> None:
    closed: list[bool] = []

    def source():
        try:
            yield 1
            raise ValueError("lazy failure")
        finally:
            closed.append(True)

    @typed_command_errors
    def stream():
        return source()

    result = stream()
    assert next(result) == 1
    with pytest.raises(CommandUsageError, match="lazy failure"):
        next(result)
    assert closed == [True]


def test_lazy_failure_is_not_masked_when_cleanup_also_fails() -> None:
    class BrokenIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise ValueError("iteration failed")

        def close(self) -> None:
            raise RuntimeError("cleanup failed")

    @typed_command_errors
    def command():
        return BrokenIterator()

    with pytest.raises(CommandUsageError, match="iteration failed"):
        next(command())


@pytest.mark.parametrize("exhausted", [False, True])
def test_stream_cleanup_failure_is_translated(exhausted: bool) -> None:
    class BrokenCloseIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self) -> None:
            raise RuntimeError("cleanup failed")

    @typed_command_errors
    def command():
        return BrokenCloseIterator()

    stream = command()
    with pytest.raises(CommandExecutionError, match="cleanup failed"):
        if exhausted:
            next(stream)
        else:
            stream.close()


def test_typed_command_errors_stream_close_is_idempotent() -> None:
    closed: list[bool] = []

    def source():
        try:
            yield 1
        finally:
            closed.append(True)

    @typed_command_errors
    def stream():
        return source()

    result = stream()
    result.close()
    result.close()
    assert closed == []
