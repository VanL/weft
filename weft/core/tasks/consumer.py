from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    WORK_ENVELOPE_START,
)
from weft.core.agent_runtime import AgentExecutionResult
from weft.core.targets import decode_work_message, serialize_result
from weft.core.taskspec import ReservedPolicy, TaskSpec
from weft.helpers import kill_process_tree, terminate_process_tree

from .base import BaseTask
from .interactive import InteractiveTaskMixin
from .multiqueue_watcher import QueueMessageContext, QueueMode
from .runner import RunnerOutcome, TaskRunner
from .sessions import AgentSession

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
        self._active_message_timestamp: int | None = None
        self._agent_session: AgentSession | None = None
        self._deferred_active_control_command: str | None = None
        self._deferred_active_control_timestamp: int | None = None

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
        self._active_message_timestamp = timestamp
        try:
            work_item = decode_work_message(message)
            initial_transition = self._begin_work_item(timestamp)
            self._execute_work_item(
                work_item,
                timestamp,
                initial_transition=initial_transition,
            )
        finally:
            self._active_raw_message = None
            self._active_message_timestamp = None

    def _execute_work_item(
        self,
        work_item: Any,
        timestamp: int | None,
        *,
        initial_transition: bool,
    ) -> Any:
        outcome = self._run_task(work_item)
        metrics_payload = self._extract_metrics(outcome)
        self._ensure_outcome_ok(outcome, timestamp, metrics_payload)
        result_bytes = self._emit_result(outcome.value)
        self._finalize_message(
            timestamp,
            result_bytes,
            metrics_payload,
            initial_transition=initial_transition,
        )
        return outcome.value

    def run_work_item(self, work_item: Any) -> Any:
        """Execute *work_item* without relying on queue plumbing."""
        initial_transition = self._begin_work_item(None)
        return self._execute_work_item(
            work_item,
            timestamp=None,
            initial_transition=initial_transition,
        )

    def _run_task(self, work_item: Any) -> RunnerOutcome:
        if self._uses_agent_session():
            session = self._ensure_agent_session()
            start_time = time.monotonic()
            result = session.execute(
                work_item,
                cancel_requested=self._cancel_requested,
            )
            return RunnerOutcome(
                status=result.status,
                value=result.value,
                error=result.error,
                stdout=None,
                stderr=None,
                returncode=None,
                duration=time.monotonic() - start_time,
                metrics=result.metrics,
                worker_pid=session.pid,
                runtime_handle=session.handle,
            )

        runner = self._make_task_runner()
        return runner.run_with_hooks(
            work_item,
            cancel_requested=self._cancel_requested,
            on_worker_started=self._register_running_worker,
            on_runtime_handle_started=self.register_runtime_handle,
        )

    def _poll_active_control_once(self) -> None:
        """Poll one active control message on the main task thread.

        STOP and KILL remain deferred until the runtime unwinds. Other control
        commands still flow through the normal BaseTask control handler.
        """

        if self._deferred_active_control_command is not None:
            return

        queue_name = self._queue_names["ctrl_in"]
        ctrl_queue = self._queue(queue_name)
        try:
            entries = ctrl_queue.peek_many(limit=1, with_timestamps=True) or []
        except Exception:  # pragma: no cover - defensive broker guard
            logger.debug("Failed to poll active control queue", exc_info=True)
            return

        if not entries:
            return

        entry = entries[0]
        if not isinstance(entry, tuple) or len(entry) != 2:
            return
        raw_message, timestamp = entry
        if not isinstance(raw_message, str):
            return

        timestamp_int = int(timestamp)
        command = raw_message.strip().upper()
        if command in {CONTROL_STOP, CONTROL_KILL}:
            self._defer_active_control(command, timestamp_int)
            self._ack_control_message(queue_name, timestamp_int)
            return

        context = QueueMessageContext(
            queue_name=queue_name,
            queue=ctrl_queue,
            mode=QueueMode.PEEK,
            timestamp=timestamp_int,
        )
        self._handle_control_message(raw_message, timestamp_int, context)

    def _defer_active_control(self, command: str, timestamp: int) -> None:
        """Defer active STOP/KILL finalization back to the main task thread.

        The background control poller may request cancellation promptly, but it
        must not publish terminal state or apply reserved-queue policy while the
        main task thread is still running the active work item.
        """

        self._deferred_active_control_command = command
        self._deferred_active_control_timestamp = timestamp
        self.should_stop = True
        if command == CONTROL_KILL:
            self._kill_requested = True

    def _finalize_deferred_active_control(self) -> None:
        """Apply deferred STOP/KILL state transitions on the main task thread."""

        command = self._deferred_active_control_command
        timestamp = self._deferred_active_control_timestamp
        if command is None:
            return

        self._deferred_active_control_command = None
        self._deferred_active_control_timestamp = None

        if command == CONTROL_STOP:
            policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_stop)
            self._handle_stop_request(
                reason="STOP command received",
                event="control_stop",
                message_id=timestamp,
                apply_reserved_policy=False,
            )
            self._apply_reserved_policy(
                policy, message_timestamp=self._active_message_timestamp
            )
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            self._send_control_response("STOP", "ack")
            return

        if command == CONTROL_KILL:
            policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_error)
            self._handle_kill_request(
                reason="KILL command received",
                event="control_kill",
                message_id=timestamp,
                apply_reserved_policy=False,
            )
            self._apply_reserved_policy(
                policy, message_timestamp=self._active_message_timestamp
            )
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            self._send_control_response("KILL", "ack")

    def _make_task_runner(self, *, interactive: bool = False) -> TaskRunner:
        return TaskRunner(
            target_type=self.taskspec.spec.type,
            tid=self.taskspec.tid,
            function_target=self.taskspec.spec.function_target,
            process_target=self.taskspec.spec.process_target,
            agent=(
                self.taskspec.spec.agent.model_dump(mode="python")
                if self.taskspec.spec.agent is not None
                else None
            ),
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
            runner_name=self.taskspec.spec.runner.name,
            runner_options=self.taskspec.spec.runner.options,
            persistent=self._task_is_persistent(),
            interactive=interactive,
            db_path=self._db_path,
            config=self._config,
        )

    def _begin_work_item(self, timestamp: int | None) -> bool:
        if self._task_is_persistent() and self.taskspec.state.status == "running":
            self._report_state_change(event="work_item_started", message_id=timestamp)
            self._update_process_title("running")
            return False

        self.taskspec.mark_started(pid=os.getpid())
        self._update_process_title("spawning")
        self._report_state_change(event="work_spawning", message_id=timestamp)
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="work_started", message_id=timestamp)
        return True

    def _cancel_requested(self) -> bool:
        if not self.should_stop:
            self._poll_active_control_once()
        if self.should_stop:
            return True
        if self._stop_event is None:
            return False
        return self._stop_event.is_set()

    def _register_running_worker(self, pid: int | None) -> None:
        self.register_managed_pid(pid)

    def _finalize_message(
        self,
        timestamp: int | None,
        result_bytes: int,
        metrics_payload: dict[str, Any] | None,
        *,
        initial_transition: bool,
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
        if self._task_is_persistent():
            self._report_state_change(
                event="work_item_completed",
                message_id=timestamp,
                result_bytes=result_bytes,
                metrics=metrics_payload,
                initial_transition=initial_transition,
            )
            self._update_process_title("running")
            return

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
        if isinstance(result, AgentExecutionResult):
            total_bytes = 0
            for output in result.outputs:
                total_bytes += self._emit_single_output(output)
            return total_bytes
        return self._emit_single_output(result)

    def _emit_single_output(self, result: Any) -> int:
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
        """Raise on non-ok outcomes and transition task state accordingly.

        Spec: [RM-1], [RM-2] (limit violations), docs/specifications/06-Resource_Management.md#error-categories
        """
        if outcome.status != "ok" and self._uses_agent_session():
            self._shutdown_agent_session()

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
            self._apply_reserved_policy_on_error(timestamp)
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise timeout_exc

        if outcome.status == "limit":
            limit_exc = RuntimeError(outcome.error or "Resource limits exceeded")
            # Spec: docs/specifications/06-Resource_Management.md#error-categories
            self.taskspec.mark_killed(reason=str(limit_exc))
            self._update_process_title("killed", "limit")
            self._report_state_change(
                event="work_limit_violation",
                message_id=timestamp,
                error=str(limit_exc),
                metrics=metrics_payload,
            )
            self._apply_reserved_policy_on_error(timestamp)
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise limit_exc

        if outcome.status == "error":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "cancelled":
                self._end_streaming_session()
                self.should_stop = True
                if self._stop_event:
                    self._stop_event.set()
                raise RuntimeError(
                    self.taskspec.state.error or "Target execution cancelled"
                )
            if self.taskspec.state.status == "killed":
                self._end_streaming_session()
                self.should_stop = True
                if self._stop_event:
                    self._stop_event.set()
                raise RuntimeError(
                    self.taskspec.state.error or "Target execution killed"
                )
            error_exc = RuntimeError(outcome.error or "Target execution failed")
            self.taskspec.mark_failed(error=str(error_exc))
            self._update_process_title("failed")
            self._report_state_change(
                event="work_failed",
                message_id=timestamp,
                error=str(error_exc),
                metrics=metrics_payload,
            )
            self._apply_reserved_policy_on_error(timestamp)
            self._end_streaming_session()
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            raise error_exc

        if outcome.status == "cancelled":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "killed":
                self._end_streaming_session()
                self.should_stop = True
                if self._stop_event:
                    self._stop_event.set()
                raise RuntimeError(
                    self.taskspec.state.error or "Target execution killed"
                )
            if self.taskspec.state.status == "cancelled":
                self._end_streaming_session()
                self.should_stop = True
                if self._stop_event:
                    self._stop_event.set()
                raise RuntimeError(
                    self.taskspec.state.error or "Target execution cancelled"
                )
            reason = outcome.error or "Target execution cancelled"
            self._handle_external_stop(reason)
            raise RuntimeError(reason)

    def handle_termination_signal(self, signum: int) -> None:
        if self._external_stop_handled:
            return
        self._external_stop_handled = True
        sigusr1 = getattr(signal, "SIGUSR1", None)
        graceful = not (sigusr1 is not None and signum == sigusr1)
        self._terminate_active_worker(graceful=graceful)
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = f"signal {signum}"
        if graceful:
            self._handle_external_stop(signal_name)
        else:
            self._handle_external_kill(signal_name)

    def _handle_external_stop(self, reason: str) -> None:
        if self.taskspec.state.status == "cancelled":
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            self._end_streaming_session()
            return
        policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_stop)
        self._handle_stop_request(
            reason=reason,
            event="task_signal_stop",
            message_id=self._active_message_timestamp,
            apply_reserved_policy=False,
        )
        self._apply_reserved_policy(
            policy, message_timestamp=self._active_message_timestamp
        )
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()
        self._end_streaming_session()

    def _handle_external_kill(self, reason: str) -> None:
        if self.taskspec.state.status == "killed":
            self.should_stop = True
            if self._stop_event:
                self._stop_event.set()
            self._end_streaming_session()
            return
        policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_error)
        self._handle_kill_request(
            reason=reason,
            event="task_signal_kill",
            message_id=self._active_message_timestamp,
            apply_reserved_policy=False,
        )
        self._apply_reserved_policy(
            policy, message_timestamp=self._active_message_timestamp
        )
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()
        self._end_streaming_session()

    def _terminate_active_worker(self, *, graceful: bool) -> None:
        self._stop_registered_runtime_handle(timeout=0.2, graceful=graceful)
        for pid in sorted(self._managed_pids):
            if graceful:
                terminate_process_tree(pid, timeout=0.2)
            else:
                kill_process_tree(pid, timeout=0.2)
        self._shutdown_agent_session()

    def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
        if self._interactive_mode:
            self._interactive_shutdown()
        self._shutdown_agent_session()
        super().stop(join=join, timeout=timeout)

    def cleanup(self) -> None:
        cleanup_enabled = getattr(
            self.taskspec.spec, "cleanup_on_exit", DEFAULT_CLEANUP_ON_EXIT
        )

        if self._interactive_mode:
            self._interactive_shutdown()

        self._shutdown_agent_session()

        if cleanup_enabled:
            self._purge_start_tokens()
            self._purge_stream_markers()
            self._cleanup_spilled_outputs_if_needed()

        super().cleanup()

    def _task_is_persistent(self) -> bool:
        return bool(getattr(self.taskspec.spec, "persistent", False))

    def _uses_agent_session(self) -> bool:
        agent = self.taskspec.spec.agent
        return bool(
            self._task_is_persistent()
            and self.taskspec.spec.type == "agent"
            and agent is not None
            and agent.conversation_scope == "per_task"
        )

    def _ensure_agent_session(self) -> AgentSession:
        if self._agent_session is not None:
            return self._agent_session

        runner = self._make_task_runner()
        session = runner.start_agent_session()
        self._agent_session = session
        self.register_runtime_handle(session.handle)
        self.register_managed_pid(session.pid)
        return session

    def _shutdown_agent_session(self) -> None:
        if self._agent_session is None:
            return
        try:
            self._agent_session.close()
        finally:
            self._agent_session = None

    # ------------------------------------------------------------------
    # Sentinel helpers
    # ------------------------------------------------------------------
    def _resolve_policy(self, policy: ReservedPolicy) -> ReservedPolicy:
        """Override reserved policy when handling start tokens."""
        if not self._is_start_token(self._active_raw_message):
            return policy
        # CLEAR is safest when the envelope is purely structural
        return ReservedPolicy.CLEAR

    def _apply_reserved_policy_on_error(self, timestamp: int | None) -> None:
        if timestamp is None:
            return
        policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_error)
        self._apply_reserved_policy(policy, message_timestamp=timestamp)
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()

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
        if command == CONTROL_KILL:
            self.should_stop = True
            self.taskspec.mark_killed(reason="KILL command received")
            self._report_state_change(
                event="control_kill", message_id=context.timestamp
            )
            self._update_process_title("killed")
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
