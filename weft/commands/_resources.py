"""Command resource cleanup with explicit exception priority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager


def close_command_resources(
    resources: ExitStack,
    *,
    body_error: BaseException | None,
) -> None:
    """Close a command scope without replacing a genuine body failure.

    GeneratorExit is stream lifecycle control. A cleanup failure during
    ``generator.close()`` must reach the close caller instead of disappearing
    behind GeneratorExit.
    """

    try:
        resources.close()
    except BaseException as cleanup_error:
        if body_error is None or isinstance(body_error, GeneratorExit):
            raise
        body_error.add_note(f"Command resource cleanup failed: {cleanup_error!r}")


@contextmanager
def command_resource_scope() -> Iterator[ExitStack]:
    """Yield an ExitStack with command-boundary exception priority."""

    resources = ExitStack()
    body_error: BaseException | None = None
    try:
        yield resources
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        close_command_resources(resources, body_error=body_error)
