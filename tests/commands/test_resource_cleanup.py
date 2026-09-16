"""Command resource cleanup exception-priority tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack

import pytest

from weft.commands._resources import close_command_resources

pytestmark = pytest.mark.shared


def _failing_stack(error: BaseException) -> ExitStack:
    stack = ExitStack()

    def fail() -> None:
        raise error

    stack.callback(fail)
    return stack


def test_cleanup_failure_propagates_without_body_failure() -> None:
    failure = RuntimeError("cleanup failed")

    with pytest.raises(RuntimeError) as exc_info:
        close_command_resources(_failing_stack(failure), body_error=None)

    assert exc_info.value is failure


def test_cleanup_failure_stays_secondary_to_application_failure() -> None:
    body_failure = ValueError("body failed")
    cleanup_failure = RuntimeError("cleanup failed")

    close_command_resources(
        _failing_stack(cleanup_failure),
        body_error=body_failure,
    )

    assert body_failure.__notes__ == [
        "Command resource cleanup failed: RuntimeError('cleanup failed')"
    ]


def test_cleanup_failure_reaches_suspended_generator_close_caller() -> None:
    failure = RuntimeError("cleanup failed")

    def stream() -> Generator[int, None, None]:
        resources = _failing_stack(failure)
        body_error: BaseException | None = None
        try:
            yield 1
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            close_command_resources(resources, body_error=body_error)

    iterator = stream()
    assert next(iterator) == 1

    with pytest.raises(RuntimeError) as exc_info:
        iterator.close()

    assert exc_info.value is failure
