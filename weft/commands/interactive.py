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
import time
from collections.abc import Callable
from typing import Any

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMode,
)


class InteractiveStreamClient:
    """Bridge between queue streams and CLI callbacks.

    The client consumes the task-local interactive queues:

    * ``outbox`` – streaming stdout envelopes (JSON) emitted by the task
    * ``ctrl_out`` – stderr and control notifications
    * ``ctrl_out`` terminal envelopes for task-local completion state

    Callbacks are invoked from the watcher thread, so they must be
    thread-safe.  The CLI supplies lightweight closures that forward messages
    to the foreground thread (for example via queues or prompt_toolkit
    patching helpers).
    """

    def __init__(
        self,
        *,
        db_path: BrokerTarget | str,
        config: dict[str, Any],
        tid: str,
        inbox: str,
        outbox: str,
        ctrl_out: str,
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

        self._stdout_cb = on_stdout or (lambda _chunk, _final: None)
        self._stderr_cb = on_stderr or (lambda _chunk, _final: None)
        self._state_cb = on_state or (lambda _event: None)
        self._stdout_history: list[str] = []
        self._stderr_history: list[str] = []

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
        self._control_condition = threading.Condition()
        self._control_responses: list[dict[str, Any]] = []
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

        try:
            self._inbox_queue.close()
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - queue close best effort
            pass

        self._completion.set()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def send_input(self, data: str, *, close: bool = False) -> None:
        """Send stdin data to the task."""

        payload: dict[str, Any] = {"stdin": data}
        if close:
            payload["close"] = True
        self._inbox_queue.write(json.dumps(payload))

    def close_input(self) -> None:
        """Indicate stdin has been closed by the user."""

        self._inbox_queue.write(json.dumps({"close": True}))

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a terminal state is observed (or the timeout expires)."""

        return self._completion.wait(timeout=timeout)

    def wait_for_control_response(
        self,
        command: str,
        *,
        status: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Block until a matching control response is observed."""

        target_command = command.strip().upper()
        target_status = status.strip().lower() if status is not None else None
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._control_condition:
            while True:
                for response in self._control_responses:
                    response_command = str(response.get("command", "")).strip().upper()
                    if response_command != target_command:
                        continue
                    response_status = str(response.get("status", "")).strip().lower()
                    if target_status is not None and response_status != target_status:
                        continue
                    return dict(response)

                if deadline is None:
                    self._control_condition.wait()
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._control_condition.wait(timeout=remaining)

    @property
    def status(self) -> str | None:
        with self._status_lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._status_lock:
            return self._error

    @property
    def stdout_history(self) -> list[str]:
        return list(self._stdout_history)

    @property
    def stderr_history(self) -> list[str]:
        return list(self._stderr_history)

    # ------------------------------------------------------------------
    # Queue handlers (run on watcher thread)
    # ------------------------------------------------------------------
    def _handle_outbox_message(
        self, message: str, timestamp: int, _context: object
    ) -> None:
        payload = self._maybe_parse_json(message)
        if isinstance(payload, dict) and payload.get("type") == "stream":
            stream = payload.get("stream")
            chunk = payload.get("data", "")
            is_final = bool(payload.get("final"))
            if stream == "stdout":
                self._stdout_history.append(str(chunk))
                self._stdout_cb(str(chunk), is_final)
            elif stream == "stderr":
                self._stderr_history.append(str(chunk))
                self._stderr_cb(str(chunk), is_final)
            else:
                # Unknown stream type; surface via stderr callback for visibility
                self._stderr_cb(json.dumps(payload), is_final)
            return

        # Non-stream payloads fallback to stdout
        text_payload = str(payload)
        self._stdout_history.append(text_payload)
        self._stdout_cb(text_payload, True)

    def _handle_ctrl_message(
        self, message: str, _timestamp: int, _context: object
    ) -> None:
        payload = self._maybe_parse_json(message)
        if (
            isinstance(payload, dict)
            and payload.get("type") == "terminal"
            and "status" in payload
        ):
            self._state_cb(dict(payload))
            error = payload.get("error")
            self._mark_completion(
                status=str(payload["status"]),
                error=str(error) if isinstance(error, str) else None,
            )
            return

        if isinstance(payload, dict) and "command" in payload and "status" in payload:
            with self._control_condition:
                self._control_responses.append(dict(payload))
                self._control_condition.notify_all()
            return
        if isinstance(payload, dict) and payload.get("type") == "stream":
            stream = payload.get("stream")
            chunk = payload.get("data", "")
            is_final = bool(payload.get("final"))
            if stream == "stderr":
                self._stderr_history.append(str(chunk))
                self._stderr_cb(str(chunk), is_final)
            else:
                text_payload = json.dumps(payload)
                self._stdout_history.append(text_payload)
                self._stdout_cb(json.dumps(payload), is_final)
            return

        # Non-stream control payloads fallback to stderr for visibility.
        text_payload = str(payload)
        self._stderr_history.append(text_payload)
        self._stderr_cb(text_payload, False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mark_completion(self, *, status: str, error: str | None = None) -> None:
        with self._status_lock:
            if self._status is not None:
                return
            self._status = status
            if error is not None or status in {"failed", "cancelled", "killed"}:
                self._error = error
        self._completion.set()

    @staticmethod
    def _maybe_parse_json(message: str) -> Any:
        try:
            return json.loads(message)
        except json.JSONDecodeError:
            return message


__all__ = ["InteractiveStreamClient"]
