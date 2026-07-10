"""Concrete task consumer runtime.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3], [CC-2.5]
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.2]
- docs/specifications/13-Agent_Runtime.md [AR-1], [AR-4.1], [AR-6]
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

from simplebroker.ext import BrokerError
from weft._constants import (
    CONSUMER_ACTIVE_WORKER_LANE,
    CONSUMER_WORKER_EVENT_LANE,
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_OUTPUT_SIZE_LIMIT_MB,
    TERMINAL_TASK_STATUSES,
    VALID_RUNNER_OUTCOME_STATUSES,
    WORK_ENVELOPE_START,
)
from weft.core.agents.runtime import AgentExecutionResult
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.targets import decode_work_message, serialize_result
from weft.core.taskspec import ReservedPolicy, TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import kill_process_tree, terminate_process_tree

from .base import BaseTask, TaskControlPolicy, TaskWorkerResult
from .interactive import InteractiveTaskMixin
from .multiqueue_watcher import QueueMessageContext, QueueMode, QueueRuntimeConfig
from .runner import RunnerOutcome, TaskRunner
from .sessions import AgentSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ConsumerWorkerEvent:
    kind: Literal["worker_started", "runtime_handle", "stream_chunk"]
    pid: int | None = None
    runtime_handle: RunnerHandle | None = None
    stream: Literal["stdout", "stderr"] | None = None
    chunk: str = ""
    final: bool = False
    index: int = 0


@dataclass(frozen=True, slots=True)
class _ConsumerWorkResult:
    timestamp: int | None
    initial_transition: bool
    live_command_streaming: bool
    outcome: RunnerOutcome | None = None
    exception: BaseException | None = None


class Consumer(BaseTask, InteractiveTaskMixin):
    """Concrete task that consumes inbox messages and executes targets (Spec: [CC-2.3], [MF-2], [RM-5.1])."""

    control_policy = TaskControlPolicy(
        stop="deferred-while-active",
        kill="deferred-while-active",
        reserved_policy="main-thread-after-runner-unwinds",
        ack="post-unwind-for-active-control",
        terminal_state="post-unwind-for-active-control",
    )

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
        self._active_work_in_flight = False
        self._in_reactor_turn = False
        self._direct_work_waiting = False
        self._direct_work_value: Any = None
        self._direct_work_exception: BaseException | None = None
        self._agent_session: AgentSession | None = None
        self._deferred_active_control_command: str | None = None
        self._deferred_active_control_timestamp: int | None = None
        self._deferred_active_control_request_id: str | None = None
        self._activate_pipeline_waiter_if_needed()
        self._set_activity("waiting", waiting_on=self._queue_names["inbox"])
        self._emit_pipeline_started_event()

    def _activate_pipeline_waiter_if_needed(self) -> None:
        owner = self._pipeline_owner_config()
        if owner is None or owner.get("role") != "pipeline_stage":
            return
        if self.taskspec.state.status != "created":
            return
        self.taskspec.mark_started(pid=os.getpid())
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._report_state_change(event="task_started")

    def _process_reactor_turn(self) -> None:
        """Run one Consumer turn behind the owner-enforcing template.

        Spec:
        - docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.3]
        - docs/specifications/05-Message_Flow_and_State.md [MF-2]
        """
        self._in_reactor_turn = True
        try:
            if self._active_work_in_flight:
                self._drain_worker_results()
                if self._active_work_in_flight:
                    self._poll_active_control_once()
                self._drain_worker_results()
                self._maybe_emit_poll_report()
            else:
                super()._process_reactor_turn()
        finally:
            self._in_reactor_turn = False
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

    def _queue_counts_as_wait_activity(self, config: QueueRuntimeConfig) -> bool:
        """Ignore already-reserved work as fresh wait activity while active."""

        if getattr(self, "_active_work_in_flight", False):
            return config.name == self._queue_names["ctrl_in"]
        return super()._queue_counts_as_wait_activity(config)

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
                except (
                    BrokerError,
                    OSError,
                    RuntimeError,
                ):  # pragma: no cover - broker requeue best effort
                    logger.debug(
                        "Failed to requeue message %s while paused",
                        timestamp,
                        exc_info=True,
                    )
            return

        if self._interactive_maybe_handle_message(message, timestamp, context):
            return

        if self._active_work_in_flight:
            self._requeue_reserved_message(context, timestamp)
            return

        self._start_reactor_work_message(message, timestamp)

    def _requeue_reserved_message(
        self,
        context: QueueMessageContext,
        timestamp: int,
    ) -> None:
        if context.mode is not QueueMode.RESERVE or not context.reserved_queue_name:
            return
        try:
            reserved_queue = self._queue(context.reserved_queue_name)
            reserved_queue.move_one(
                context.queue_name,
                exact_timestamp=timestamp,
                require_unclaimed=True,
                with_timestamps=False,
            )
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker requeue best effort
            logger.debug("Failed to requeue message %s", timestamp, exc_info=True)

    def _start_reactor_work_message(self, message: str, timestamp: int) -> None:
        self._active_raw_message = message
        self._active_message_timestamp = timestamp
        try:
            work_item = decode_work_message(message)
            if self._uses_agent_session() and self._agent_session is None:
                self._ensure_agent_session()
            initial_transition = self._begin_work_item(timestamp)
            self._active_work_in_flight = True
            self._submit_worker_call(
                CONSUMER_ACTIVE_WORKER_LANE,
                lambda: self._run_reactor_work_item(
                    work_item,
                    timestamp=timestamp,
                    initial_transition=initial_transition,
                ),
            )
        except Exception:  # pragma: no cover - restore reactor state before re-raise
            self._active_work_in_flight = False
            self._active_raw_message = None
            self._active_message_timestamp = None
            raise

    def _finalize_work_exception(
        self,
        exc: BaseException,
        timestamp: int | None,
    ) -> Any:
        diagnostics = runner_diagnostics(
            phase="execute",
            runner=self.taskspec.spec.runner.name,
            target_type=self.taskspec.spec.type,
            pid=os.getpid(),
            alive=True,
            message=str(exc),
            exception_type=type(exc).__name__,
        )
        self.taskspec.mark_failed(error=str(exc))
        return self._finalize_terminal_outcome(
            title_state="failed",
            title_detail=None,
            event="work_failed",
            pipeline_status="failed",
            timestamp=timestamp,
            metrics_payload=None,
            runner_diagnostics=diagnostics,
            exc=exc,
        )

    def _commit_work_outcome(
        self,
        outcome: RunnerOutcome,
        timestamp: int | None,
        *,
        initial_transition: bool,
        live_command_streaming: bool,
    ) -> Any:
        self._register_outcome_runtime(outcome)
        metrics_payload = self._extract_metrics(outcome)
        agent_execution = self._build_agent_execution_payload(outcome.value)
        self._ensure_outcome_ok(outcome, timestamp, metrics_payload)
        if live_command_streaming:
            result_bytes = self._serialized_result_bytes(outcome.value)
        else:
            result_bytes = self._emit_result(outcome.value)
        self._finalize_message(
            timestamp,
            result_bytes,
            metrics_payload,
            agent_execution=agent_execution,
            initial_transition=initial_transition,
        )
        return outcome.value

    def _register_outcome_runtime(self, outcome: RunnerOutcome) -> None:
        self._register_running_worker(outcome.worker_pid)
        self.register_runtime_handle(outcome.runtime_handle)

    def run_work_item(self, work_item: Any) -> Any:
        """Execute *work_item* without relying on queue plumbing."""
        initial_transition = self._begin_work_item(None)
        self._direct_work_waiting = True
        self._direct_work_value = None
        self._direct_work_exception = None
        self._active_raw_message = None
        self._active_message_timestamp = None
        self._active_work_in_flight = True
        self._submit_worker_call(
            CONSUMER_ACTIVE_WORKER_LANE,
            lambda: self._run_reactor_work_item(
                work_item,
                timestamp=None,
                initial_transition=initial_transition,
            ),
        )
        try:
            while self._active_work_in_flight or self._has_worker_activity():
                self.process_once()
                if self._direct_work_exception is not None:
                    raise self._direct_work_exception
                if self._active_work_in_flight or self._has_worker_activity():
                    self.wait_for_activity(timeout=0.05)
            if self._direct_work_exception is not None:
                raise self._direct_work_exception
            return self._direct_work_value
        finally:
            self._direct_work_waiting = False

    def _run_reactor_work_item(
        self,
        work_item: Any,
        *,
        timestamp: int | None,
        initial_transition: bool,
    ) -> _ConsumerWorkResult:
        try:
            outcome, live_command_streaming = self._run_task_for_reactor(work_item)
        except Exception as exc:  # pragma: no cover - worker exception transport
            return _ConsumerWorkResult(
                timestamp=timestamp,
                initial_transition=initial_transition,
                live_command_streaming=False,
                exception=exc,
            )
        return _ConsumerWorkResult(
            timestamp=timestamp,
            initial_transition=initial_transition,
            live_command_streaming=live_command_streaming,
            outcome=outcome,
        )

    def _run_task_for_reactor(self, work_item: Any) -> tuple[RunnerOutcome, bool]:
        if self._uses_agent_session():
            session = self._agent_session
            if session is None:
                raise RuntimeError("Agent session was not started on the task reactor")
            start_time = time.monotonic()
            result = session.execute(
                work_item,
                cancel_requested=self._worker_cancel_requested,
            )
            return (
                RunnerOutcome(
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
                ),
                False,
            )

        runner = self._make_task_runner(broker_access=False)
        live_command_streaming = (
            self._uses_live_command_streaming() and runner.supports_stream_callbacks()
        )
        stdout_index = 0
        stderr_index = 0

        def _publish_worker_started(pid: int | None) -> None:
            self._publish_consumer_worker_event(
                _ConsumerWorkerEvent(kind="worker_started", pid=pid)
            )

        def _publish_runtime_handle(handle: RunnerHandle) -> None:
            self._publish_consumer_worker_event(
                _ConsumerWorkerEvent(kind="runtime_handle", runtime_handle=handle)
            )

        def _on_stdout_chunk(chunk: str, final: bool) -> None:
            nonlocal stdout_index
            self._publish_consumer_worker_event(
                _ConsumerWorkerEvent(
                    kind="stream_chunk",
                    stream="stdout",
                    chunk=chunk,
                    final=final,
                    index=stdout_index,
                )
            )
            stdout_index += 1

        def _on_stderr_chunk(chunk: str, final: bool) -> None:
            nonlocal stderr_index
            self._publish_consumer_worker_event(
                _ConsumerWorkerEvent(
                    kind="stream_chunk",
                    stream="stderr",
                    chunk=chunk,
                    final=final,
                    index=stderr_index,
                )
            )
            stderr_index += 1

        if live_command_streaming:
            return (
                runner.run_with_hooks(
                    work_item,
                    cancel_requested=self._worker_cancel_requested,
                    on_worker_started=_publish_worker_started,
                    on_runtime_handle_started=_publish_runtime_handle,
                    on_stdout_chunk=_on_stdout_chunk,
                    on_stderr_chunk=_on_stderr_chunk,
                ),
                live_command_streaming,
            )

        return (
            runner.run_with_hooks(
                work_item,
                cancel_requested=self._worker_cancel_requested,
                on_worker_started=_publish_worker_started,
                on_runtime_handle_started=_publish_runtime_handle,
            ),
            live_command_streaming,
        )

    def _worker_cancel_requested(self) -> bool:
        if self.should_stop:
            return True
        if self._stop_event is None:
            return False
        return self._stop_event.is_set()

    def _publish_consumer_worker_event(self, event: _ConsumerWorkerEvent) -> None:
        self._publish_worker_result(CONSUMER_WORKER_EVENT_LANE, value=event)

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if result.lane == CONSUMER_WORKER_EVENT_LANE:
            if result.error is not None:
                raise RuntimeError("Consumer worker event failed") from result.error
            self._handle_consumer_worker_event(cast(_ConsumerWorkerEvent, result.value))
            return

        if result.lane == CONSUMER_ACTIVE_WORKER_LANE:
            self._handle_active_work_result(result)
            return

        super()._handle_worker_result(result)

    def _handle_active_work_result(self, result: TaskWorkerResult) -> None:
        committed_value: Any = None
        try:
            if result.error is not None:
                self._finalize_work_exception(
                    result.error,
                    self._active_message_timestamp,
                )
                return

            work_result = cast(_ConsumerWorkResult, result.value)
            if work_result.exception is not None:
                self._finalize_work_exception(
                    work_result.exception,
                    work_result.timestamp,
                )
                return
            if work_result.outcome is None:
                raise RuntimeError("Consumer worker produced no outcome")

            if work_result.live_command_streaming:
                self._drain_worker_results()
            committed_value = self._commit_work_outcome(
                work_result.outcome,
                work_result.timestamp,
                initial_transition=work_result.initial_transition,
                live_command_streaming=work_result.live_command_streaming,
            )
            # A STOP/KILL deferred while this work item was active (Spec:
            # [MF-3]) must still be honored when the outcome is "ok" -- the
            # non-ok branches of `_ensure_outcome_ok` finalize the deferred
            # command themselves, but the ok path (`_commit_work_outcome` ->
            # `_finalize_message`) never touches deferred control state.
            # Without this call a persistent consumer's status could be left
            # `running` forever with no terminal event ([STATE.1], [OBS.1]).
            #
            # This must run here, inside the try block, *before* the
            # `finally` below clears `_active_message_timestamp`:
            # `_finalize_deferred_active_control` reads that attribute to
            # decide whether a reserved-queue row still needs the configured
            # disposition policy applied. On the ok path `_finalize_message`
            # has already consumed the reserved row (the work completed
            # successfully), so the finalizer's reserved-policy step is a
            # clean no-op here (`_apply_reserved_policy`/`_ensure_reserved_empty`
            # tolerate an already-missing row) -- applying `requeue` to
            # completed work would cause duplicate execution, which
            # [QUEUE.6] does not intend. Only the task-level part (terminal
            # transition, envelope, control ack) has any effect.
            #
            # `_finalize_deferred_active_control` also no-ops cleanly when
            # there is no pending deferred command, and when the task is
            # already terminal (one-shot tasks reach `completed` inside
            # `_finalize_message` above), so this call never double-emits a
            # terminal event.
            #
            # Accepted trade: if the ok-commit above raises (broker write
            # failure), this call is skipped and the deferral stays
            # unfinalized — result-then-terminal ordering is mandated, so
            # the deferral cannot run before the commit.
            self._finalize_deferred_active_control()
        except Exception as exc:  # pragma: no cover - worker result finalization
            if self._direct_work_waiting:
                self._direct_work_exception = exc
            elif (
                self.taskspec.state.status in TERMINAL_TASK_STATUSES or self.should_stop
            ):
                logger.debug(
                    "Consumer worker result finalized terminal state",
                    exc_info=True,
                )
            else:
                raise
        else:
            if self._direct_work_waiting:
                self._direct_work_value = committed_value
        finally:
            self._active_work_in_flight = False
            self._active_raw_message = None
            self._active_message_timestamp = None

    def _handle_consumer_worker_event(self, event: _ConsumerWorkerEvent) -> None:
        if event.kind == "worker_started":
            self._register_running_worker(event.pid)
            return
        if event.kind == "runtime_handle":
            self.register_runtime_handle(event.runtime_handle)
            return
        if event.kind == "stream_chunk":
            self._emit_live_stream_chunk(event)
            return
        raise RuntimeError(f"Unknown consumer worker event {event.kind!r}")

    def _emit_live_stream_chunk(self, event: _ConsumerWorkerEvent) -> None:
        stream = event.stream
        if stream not in {"stdout", "stderr"}:
            raise RuntimeError("Stream chunk event missing stream name")

        self._begin_streaming_session(
            mode="stream",
            metadata={
                "queue": self._queue_names["outbox"],
                "ctrl_queue": self._queue_names["ctrl_out"],
            },
        )
        envelope: dict[str, Any] = {
            "type": "stream",
            "stream": stream,
            "chunk": event.index,
            "final": event.final,
            "encoding": "text",
            "data": event.chunk,
        }
        if stream == "stdout":
            if event.final:
                envelope["result_transform"] = "strip"
        self._queue(self._queue_names["outbox"]).write(json.dumps(envelope))

    def _uses_live_command_streaming(self) -> bool:
        return bool(
            self.taskspec.spec.type == "command"
            and getattr(self.taskspec.spec, "stream_output", False)
            and not getattr(self.taskspec.spec, "interactive", False)
        )

    @staticmethod
    def _serialized_result_bytes(result: Any) -> int:
        serialized = serialize_result(result)
        try:
            encoded_result = serialized.encode("utf-8")
        except UnicodeEncodeError:
            encoded_result = serialized.encode("utf-8", errors="replace")
        return len(encoded_result)

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
        request = self._parse_control_request(raw_message)
        command = request.command
        if command in {CONTROL_STOP, CONTROL_KILL}:
            self._defer_active_control(
                command,
                timestamp_int,
                request_id=request.request_id,
            )
            self._ack_control_message(queue_name, timestamp_int)
            return

        context = QueueMessageContext(
            queue_name=queue_name,
            queue=ctrl_queue,
            mode=QueueMode.PEEK,
            timestamp=timestamp_int,
        )
        self._handle_control_message(raw_message, timestamp_int, context)

    def _defer_active_control(
        self,
        command: str,
        timestamp: int,
        *,
        request_id: str | None = None,
    ) -> None:
        """Defer active STOP/KILL finalization back to the main task thread.

        Thread-safety pattern
        ---------------------
        Control messages (STOP/KILL) are observed by the main task reactor
        while blocking work runs on the broker-free worker lane. Applying the
        full terminal transition immediately would still be unsafe because:

        1. ``taskspec`` state mutations and reserved-queue operations are not
           applied until the active runner has unwound.
        2. The reserved-queue policy (keep/requeue/clear) must be applied
           against ``_active_message_timestamp`` after the worker result comes
           back to the reactor.
        3. Sending the control acknowledgement response before the runner has
           unwound would create a false ordering: the caller would see an ack
           before the task is actually done.

        Instead, the reactor stores the command and its queue timestamp in
        ``_deferred_active_control_command`` /
        ``_deferred_active_control_timestamp``, then sets ``should_stop`` (and
        ``_kill_requested`` for KILL) so the worker lane's cancellation
        callback returns ``True`` on its next poll. The raw control message is
        acknowledged immediately so that the BaseTask control handler does not
        double-process it.

        Handoff to main thread
        ----------------------
        After the runner unwinds (whether due to cancellation, an error, or
        normal completion), the main execution loop calls
        ``_finalize_deferred_active_control``.  That method re-reads the
        stored command and applies the terminal state transition,
        reserved-queue policy, and acknowledgement response in the correct
        order on the main thread, where all broker and TaskSpec state is owned.
        """

        self._deferred_active_control_command = command
        self._deferred_active_control_timestamp = timestamp
        self._deferred_active_control_request_id = request_id
        self.should_stop = True
        if command == CONTROL_KILL:
            self._kill_requested = True

    def _finalize_deferred_active_control(self) -> None:
        """Apply deferred STOP/KILL state transitions on the main task thread.

        Called by the main execution loop after the work runner has fully
        unwound.  Reads the command stashed by ``_defer_active_control``,
        applies the appropriate terminal transition (``_handle_stop_request``
        or ``_handle_kill_request``), enforces the reserved-queue policy, and
        sends the control acknowledgement.  Clears the deferred state before
        returning so a second call is a no-op.
        """

        command = self._deferred_active_control_command
        timestamp = self._deferred_active_control_timestamp
        request_id = self._deferred_active_control_request_id
        if command is None:
            return

        self._deferred_active_control_command = None
        self._deferred_active_control_timestamp = None
        self._deferred_active_control_request_id = None
        active_message_timestamp = self._active_message_timestamp
        has_active_reserved_message = active_message_timestamp is not None
        response_extra = {"request_id": request_id} if request_id is not None else {}

        if command == CONTROL_STOP:
            policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_stop)
            self._handle_stop_request(
                reason="STOP command received",
                event="control_stop",
                message_id=timestamp,
                apply_reserved_policy=False,
            )
            if has_active_reserved_message:
                self._apply_reserved_policy(
                    policy, message_timestamp=active_message_timestamp
                )
            if has_active_reserved_message and policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            self._send_control_response("STOP", "ack", **response_extra)
            return

        if command == CONTROL_KILL:
            policy = self._resolve_policy(self.taskspec.spec.reserved_policy_on_error)
            self._handle_kill_request(
                reason="KILL command received",
                event="control_kill",
                message_id=timestamp,
                apply_reserved_policy=False,
            )
            if has_active_reserved_message:
                self._apply_reserved_policy(
                    policy, message_timestamp=active_message_timestamp
                )
            if has_active_reserved_message and policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            self._send_control_response("KILL", "ack", **response_extra)

    def _make_task_runner(
        self,
        *,
        interactive: bool = False,
        broker_access: bool = True,
    ) -> TaskRunner:
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
            environment_profile_ref=self.taskspec.spec.runner.environment_profile_ref,
            bundle_root=self.taskspec.get_bundle_root(),
            persistent=self._task_is_persistent(),
            interactive=interactive,
            db_path=self._db_path if broker_access else None,
            config=self._config if broker_access else None,
        )

    def _begin_work_item(self, timestamp: int | None) -> bool:
        if self.taskspec.state.status == "running":
            self._set_activity("working")
            event = (
                "work_item_started" if self._task_is_persistent() else "work_started"
            )
            self._report_state_change(event=event, message_id=timestamp)
            self._update_process_title("running")
            return False

        self.taskspec.mark_started(pid=os.getpid())
        self._set_activity("working")
        self._update_process_title("spawning")
        self._report_state_change(event="work_spawning", message_id=timestamp)
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="work_started", message_id=timestamp)
        return True

    def _register_running_worker(self, pid: int | None) -> None:
        self.register_managed_pid(pid)

    def _finalize_message(
        self,
        timestamp: int | None,
        result_bytes: int,
        metrics_payload: dict[str, Any] | None,
        agent_execution: dict[str, Any] | None,
        *,
        initial_transition: bool,
    ) -> None:
        if timestamp is not None:
            try:
                self._get_reserved_queue().delete(message_id=timestamp)
            except (
                BrokerError,
                OSError,
                RuntimeError,
            ):  # pragma: no cover - broker ack best effort
                logger.debug(
                    "Failed to acknowledge reserved message %s",
                    timestamp,
                    exc_info=True,
                )
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()

        self._monitor_resource_usage()
        if self._task_is_persistent():
            self._set_activity("waiting", waiting_on=self._queue_names["inbox"])
            self._report_state_change(
                event="work_item_completed",
                message_id=timestamp,
                result_bytes=result_bytes,
                metrics=metrics_payload,
                agent_execution=agent_execution,
                initial_transition=initial_transition,
            )
            self._end_streaming_session()
            self._update_process_title("running")
            return

        self.taskspec.mark_completed(return_code=0)
        self._clear_activity()
        self._report_state_change(
            event="work_completed",
            message_id=timestamp,
            result_bytes=result_bytes,
            metrics=metrics_payload,
            agent_execution=agent_execution,
        )
        self._send_terminal_envelope()
        self._emit_pipeline_terminal_event(status="completed")
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

    @staticmethod
    def _build_agent_execution_payload(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, AgentExecutionResult):
            return None
        payload = {
            "runtime": result.runtime,
            "model": result.model,
            "output_mode": result.output_mode,
            "status": result.status,
            "metadata": result.metadata,
            "usage": result.usage,
            "tool_trace": list(result.tool_trace),
            "artifacts": list(result.artifacts),
        }
        try:
            serialized = json.loads(json.dumps(payload, default=str))
            if isinstance(serialized, dict):
                return serialized
        except (
            TypeError,
            ValueError,
            OverflowError,
        ):  # pragma: no cover - non-serializable agent payload
            logger.debug("Failed to serialize agent execution payload", exc_info=True)
        return {
            "runtime": result.runtime,
            "model": result.model,
            "output_mode": result.output_mode,
            "status": result.status,
        }

    def _raise_already_terminal(self, exc: BaseException) -> NoReturn:
        """Raise after minimal cleanup when state is already terminal."""
        self._end_streaming_session()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()
        raise exc

    def _finalize_terminal_outcome(
        self,
        *,
        title_state: str,
        title_detail: str | None,
        event: str,
        pipeline_status: str,
        timestamp: int | None,
        metrics_payload: dict[str, Any] | None,
        exc: BaseException,
        runner_diagnostics: dict[str, Any] | None = None,
    ) -> NoReturn:
        """Run standard terminal cleanup after the caller marks state."""
        self._clear_activity()
        if title_detail is None:
            self._update_process_title(title_state)
        else:
            self._update_process_title(title_state, title_detail)
        state_extra: dict[str, Any] = {
            "message_id": timestamp,
            "error": str(exc),
            "metrics": metrics_payload,
        }
        if runner_diagnostics is not None:
            state_extra["runner_diagnostics"] = runner_diagnostics
        self._report_state_change(event=event, **state_extra)
        self._send_terminal_envelope()
        self._emit_pipeline_terminal_event(
            status=pipeline_status,
            error=self.taskspec.state.error,
        )
        self._apply_reserved_policy_on_error(timestamp)
        self._end_streaming_session()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()
        raise exc

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

        if outcome.status not in VALID_RUNNER_OUTCOME_STATUSES:
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "cancelled":
                self._raise_already_terminal(
                    RuntimeError(
                        self.taskspec.state.error or "Target execution cancelled"
                    )
                )
            if self.taskspec.state.status == "killed":
                self._raise_already_terminal(
                    RuntimeError(self.taskspec.state.error or "Target execution killed")
                )
            error_exc = RuntimeError(
                f"unsupported runner outcome status {outcome.status!r}"
            )
            self.taskspec.mark_failed(error=str(error_exc))
            self._finalize_terminal_outcome(
                title_state="failed",
                title_detail=None,
                event="work_failed",
                pipeline_status="failed",
                timestamp=timestamp,
                metrics_payload=metrics_payload,
                exc=error_exc,
                runner_diagnostics=outcome.diagnostics,
            )

        if outcome.status == "timeout":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "cancelled":
                self._raise_already_terminal(
                    RuntimeError(
                        self.taskspec.state.error or "Target execution cancelled"
                    )
                )
            if self.taskspec.state.status == "killed":
                self._raise_already_terminal(
                    RuntimeError(self.taskspec.state.error or "Target execution killed")
                )
            timeout_exc = TimeoutError(outcome.error or "Target timeout")
            self.taskspec.mark_timeout(error=str(timeout_exc))
            self._finalize_terminal_outcome(
                title_state="timeout",
                title_detail=None,
                event="work_timeout",
                pipeline_status="timeout",
                timestamp=timestamp,
                metrics_payload=metrics_payload,
                exc=timeout_exc,
                runner_diagnostics=outcome.diagnostics,
            )

        if outcome.status == "limit":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "cancelled":
                self._raise_already_terminal(
                    RuntimeError(
                        self.taskspec.state.error or "Target execution cancelled"
                    )
                )
            if self.taskspec.state.status == "killed":
                self._raise_already_terminal(
                    RuntimeError(self.taskspec.state.error or "Target execution killed")
                )
            limit_exc = RuntimeError(outcome.error or "Resource limits exceeded")
            # Spec: docs/specifications/06-Resource_Management.md#error-categories
            self.taskspec.mark_killed(reason=str(limit_exc))
            self._finalize_terminal_outcome(
                title_state="killed",
                title_detail="limit",
                event="work_limit_violation",
                pipeline_status="killed",
                timestamp=timestamp,
                metrics_payload=metrics_payload,
                exc=limit_exc,
                runner_diagnostics=outcome.diagnostics,
            )

        if outcome.status == "error":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "cancelled":
                self._raise_already_terminal(
                    RuntimeError(
                        self.taskspec.state.error or "Target execution cancelled"
                    )
                )
            if self.taskspec.state.status == "killed":
                self._raise_already_terminal(
                    RuntimeError(self.taskspec.state.error or "Target execution killed")
                )
            error_exc = RuntimeError(outcome.error or "Target execution failed")
            self.taskspec.mark_failed(error=str(error_exc))
            self._finalize_terminal_outcome(
                title_state="failed",
                title_detail=None,
                event="work_failed",
                pipeline_status="failed",
                timestamp=timestamp,
                metrics_payload=metrics_payload,
                exc=error_exc,
                runner_diagnostics=outcome.diagnostics,
            )

        if outcome.status == "cancelled":
            self._finalize_deferred_active_control()
            if self.taskspec.state.status == "killed":
                self._raise_already_terminal(
                    RuntimeError(self.taskspec.state.error or "Target execution killed")
                )
            if self.taskspec.state.status == "cancelled":
                self._raise_already_terminal(
                    RuntimeError(
                        self.taskspec.state.error or "Target execution cancelled"
                    )
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

    def _cleanup_task_resources(self, deadline: float) -> None:
        """Release Consumer-owned sessions before shared task resources.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.10]
        """

        cleanup_enabled = getattr(
            self.taskspec.spec, "cleanup_on_exit", DEFAULT_CLEANUP_ON_EXIT
        )

        if self._interactive_mode:
            self._interactive_shutdown(deadline=deadline)

        self._stop_worker_lanes(deadline)
        self._shutdown_agent_session(deadline=deadline)

        if cleanup_enabled:
            self._purge_start_tokens()
            if not self._uses_live_command_streaming():
                self._purge_stream_markers()
            self._cleanup_spilled_outputs_if_needed()

        super()._cleanup_task_resources(deadline)

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

    def _shutdown_agent_session(self, *, deadline: float | None = None) -> None:
        if self._agent_session is None:
            return
        try:
            self._agent_session.close(deadline=deadline)
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
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker purge best effort
            return
        if not entries:
            return
        typed_entries = [cast(tuple[str, int], entry) for entry in entries]
        for body, ts in typed_entries:
            if self._is_start_token(body):
                try:
                    reserved_queue.delete(message_id=ts)
                except (
                    BrokerError,
                    OSError,
                    RuntimeError,
                ):  # pragma: no cover - broker purge best effort
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
            except (
                BrokerError,
                OSError,
                RuntimeError,
            ):  # pragma: no cover - broker purge best effort
                continue
            if not entries:
                continue
            typed_entries = [cast(tuple[str, int], entry) for entry in entries]
            for body, ts in typed_entries:
                if self._is_stream_final_marker(body):
                    try:
                        queue.delete(message_id=ts)
                    except (
                        BrokerError,
                        OSError,
                        RuntimeError,
                    ):  # pragma: no cover - broker purge best effort
                        logger.debug(
                            "Failed to purge stream sentinel %s from %s",
                            ts,
                            queue_name,
                            exc_info=True,
                        )


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
