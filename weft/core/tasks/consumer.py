from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from weft._constants import (
    CONTROL_STOP,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    WORK_ENVELOPE_START,
)
from weft.core.targets import decode_work_message, serialize_result
from weft.core.taskspec import ReservedPolicy, TaskSpec

from .base import BaseTask
from .interactive import InteractiveTaskMixin
from .multiqueue_watcher import QueueMessageContext, QueueMode
from .runner import RunnerOutcome, TaskRunner

logger = logging.getLogger(__name__)


class Consumer(BaseTask, InteractiveTaskMixin):
    """Concrete task that consumes inbox messages and executes targets (Spec: [CC-2.3], [MF-2], [RM-5.1])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(db, taskspec, stop_event=stop_event, config=config)
        self._init_interactive()
        self._active_raw_message: str | None = None

    def process_once(self) -> None:
        super().process_once()
        if self._interactive_mode:
            self._interactive_flush_outputs()

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Configure inbox handling with reserve semantics and control queue peeking.

        Spec: [CC-2.3], [MF-2], [MF-3]
        """
        reserved_name = self._queue_names["reserved"]
        return {
            self._queue_names["inbox"]: self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=reserved_name,
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
            self._queue_names["reserved"]: self._peek_queue_config(
                self._handle_reserved_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Execute the declared target and emit a serialized result.

        Spec: [CC-2.3], [CC-2.5], [MF-2], [MF-5], [RM-5.1]
        """
        if self._paused:
            if context.mode is QueueMode.RESERVE and context.reserved_queue_name:
                try:
                    reserved_queue = self._queue(context.reserved_queue_name)
                    reserved_queue.move_one(
                        context.queue_name,
                        exact_timestamp=timestamp,
                        require_unclaimed=True,
                        with_timestamps=False,
                    )
                except Exception:
                    logger.debug(
                        "Failed to requeue message %s while paused",
                        timestamp,
                        exc_info=True,
                    )
            return

        if self._interactive_maybe_handle_message(message, timestamp, context):
            return

        self._active_raw_message = message
        try:
            self.taskspec.mark_running(pid=os.getpid())
            self._update_process_title("running")
            self._report_state_change(event="work_started", message_id=timestamp)

            work_item = decode_work_message(message)

            self._execute_work_item(work_item, timestamp)
        finally:
            self._active_raw_message = None

    def _execute_work_item(self, work_item: Any, timestamp: int | None) -> Any:
        outcome = self._run_task(work_item)
        if getattr(outcome, "worker_pid", None):
            self.register_managed_pid(outcome.worker_pid)
        metrics_payload = self._extract_metrics(outcome)
        self._ensure_outcome_ok(outcome, timestamp, metrics_payload)
        result_bytes = self._emit_result(outcome.value)
        self._finalize_message(timestamp, result_bytes, metrics_payload)
        return outcome.value

    def run_work_item(self, work_item: Any) -> Any:
        """Execute *work_item* without relying on queue plumbing."""
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="work_started", message_id=None)
        return self._execute_work_item(work_item, timestamp=None)

    def _run_task(self, work_item: Any) -> RunnerOutcome:
        runner = TaskRunner(
            target_type=self.taskspec.spec.type,
            function_target=self.taskspec.spec.function_target,
            process_target=self.taskspec.spec.process_target,
            args=getattr(self.taskspec.spec, "args", None),
            kwargs=getattr(self.taskspec.spec, "keyword_args", None),
            env=self.taskspec.spec.env or {},
            working_dir=self.taskspec.spec.working_dir,
            timeout=self.taskspec.spec.timeout,
            limits=self.taskspec.spec.limits,
            monitor_class=getattr(
                self.taskspec.spec,
                "monitor_class",
                "weft.core.resource_monitor.ResourceMonitor",
            ),
            monitor_interval=self.taskspec.spec.polling_interval,
        )
        return runner.run(work_item)

    def _finalize_message(
        self,
        timestamp: int | None,
        result_bytes: int,
        metrics_payload: dict[str, Any] | None,
    ) -> None:
        if timestamp is not None:
            try:
                self._get_reserved_queue().delete(message_id=timestamp)
            except Exception:
                logger.debug(
                    "Failed to acknowledge reserved message %s",
                    timestamp,
                    exc_info=True,
                )
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()

        self._monitor_resource_usage()
        self.taskspec.mark_completed(return_code=0)
        self._report_state_change(
            event="work_completed",
            message_id=timestamp,
            result_bytes=result_bytes,
            metrics=metrics_payload,
        )
        self._update_process_title("completed")
        self._cleanup_spilled_outputs_if_needed()
        self._end_streaming_session()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()

    def _emit_result(self, result: Any) -> int:
        serialized = serialize_result(result)
        try:
            encoded_result = serialized.encode("utf-8")
        except UnicodeEncodeError:
            encoded_result = serialized.encode("utf-8", errors="replace")

        limit_mb = getattr(
            self.taskspec.spec, "output_size_limit_mb", DEFAULT_OUTPUT_SIZE_LIMIT_MB
        )
        try:
            limit_bytes = max(int(float(limit_mb) * 1024 * 1024), 1)
        except (TypeError, ValueError):
            limit_bytes = DEFAULT_OUTPUT_SIZE_LIMIT_MB * 1024 * 1024

        outbox_queue = self._queue(self._queue_names["outbox"])
        if getattr(self.taskspec.spec, "stream_output", False):
            return self._write_streaming_result(
                outbox_queue, encoded_result, limit_bytes
            )
        if len(encoded_result) > limit_bytes:
            reference = self._spill_large_output(encoded_result)
            outbox_queue.write(json.dumps(reference))
            return len(encoded_result)

        outbox_queue.write(serialized)
        return len(encoded_result)

    def _extract_metrics(self, outcome: RunnerOutcome) -> dict[str, Any] | None:
        metrics_payload: dict[str, Any] | None = None
        metrics = outcome.metrics
        if metrics is not None:
            metrics_payload = asdict(metrics)
            cpu_percent = int(round(metrics.cpu_percent))
            self.taskspec.update_metrics(
                memory=metrics.memory_mb,
                cpu=cpu_percent,
                fds=metrics.open_files,
                net_connections=metrics.connections,
            )
        return metrics_payload

    def _ensure_outcome_ok(
        self,
        outcome: RunnerOutcome,
        timestamp: int | None,
        metrics_payload: dict[str, Any] | None,
    ) -> None:
        if outcome.status == "timeout":
            timeout_exc = TimeoutError(outcome.error or "Target timeout")
            self.taskspec.mark_timeout(error=str(timeout_exc))
            self._update_process_title("timeout")
            self._report_state_change(
                event="work_timeout",
                message_id=timestamp,
                error=str(timeout_exc),
                metrics=metrics_payload,
            )
            if timestamp is not None:
                self._apply_reserved_policy(
                    self._resolve_policy(self.taskspec.spec.reserved_policy_on_error),
                    message_timestamp=timestamp,
                )
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise timeout_exc

        if outcome.status == "limit":
            limit_exc = RuntimeError(outcome.error or "Resource limits exceeded")
            self.taskspec.mark_failed(error=str(limit_exc))
            self._update_process_title("failed", "limit")
            self._report_state_change(
                event="work_limit_violation",
                message_id=timestamp,
                error=str(limit_exc),
                metrics=metrics_payload,
            )
            if timestamp is not None:
                self._apply_reserved_policy(
                    self._resolve_policy(self.taskspec.spec.reserved_policy_on_error),
                    message_timestamp=timestamp,
                )
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise limit_exc

        if outcome.status == "error":
            error_exc = RuntimeError(outcome.error or "Target execution failed")
            self.taskspec.mark_failed(error=str(error_exc))
            self._update_process_title("failed")
            self._report_state_change(
                event="work_failed",
                message_id=timestamp,
                error=str(error_exc),
                metrics=metrics_payload,
            )
            if timestamp is not None:
                self._apply_reserved_policy(
                    self._resolve_policy(self.taskspec.spec.reserved_policy_on_error),
                    message_timestamp=timestamp,
                )
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise error_exc

    def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
        if self._interactive_mode:
            self._interactive_shutdown()
        super().stop(join=join, timeout=timeout)

    def cleanup(self) -> None:
        cleanup_enabled = getattr(
            self.taskspec.spec, "cleanup_on_exit", DEFAULT_CLEANUP_ON_EXIT
        )

        if self._interactive_mode:
            self._interactive_shutdown()

        if cleanup_enabled:
            self._purge_start_tokens()
            self._purge_stream_markers()

        super().cleanup()

    # ------------------------------------------------------------------
    # Sentinel helpers
    # ------------------------------------------------------------------
    def _resolve_policy(self, policy: ReservedPolicy) -> ReservedPolicy:
        """Override reserved policy when handling start tokens."""
        if not self._is_start_token(self._active_raw_message):
            return policy
        # CLEAR is safest when the envelope is purely structural
        return ReservedPolicy.CLEAR

    @staticmethod
    def _is_start_token(raw: str | None) -> bool:
        if raw is None:
            return False
        stripped = raw.strip()
        if stripped in {"", "{}"}:
            return True
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if payload == WORK_ENVELOPE_START or payload == {}:
            return True
        if (
            isinstance(payload, dict)
            and payload.get("close") is True
            and len(payload) == 1
        ):
            return True
        return False

    @staticmethod
    def _is_stream_final_marker(raw: str) -> bool:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        return (
            payload.get("type") == "stream"
            and payload.get("final") is True
            and payload.get("data") in ("", None)
        )

    def _purge_start_tokens(self) -> None:
        reserved_queue = self._get_reserved_queue()
        try:
            entries = reserved_queue.peek_many(limit=256, with_timestamps=True)
        except Exception:
            return
        if not entries:
            return
        typed_entries = [cast(tuple[str, int], entry) for entry in entries]
        for body, ts in typed_entries:
            if self._is_start_token(body):
                try:
                    reserved_queue.delete(message_id=ts)
                except Exception:
                    logger.debug(
                        "Failed to purge start token %s from reserved queue",
                        ts,
                        exc_info=True,
                    )

    def _purge_stream_markers(self) -> None:
        for queue_key in ("outbox", "ctrl_out"):
            queue_name = self._queue_names.get(queue_key)
            if not queue_name:
                continue
            queue = self._queue(queue_name)
            try:
                entries = queue.peek_many(limit=256, with_timestamps=True)
            except Exception:
                continue
            if not entries:
                continue
            typed_entries = [cast(tuple[str, int], entry) for entry in entries]
            for body, ts in typed_entries:
                if self._is_stream_final_marker(body):
                    try:
                        queue.delete(message_id=ts)
                    except Exception:
                        logger.debug(
                            "Failed to purge stream sentinel %s from %s",
                            ts,
                            queue_name,
                            exc_info=True,
                        )


class Observer(BaseTask):
    """Task that peeks at messages without consuming them (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._observer = observer
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Peek at inbox and control queues without consuming messages.

        Spec: [CC-2.3], [MF-3]
        """
        return {
            self._queue_names["inbox"]: self._peek_queue_config(
                self._handle_work_message
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Surface messages to the caller without acknowledging them.

        Spec: [CC-2.3], [MF-5]
        """
        self._observer(message, timestamp)

    def _cleanup_reserved_if_needed(self) -> None:
        """Observers never create reserved messages, so no cleanup is required.

        Spec: [CC-2.3]
        """
        return


class SelectiveConsumer(BaseTask):
    """Task that peeks and optionally consumes messages based on a selector (Spec: [CC-2.3], [MF-2])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        selector: Callable[[str, int], bool],
        *,
        callback: Callable[[str, int], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._selector = selector
        self._callback = callback
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Peek at queues so the selector can decide whether to consume messages.

        Spec: [CC-2.3], [MF-3]
        """
        return {
            self._queue_names["inbox"]: self._peek_queue_config(
                self._handle_work_message
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Invoke the optional callback and delete the message when the selector permits.

        Spec: [CC-2.3], [MF-2]
        """
        if self._callback:
            self._callback(message, timestamp)
        if self._selector(message, timestamp):
            self._queue(context.queue_name).delete(message_id=timestamp)

    def _cleanup_reserved_if_needed(self) -> None:
        """Selectors never operate in reserve mode, so reserved cleanup is unnecessary.

        Spec: [CC-2.3]
        """
        return


class Monitor(BaseTask):
    """Forward messages to a downstream queue while observing them (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        downstream_queue: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._observer = observer
        self._downstream_queue = downstream_queue
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Reserve messages, forwarding them to the downstream queue while observing.

        Spec: [CC-2.3], [MF-2], [MF-5]
        """
        target = self._downstream_queue or self._queue_names["outbox"]
        self._downstream_queue = target
        return {
            self._queue_names["inbox"]: self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=target,
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Forward the observed payload while preserving the move performed by reserve mode.

        Spec: [CC-2.3], [MF-2], [MF-5]
        """
        self._observer(message, timestamp)
        # message already moved to downstream queue by the watcher

    def _handle_control_command(
        self, command: str, context: QueueMessageContext
    ) -> bool:
        """Allow STOP to cancel the monitor without reserved-queue manipulation.

        Spec: [CC-2.4], [MF-3]
        """
        if command == CONTROL_STOP:
            self.should_stop = True
            self.taskspec.mark_cancelled(reason="STOP command received")
            self._report_state_change(
                event="control_stop", message_id=context.timestamp
            )
            self._update_process_title("cancelled")
            if self._stop_event:
                self._stop_event.set()
            return True
        return False

    def _cleanup_reserved_if_needed(self) -> None:
        """Monitor never allocates its own reserved queue so cleanup is unnecessary.

        Spec: [CC-2.3]
        """
        return


class SamplingObserver(Observer):
    """Observer that samples messages based on elapsed time (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        interval_seconds: float,
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__(
            db=db, taskspec=taskspec, observer=observer, stop_event=stop_event
        )
        self._sampling_interval = max(0.0, interval_seconds)
        self._last_sample_time: float | None = None

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        """Forward messages when the configured interval has elapsed since the last sample.

        Spec: [CC-2.3], [MF-5]
        """
