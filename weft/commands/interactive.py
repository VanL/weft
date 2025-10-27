"""Interactive queue client for CLI sessions.

The CLI needs to translate streaming messages emitted by interactive tasks
into stdout/stderr updates while the user provides new input.  This module
wraps :class:`~weft.core.tasks.multiqueue_watcher.MultiQueueWatcher` so the
queue polling happens in a background thread, allowing the foreground thread
to focus on user interaction (prompt handling, signal management, etc.).

Spec references:
- docs/specifications/01-Core_Components.md (queue patterns, interactive tasks)
- docs/specifications/05-Message_Flow_and_State.md (message flow invariants)
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, cast

from simplebroker import Queue
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMessageContext,
    QueueMode,
)


class InteractiveStreamClient:
    """Bridge between queue streams and CLI callbacks.

    The client consumes three queues associated with an interactive task:

    * ``outbox`` – streaming stdout envelopes (JSON) emitted by the task
    * ``ctrl_out`` – stderr and control notifications
    * ``weft.tasks.log`` – state transitions for the task

    Callbacks are invoked from the watcher thread, so they must be
    thread-safe.  The CLI supplies lightweight closures that forward messages
    to the foreground thread (for example via queues or prompt_toolkit
    patching helpers).
    """

    def __init__(
        self,
        *,
        db_path: str,
        config: dict[str, Any],
        tid: str,
        inbox: str,
        outbox: str,
        ctrl_out: str,
        log_queue: str = WEFT_GLOBAL_LOG_QUEUE,
        on_stdout: Callable[[str, bool], None] | None = None,
        on_stderr: Callable[[str, bool], None] | None = None,
        on_state: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._db_path = db_path
        self._config = dict(config)
        self._tid = tid
        self._inbox_name = inbox
        self._outbox_name = outbox
        self._ctrl_out_name = ctrl_out
        self._log_queue_name = log_queue

        self._stdout_cb = on_stdout or (lambda _chunk, _final: None)
        self._stderr_cb = on_stderr or (lambda _chunk, _final: None)
        self._state_cb = on_state or (lambda _event: None)

        self._inbox_queue = Queue(
            inbox,
            db_path=db_path,
            persistent=True,
            config=self._config,
        )

        self._watcher: MultiQueueWatcher | None = None
        self._watcher_thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status: str | None = None
        self._error: str | None = None
        self._completion = threading.Event()
        self._log_last_timestamp: int | None = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start polling queues in a background thread."""

        if self._watcher is not None:
            return

        queue_configs = {
            self._outbox_name: {
                "handler": self._handle_outbox_message,
                "mode": QueueMode.READ,
            },
            self._ctrl_out_name: {
                "handler": self._handle_ctrl_message,
                "mode": QueueMode.READ,
            },
            self._log_queue_name: {
                "handler": self._handle_log_message,
                "mode": QueueMode.PEEK,
            },
        }

        watcher = MultiQueueWatcher(
            queue_configs,
            db=self._db_path,
            persistent=True,
            stop_event=threading.Event(),
            config=self._config,
        )
        self._watcher = watcher
        self._watcher_thread = watcher.run_in_thread()

    def stop(self) -> None:
        """Stop the watcher and signal completion."""

        if self._stopped:
            return
        self._stopped = True

        watcher = self._watcher
        if watcher is not None:
            watcher.stop(join=True)

        thread = self._watcher_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

        self._completion.set()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def send_input(self, data: str) -> None:
        """Send stdin data to the task."""

        payload = {"stdin": data}
        self._inbox_queue.write(json.dumps(payload))

    def close_input(self) -> None:
        """Indicate stdin has been closed by the user."""

        self._inbox_queue.write(json.dumps({"close": True}))

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a terminal state is observed (or the timeout expires)."""

        return self._completion.wait(timeout=timeout)

    @property
    def status(self) -> str | None:
        with self._status_lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._status_lock:
            return self._error

    # ------------------------------------------------------------------
    # Queue handlers (run on watcher thread)
    # ------------------------------------------------------------------
    def _handle_outbox_message(
        self, message: str, timestamp: int, _context: QueueMessageContext
    ) -> None:
        payload = self._maybe_parse_json(message)
        if isinstance(payload, dict) and payload.get("type") == "stream":
            stream = payload.get("stream")
            chunk = payload.get("data", "")
            is_final = bool(payload.get("final"))
            if stream == "stdout":
                self._stdout_cb(str(chunk), is_final)
                if is_final:
                    self._mark_completion(status="completed")
            elif stream == "stderr":
                self._stderr_cb(str(chunk), is_final)
            else:
                # Unknown stream type; surface via stderr callback for visibility
                self._stderr_cb(json.dumps(payload), is_final)
            return

        # Non-stream payloads fallback to stdout
        self._stdout_cb(str(payload), True)
        self._mark_completion(status="completed")

    def _handle_ctrl_message(
        self, message: str, _timestamp: int, _context: QueueMessageContext
    ) -> None:
        payload = self._maybe_parse_json(message)
        if isinstance(payload, dict) and payload.get("type") == "stream":
            stream = payload.get("stream")
            chunk = payload.get("data", "")
            is_final = bool(payload.get("final"))
            if stream == "stderr":
                self._stderr_cb(str(chunk), is_final)
            else:
                self._stdout_cb(json.dumps(payload), is_final)
            return

        # Control responses (STATUS/INFO) are surfaced to stderr for visibility
        self._stderr_cb(str(payload), False)

    def _handle_log_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        # Peek mode returns the oldest message repeatedly.  We fetch a batch and
        # only act on entries newer than the last one we processed.
        queue = context.queue
        try:
            entries = cast(
                list[tuple[str, int]] | None,
                queue.peek_many(limit=128, with_timestamps=True),
            )
        except Exception:
            entries = None

        processed = False
        if entries is not None:
            for body, entry_ts in entries:
                if (
                    self._log_last_timestamp is not None
                    and entry_ts <= self._log_last_timestamp
                ):
                    continue

                payload = self._maybe_parse_json(body)
                if not isinstance(payload, dict):
                    continue
                if payload.get("tid") != self._tid:
                    continue

                self._log_last_timestamp = entry_ts
                processed = True
                self._state_cb(payload)

                event = payload.get("event")
                if event == "work_completed":
                    self._mark_completion(status="completed")
                elif event in {"work_failed", "work_timeout", "work_limit_violation"}:
                    error = payload.get("error") or event.replace("_", " ")
                    self._mark_completion(status="failed", error=error)

        if not processed and self._log_last_timestamp is None:
            # Fallback for the very first message (already provided) so future
            # iterations can progress.
            payload = self._maybe_parse_json(message)
            if isinstance(payload, dict) and payload.get("tid") == self._tid:
                self._log_last_timestamp = timestamp
                self._state_cb(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mark_completion(self, *, status: str, error: str | None = None) -> None:
        with self._status_lock:
            if self._status in {"completed", "failed"}:
                return
            self._status = status
            self._error = error
        self._completion.set()

    @staticmethod
    def _maybe_parse_json(message: str) -> Any:
        try:
            return json.loads(message)
        except json.JSONDecodeError:
            return message


__all__ = ["InteractiveStreamClient"]
