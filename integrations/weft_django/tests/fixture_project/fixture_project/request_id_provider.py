from __future__ import annotations

from contextvars import ContextVar

_CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar(
    "weft_django_fixture_request_id",
    default=None,
)


def get_current() -> str | None:
    return _CURRENT_REQUEST_ID.get()


def set_current(value: str | None) -> None:
    _CURRENT_REQUEST_ID.set(value)
