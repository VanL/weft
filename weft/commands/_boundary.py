"""Private typed-error translation for public command functions [PY-2]."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import wraps
from typing import Any, cast

from weft._exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    CommandUsageError,
    WeftError,
)


def _translate_command_error(exc: Exception, *, type_error_is_usage: bool) -> None:
    """Raise the public typed equivalent of one implementation failure."""

    if isinstance(exc, WeftError):
        raise exc
    if isinstance(exc, TimeoutError):
        raise CommandTimeoutError(str(exc)) from exc
    if isinstance(exc, ValueError) or (
        type_error_is_usage and isinstance(exc, TypeError)
    ):
        raise CommandUsageError(str(exc)) from exc
    raise CommandExecutionError(str(exc)) from exc


class _TranslatedIterator[T](Iterator[T]):
    """Closable iterator that translates generator-lazy command failures."""

    def __init__(self, source: Iterator[T], *, type_error_is_usage: bool) -> None:
        self._source = source
        self._type_error_is_usage = type_error_is_usage
        self._closed = False

    def __iter__(self) -> _TranslatedIterator[T]:
        return self

    def __next__(self) -> T:
        if self._closed:
            raise StopIteration
        try:
            return next(self._source)
        except StopIteration:
            self.close()
            raise
        except Exception as exc:
            self._close_source(translate=False)
            _translate_command_error(
                exc,
                type_error_is_usage=self._type_error_is_usage,
            )
            raise AssertionError("unreachable") from exc

    def close(self) -> None:
        self._close_source(translate=True)

    def _close_source(self, *, translate: bool) -> None:
        """Close once, optionally translating a cleanup failure."""

        if self._closed:
            return
        self._closed = True
        close = getattr(self._source, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-370] exception
                if translate:
                    _translate_command_error(
                        exc,
                        type_error_is_usage=self._type_error_is_usage,
                    )


def _typed_command_errors[**P, R](
    function: Callable[P, R],
    *,
    type_error_is_usage: bool,
) -> Callable[P, R]:
    """Build one eager-and-lazy public command translation boundary."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            _translate_command_error(exc, type_error_is_usage=type_error_is_usage)
            raise AssertionError("unreachable") from exc
        if isinstance(result, Iterator):
            return cast(
                R,
                _TranslatedIterator(
                    cast(Iterator[Any], result),
                    type_error_is_usage=type_error_is_usage,
                ),
            )
        return result

    cast(Any, wrapped)._weft_typed_command_errors = True
    return wrapped


def typed_command_errors[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Translate raw failures; programmer `TypeError` remains execution failure."""

    return _typed_command_errors(function, type_error_is_usage=False)


def typed_queue_command_errors[**P, R](
    function: Callable[P, R],
) -> Callable[P, R]:
    """Translate queue-boundary `TypeError` as caller usage failure."""

    return _typed_command_errors(function, type_error_is_usage=True)
