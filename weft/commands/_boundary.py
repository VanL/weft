"""Private typed-error translation for public command functions [PY-2]."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from weft._exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    CommandUsageError,
    WeftError,
)


def typed_command_errors[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Translate raw implementation failures at one public command seam."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except WeftError:
            raise
        except TimeoutError as exc:
            raise CommandTimeoutError(str(exc)) from exc
        except ValueError as exc:
            raise CommandUsageError(str(exc)) from exc
        except Exception as exc:
            raise CommandExecutionError(str(exc)) from exc

    return wrapped
