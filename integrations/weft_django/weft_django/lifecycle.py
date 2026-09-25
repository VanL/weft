"""Request-local lifecycle ownership for Django-facing Weft clients.

The registry copies Django's owner-local connection-handler pattern while
keeping Weft outside the ORM connection registry. It retains a client only for
an active synchronous request and never resolves settings or touches a broker
from a lifecycle signal by itself.

Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-3.1], [DJ-13.2].
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from asgiref.local import Local
from django.core.signals import request_finished, request_started
from django.test.signals import setting_changed

if TYPE_CHECKING:
    from weft_django.client import DjangoWeftClient

logger = logging.getLogger(__name__)

REQUEST_STARTED_DISPATCH_UID: Final[str] = "weft_django.lifecycle.request_started"
REQUEST_FINISHED_DISPATCH_UID: Final[str] = "weft_django.lifecycle.request_finished"
SETTING_CHANGED_DISPATCH_UID: Final[str] = "weft_django.lifecycle.setting_changed"

_RELEVANT_SETTINGS: Final[frozenset[str]] = frozenset({"WEFT_DJANGO", "BASE_DIR"})


class _SignalConnector(Protocol):
    """Subset of Django's public Signal API used for registration."""

    def connect(
        self,
        receiver: Callable[..., Any],
        *,
        dispatch_uid: str,
        weak: bool,
    ) -> Any: ...


@dataclass(slots=True)
class _ClientRecord:
    """One replaceable owner-local client entry."""

    pid: int
    generation: int
    client: DjangoWeftClient
    cleanup_pending: bool = False


_owner_pid = os.getpid()
_generation = 0
_generation_lock = threading.Lock()
_local = Local(thread_critical=True)


def _guard_process() -> None:
    """Replace inherited registry primitives before touching either one."""

    global _generation, _generation_lock, _local, _owner_pid

    current_pid = os.getpid()
    if current_pid == _owner_pid:
        return

    inherited_local = _local
    _owner_pid = current_pid
    _generation = 0
    _generation_lock = threading.Lock()
    _local = Local(thread_critical=True)

    inherited_record = getattr(inherited_local, "client_record", None)
    if inherited_record is not None:
        # WeftClient.close() owns fork-safe inherited-session detachment. Read
        # only the fork-surviving current owner context; never traverse parent
        # thread entries.
        inherited_record.client.close()


def _current_generation() -> int:
    _guard_process()
    with _generation_lock:
        return _generation


def _request_is_active() -> bool:
    return bool(getattr(_local, "request_active", False))


def _set_request_active(active: bool) -> None:
    _local.request_active = active


def _current_record() -> _ClientRecord | None:
    return getattr(_local, "client_record", None)


def _has_current_record() -> bool:
    """Return whether the current owner context retains a client."""

    _guard_process()
    return _current_record() is not None


def _delete_current_record() -> None:
    try:
        del _local.client_record
    except AttributeError:
        pass


def _close_current_record() -> None:
    """Close and delete the current record, preserving it on failure."""

    record = _current_record()
    if record is None:
        return
    try:
        record.client.close()
    except BaseException:
        record.cleanup_pending = True
        raise
    _delete_current_record()


def _in_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def get_current_client(
    factory: Callable[[], DjangoWeftClient],
) -> DjangoWeftClient:
    """Return the retained synchronous-request client or a fresh one-shot client.

    A stale or cleanup-pending record is closed before any replacement is
    admitted. Direct async callers and callers outside an active request always
    receive an unentered client from ``factory``.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-13.2]
    """

    _guard_process()
    generation = _current_generation()
    record = _current_record()
    request_active = _request_is_active()

    if record is not None and (
        record.cleanup_pending
        or record.pid != _owner_pid
        or record.generation != generation
        or not request_active
    ):
        _close_current_record()
        record = None

    if _in_running_event_loop() or not request_active:
        return factory()

    if record is None:
        client = factory()
        client.__enter__()
        record = _ClientRecord(
            pid=_owner_pid,
            generation=generation,
            client=client,
        )
        _local.client_record = record
    return record.client


def request_started_receiver(sender: object, **kwargs: Any) -> None:
    """Reset stale current-owner state and activate a request scope."""

    del sender, kwargs
    _guard_process()
    _set_request_active(False)
    _close_current_record()
    _set_request_active(True)


def request_finished_receiver(sender: object, **kwargs: Any) -> None:
    """Close this request's client, retrying one ordinary failure immediately."""

    del sender, kwargs
    _guard_process()
    _set_request_active(False)
    if _current_record() is None:
        return

    try:
        _close_current_record()
    except Exception as first_error:
        logger.warning(
            "Weft request client cleanup failed; retrying once on its owner thread",
            exc_info=first_error,
            extra={
                "owner_pid": _owner_pid,
                "owner_thread": threading.current_thread().name,
            },
        )
        try:
            _close_current_record()
        except Exception as retry_error:
            logger.error(
                "Weft request client remains cleanup-pending after retry; "
                "the owner lifecycle invariant was violated and safe recovery "
                "requires process recycling",
                exc_info=retry_error,
                extra={
                    "owner_pid": _owner_pid,
                    "owner_thread": threading.current_thread().name,
                },
            )


def setting_changed_receiver(
    sender: object,
    *,
    setting: str,
    **kwargs: Any,
) -> None:
    """Advance settings generation and rotate only the current owner's client."""

    del sender, kwargs
    _guard_process()
    if setting not in _RELEVANT_SETTINGS:
        return

    global _generation
    with _generation_lock:
        _generation += 1

    try:
        _close_current_record()
    except Exception as error:
        logger.error(
            "Weft request client is cleanup-pending after a settings change",
            exc_info=error,
            extra={
                "owner_pid": _owner_pid,
                "setting": setting,
                "owner_thread": threading.current_thread().name,
            },
        )


def register_lifecycle_signals(
    *,
    request_started_signal: _SignalConnector = request_started,
    request_finished_signal: _SignalConnector = request_finished,
    setting_changed_signal: _SignalConnector = setting_changed,
) -> None:
    """Register idempotent synchronous lifecycle receivers."""

    request_started_signal.connect(
        request_started_receiver,
        dispatch_uid=REQUEST_STARTED_DISPATCH_UID,
        weak=False,
    )
    request_finished_signal.connect(
        request_finished_receiver,
        dispatch_uid=REQUEST_FINISHED_DISPATCH_UID,
        weak=False,
    )
    setting_changed_signal.connect(
        setting_changed_receiver,
        dispatch_uid=SETTING_CHANGED_DISPATCH_UID,
        weak=False,
    )
