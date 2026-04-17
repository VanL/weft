"""Internal task runtimes for first-class linear pipelines.

Spec references:
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-3.2], [PL-4.1], [PL-4.2], [PL-5.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-4], [MF-6]
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    FAILURE_LIKE_TASK_STATUSES,
    PIPELINE_EDGE_RUNTIME_METADATA_KEY,
    PIPELINE_RUNTIME_METADATA_KEY,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.core.pipelines import (
    CompiledPipelineEdge,
    CompiledPipelineStage,
    PipelineQueues,
)
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import ReservedPolicy, TaskSpec

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext

logger = logging.getLogger(__name__)


class PipelineEdgeRuntimeConfig(BaseModel):
    """Compiled runtime config for a generated edge task."""

    model_config = ConfigDict(extra="forbid")

    pipeline_tid: str
    edge_name: str
    source_kind: str
    source_queue: str
    target_queue: str
    events_queue: str
    upstream_stage: str | None = None
    upstream_tid: str | None = None
    downstream_stage: str | None = None
    downstream_tid: str | None = None
    override_input: Any = None
    emits_pipeline_result: bool = False


class PipelineRuntimeEnvelope(BaseModel):
    """Compiled runtime plan stored on the pipeline task metadata."""

    model_config = ConfigDict(extra="forbid")

    pipeline_name: str
    pipeline_tid: str
    source_ref: str | None = None
    queues: PipelineQueues
    stages: list[CompiledPipelineStage]
    edges: list[CompiledPipelineEdge]
    stage_taskspecs: list[dict[str, Any]]
    edge_taskspecs: list[dict[str, Any]]


class PipelineEdgeTask(BaseTask):
    """Generated one-shot edge that moves one payload and checkpoints progress."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        runtime_payload = taskspec.metadata.get(PIPELINE_EDGE_RUNTIME_METADATA_KEY)
        self._runtime = PipelineEdgeRuntimeConfig.model_validate(runtime_payload or {})
        super().__init__(db, taskspec, stop_event=stop_event, config=config)
        self._activate_waiter()
        self._set_activity("waiting", waiting_on=self._runtime.source_queue)
        self._emit_pipeline_started_event()

    def _activate_waiter(self) -> None:
        if self.taskspec.state.status != "created":
            return
        self.taskspec.mark_started(pid=os.getpid())
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._report_state_change(event="task_started")

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["inbox"]: self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=self._queue_names["reserved"],
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
        if self._paused:
            self._requeue_reserved_message(timestamp, context)
            return

        del message
        self._set_activity("working")
        try:
            self._handoff_payload(timestamp)
        except Exception as exc:
            self._fail_edge(str(exc), timestamp)
            return

        checkpoint_payload = {
            "type": "edge_checkpoint",
            "pipeline_tid": self._runtime.pipeline_tid,
            "edge_name": self._runtime.edge_name,
            "edge_tid": self.tid,
            "checkpoint": "queued_for_downstream",
            "queue": self._runtime.target_queue,
            "message_id": str(timestamp),
            "downstream_stage": self._runtime.downstream_stage,
            "downstream_tid": self._runtime.downstream_tid,
            "timestamp": time.time_ns(),
        }
        try:
            self._queue(self._runtime.events_queue).write(
                json.dumps(checkpoint_payload, ensure_ascii=False)
            )
        except Exception:
            logger.debug(
                "Failed to emit edge checkpoint for %s",
                self._runtime.edge_name,
                exc_info=True,
            )
            self._report_state_change(
                event="edge_checkpoint_emit_failed",
                message_id=timestamp,
                checkpoint=checkpoint_payload,
            )

        self.taskspec.mark_completed(return_code=0)
        self._clear_activity()
        self._report_state_change(
            event="work_completed",
            message_id=timestamp,
            checkpoint=checkpoint_payload,
        )
        self._update_process_title("completed")
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()

    def _requeue_reserved_message(
        self,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        if context.reserved_queue_name is None:
            return
        try:
            self._queue(context.reserved_queue_name).move_one(
                context.queue_name,
                exact_timestamp=timestamp,
                require_unclaimed=True,
                with_timestamps=False,
            )
        except Exception:
            logger.debug(
                "Failed to requeue edge message %s while paused",
                timestamp,
                exc_info=True,
            )

    def _handoff_payload(self, timestamp: int) -> None:
        reserved_queue = self._get_reserved_queue()
        if self._runtime.override_input is None:
            reserved_queue.move_one(
                self._runtime.target_queue,
                exact_timestamp=timestamp,
                require_unclaimed=True,
                with_timestamps=False,
            )
            return

        payload = self._runtime.override_input
        payload_text = payload if isinstance(payload, str) else json.dumps(payload)
        self._queue(self._runtime.target_queue).write(payload_text)
        reserved_queue.delete(message_id=timestamp)
        self._ensure_reserved_empty()
        self._cleanup_reserved_if_needed()

    def _fail_edge(self, error: str, timestamp: int) -> None:
        self.taskspec.mark_failed(error=error)
        self._clear_activity()
        self._report_state_change(
            event="work_failed",
            message_id=timestamp,
            error=error,
        )
        self._emit_pipeline_terminal_event(status="failed", error=error)
        policy = self.taskspec.spec.reserved_policy_on_error
        self._apply_reserved_policy(policy, message_timestamp=timestamp)
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()


class PipelineTask(BaseTask):
    """Internal orchestrator task for a compiled first-class linear pipeline."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        runtime_payload = taskspec.metadata.get(PIPELINE_RUNTIME_METADATA_KEY)
        self._runtime = PipelineRuntimeEnvelope.model_validate(runtime_payload or {})
        self._bootstrapped = False
        self._registry_message_id: int | None = None
        self._status_snapshot = self._build_initial_snapshot()
        super().__init__(db, taskspec, stop_event=stop_event, config=config)
        self._activate_runtime()

    def _build_initial_snapshot(self) -> dict[str, Any]:
        return {
            "type": "pipeline_status",
            "pipeline_tid": self._runtime.pipeline_tid,
            "pipeline_name": self._runtime.pipeline_name,
            "status": "running",
            "activity": "bootstrapping",
            "current_stage": None,
            "current_edge": None,
            "last_checkpoint": None,
            "queues": self._runtime.queues.model_dump(mode="json"),
            "source_ref": self._runtime.source_ref,
            "bindings": [
                {
                    "edge_name": edge.name,
                    "queue": edge.target_queue,
                }
                for edge in self._runtime.edges
            ],
            "stages": [
                {
                    "name": stage.name,
                    "child_tid": stage.tid,
                    "status": "created",
                    "activity": "queued",
                    "waiting_on": WEFT_SPAWN_REQUESTS_QUEUE,
                }
                for stage in self._runtime.stages
            ],
            "edges": [
                {
                    "name": edge.name,
                    "child_tid": edge.tid,
                    "status": "created",
                    "activity": "queued",
                    "waiting_on": WEFT_SPAWN_REQUESTS_QUEUE,
                    "source_queue": edge.source_queue,
                    "target_queue": edge.target_queue,
                    "upstream_stage": edge.upstream_stage,
                    "downstream_stage": edge.downstream_stage,
                }
                for edge in self._runtime.edges
            ],
        }

    def _activate_runtime(self) -> None:
        if self.taskspec.state.status != "created":
            return
        self.taskspec.mark_started(pid=os.getpid())
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._report_state_change(event="task_started")
        self._set_activity("bootstrapping")

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
            self._runtime.queues.events: self._read_queue_config(
                self._handle_pipeline_event
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        del message, timestamp, context
        logger.debug("Ignoring unexpected pipeline inbox message for %s", self.tid)

    def process_once(self) -> None:
        if not self._bootstrapped and not self.should_stop:
            self._bootstrap_children()
        super().process_once()

    def _bootstrap_children(self) -> None:
        submitted_ctrl_queues: list[str] = []
        self._write_pipeline_registry_record()
        try:
            for taskspec_payload in self._runtime.stage_taskspecs:
                submitted_ctrl_queues.append(self._submit_child_spawn(taskspec_payload))
            for taskspec_payload in self._runtime.edge_taskspecs:
                submitted_ctrl_queues.append(self._submit_child_spawn(taskspec_payload))
        except Exception as exc:
            self._broadcast_control(CONTROL_STOP, ctrl_queues=submitted_ctrl_queues)
            self._fail_pipeline(
                f"Pipeline bootstrap failed: {exc}",
                child_kind="bootstrap",
                child_name=None,
                child_tid=None,
            )
            return

        self._bootstrapped = True
        self._set_activity("waiting")
        self._publish_pipeline_snapshot()

    def _submit_child_spawn(self, taskspec_payload: Mapping[str, Any]) -> str:
        ctrl_queue = (
            taskspec_payload.get("io", {}).get("control", {}).get("ctrl_in", "")
        )
        submit_spawn_request(
            self._db_path,
            taskspec=taskspec_payload,
            work_payload=None,
            config=self._config,
            tid=taskspec_payload.get("tid"),
            seed_start_envelope=False,
            allow_internal_runtime=True,
        )
        return str(ctrl_queue)

    def _handle_pipeline_event(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        del timestamp, context
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON pipeline event %s", message)
            return
        if not isinstance(payload, dict):
            return

        event_type = payload.get("type")
        if event_type == "stage_started":
            self._handle_stage_started(payload)
        elif event_type == "stage_terminal":
            self._handle_stage_terminal(payload)
        elif event_type == "edge_started":
            self._handle_edge_started(payload)
        elif event_type == "edge_checkpoint":
            self._handle_edge_checkpoint(payload)
        elif event_type == "edge_terminal":
            self._handle_edge_terminal(payload)

    def _handle_stage_started(self, payload: Mapping[str, Any]) -> None:
        stage_name = payload.get("stage_name")
        if not isinstance(stage_name, str):
            return
        for stage in self._status_snapshot["stages"]:
            if stage.get("name") != stage_name:
                continue
            stage["status"] = "running"
            activity = payload.get("activity")
            waiting_on = payload.get("waiting_on")
            if isinstance(activity, str) and activity:
                stage["activity"] = activity
            else:
                stage["activity"] = "waiting"
            if isinstance(waiting_on, str) and waiting_on:
                stage["waiting_on"] = waiting_on
            else:
                stage.pop("waiting_on", None)
            break
        self._publish_pipeline_snapshot()

    def _handle_stage_terminal(self, payload: Mapping[str, Any]) -> None:
        stage_name = payload.get("stage_name")
        status = payload.get("status")
        if not isinstance(stage_name, str) or not isinstance(status, str):
            return
        for stage in self._status_snapshot["stages"]:
            if stage.get("name") != stage_name:
                continue
            stage["status"] = status
            stage.pop("activity", None)
            stage.pop("waiting_on", None)
            error = payload.get("error")
            if isinstance(error, str):
                stage["error"] = error
            break

        if status in FAILURE_LIKE_TASK_STATUSES:
            self._fail_pipeline(
                self._failure_message(payload, child_name=stage_name),
                child_kind="stage",
                child_name=stage_name,
                child_tid=self._mapping_str(payload, "child_tid"),
            )
            return
        self._status_snapshot["current_stage"] = None
        self._status_snapshot["current_edge"] = self._next_edge_name_for_stage(
            stage_name
        )
        self._publish_pipeline_snapshot()

    def _handle_edge_checkpoint(self, payload: Mapping[str, Any]) -> None:
        edge_name = payload.get("edge_name")
        if not isinstance(edge_name, str):
            return
        for edge in self._status_snapshot["edges"]:
            if edge.get("name") != edge_name:
                continue
            edge["status"] = "completed"
            edge.pop("activity", None)
            edge.pop("waiting_on", None)
            edge["checkpoint"] = payload.get("checkpoint")
            edge["queue"] = payload.get("queue")
            edge["message_id"] = payload.get("message_id")
            break

        self._status_snapshot["current_edge"] = None
        self._status_snapshot["last_checkpoint"] = {
            "edge_name": edge_name,
            "checkpoint": payload.get("checkpoint"),
            "message_id": payload.get("message_id"),
            "queue": payload.get("queue"),
            "timestamp": payload.get("timestamp"),
        }

        if bool(payload.get("downstream_stage")):
            stage_name = payload.get("downstream_stage")
            self._status_snapshot["current_stage"] = stage_name
            for stage in self._status_snapshot["stages"]:
                if stage.get("name") == stage_name and stage.get("status") == "running":
                    stage["activity"] = "waiting"
                    stage["waiting_on"] = payload.get("queue")
                    break

        if edge_name == self._runtime.edges[-1].name:
            self._complete_pipeline()
            return
        self._publish_pipeline_snapshot()

    def _handle_edge_started(self, payload: Mapping[str, Any]) -> None:
        edge_name = payload.get("edge_name")
        if not isinstance(edge_name, str):
            return
        for edge in self._status_snapshot["edges"]:
            if edge.get("name") != edge_name:
                continue
            edge["status"] = "running"
            activity = payload.get("activity")
            waiting_on = payload.get("waiting_on")
            if isinstance(activity, str) and activity:
                edge["activity"] = activity
            else:
                edge["activity"] = "waiting"
            if isinstance(waiting_on, str) and waiting_on:
                edge["waiting_on"] = waiting_on
            else:
                edge.pop("waiting_on", None)
            break
        self._publish_pipeline_snapshot()

    def _handle_edge_terminal(self, payload: Mapping[str, Any]) -> None:
        edge_name = payload.get("edge_name")
        status = payload.get("status")
        if not isinstance(edge_name, str) or not isinstance(status, str):
            return
        if status in FAILURE_LIKE_TASK_STATUSES:
            self._fail_pipeline(
                self._failure_message(payload, child_name=edge_name),
                child_kind="edge",
                child_name=edge_name,
                child_tid=self._mapping_str(payload, "child_tid"),
            )
            return
        self._publish_pipeline_snapshot()

    def _failure_message(
        self,
        payload: Mapping[str, Any],
        *,
        child_name: str,
    ) -> str:
        status = self._mapping_str(payload, "status") or "failed"
        child_tid = self._mapping_str(payload, "child_tid")
        error = self._mapping_str(payload, "error")
        base = (
            f"{child_name} ({child_tid}) {status}"
            if child_tid
            else f"{child_name} {status}"
        )
        if error:
            return f"Pipeline failed: {base}: {error}"
        return f"Pipeline failed: {base}"

    def _mapping_str(self, payload: Mapping[str, Any], key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    def _next_edge_name_for_stage(self, stage_name: str) -> str | None:
        for edge in self._runtime.edges:
            if edge.upstream_stage == stage_name:
                return edge.name
        return None

    def _publish_pipeline_snapshot(self) -> None:
        payload = {
            **self._status_snapshot,
            "status": self.taskspec.state.status,
            "timestamp": time.time_ns(),
        }
        if (
            self._activity is not None
            and self.taskspec.state.status
            not in FAILURE_LIKE_TASK_STATUSES.union({"completed"})
        ):
            payload["activity"] = self._activity
            if self._waiting_on is not None:
                payload["waiting_on"] = self._waiting_on
        else:
            payload.pop("activity", None)
            payload.pop("waiting_on", None)
        self._status_snapshot = payload
        try:
            self._queue(self._runtime.queues.status).write(
                json.dumps(payload, ensure_ascii=False)
            )
        except Exception:
            logger.debug("Failed to publish pipeline snapshot", exc_info=True)

    def _write_pipeline_registry_record(self) -> None:
        payload = {
            "tid": self.tid,
            "name": self._runtime.pipeline_name,
            "role": "pipeline",
            "status": "active",
            "timestamp": time.time_ns(),
            "source_ref": self._runtime.source_ref,
            "queues": self._runtime.queues.model_dump(mode="json"),
            "stages": [
                {"name": stage.name, "tid": stage.tid} for stage in self._runtime.stages
            ],
            "edges": [
                {
                    "name": edge.name,
                    "tid": edge.tid,
                    "source_queue": edge.source_queue,
                    "target_queue": edge.target_queue,
                }
                for edge in self._runtime.edges
            ],
        }
        queue = self._queue(WEFT_PIPELINES_STATE_QUEUE)
        queue.write(json.dumps(payload, ensure_ascii=False))
        self._registry_message_id = self._latest_pipeline_registry_message_id()

    def _latest_pipeline_registry_message_id(self) -> int | None:
        latest: int | None = None
        for entry in self._queue(WEFT_PIPELINES_STATE_QUEUE).peek_generator(
            with_timestamps=True
        ):
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            body, timestamp = entry
            try:
                payload = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                continue
            if payload.get("tid") == self.tid:
                latest = int(timestamp)
        return latest

    def _delete_pipeline_registry_record(self) -> None:
        queue = self._queue(WEFT_PIPELINES_STATE_QUEUE)
        message_id = (
            self._registry_message_id or self._latest_pipeline_registry_message_id()
        )
        if message_id is None:
            return
        try:
            queue.delete(message_id=message_id)
        except Exception:
            logger.debug("Failed to delete pipeline registry record", exc_info=True)

    def _broadcast_control(
        self,
        command: str,
        *,
        ctrl_queues: list[str] | None = None,
    ) -> None:
        queue_names = ctrl_queues or [
            stage.ctrl_in_queue for stage in self._runtime.stages
        ] + [edge.taskspec["io"]["control"]["ctrl_in"] for edge in self._runtime.edges]
        for queue_name in queue_names:
            if not queue_name:
                continue
            try:
                self._queue(queue_name).write(command)
            except Exception:
                logger.debug(
                    "Failed to send %s to %s", command, queue_name, exc_info=True
                )

    def _fail_pipeline(
        self,
        error: str,
        *,
        child_kind: str,
        child_name: str | None,
        child_tid: str | None,
    ) -> None:
        if self.taskspec.state.status in FAILURE_LIKE_TASK_STATUSES.union(
            {"completed"}
        ):
            return
        self._broadcast_control(CONTROL_STOP)
        self.taskspec.mark_failed(error=error)
        self._clear_activity()
        self._status_snapshot["failure"] = {
            "child_kind": child_kind,
            "child_name": child_name,
            "child_tid": child_tid,
            "error": error,
        }
        self._report_state_change(event="work_failed", error=error)
        self._publish_pipeline_snapshot()
        self._delete_pipeline_registry_record()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()

    def _complete_pipeline(self) -> None:
        if self.taskspec.state.status in FAILURE_LIKE_TASK_STATUSES.union(
            {"completed"}
        ):
            return
        self.taskspec.mark_completed(return_code=0)
        self._clear_activity()
        self._report_state_change(event="work_completed")
        self._publish_pipeline_snapshot()
        self._delete_pipeline_registry_record()
        self.should_stop = True
        if self._stop_event:
            self._stop_event.set()

    def _handle_control_command(
        self, command: str, context: QueueMessageContext
    ) -> bool:
        if command == "PING":
            self._send_control_response("PING", "ok", message="PONG")
            return True
        if command == "STATUS":
            self._publish_pipeline_snapshot()
            self._send_control_response(
                "STATUS",
                "ok",
                task_status=self.taskspec.state.status,
                status_queue=self._runtime.queues.status,
            )
            return True
        if command == CONTROL_STOP:
            self._broadcast_control(CONTROL_STOP)
            self._handle_stop_request(
                reason="STOP command received",
                event="control_stop",
                message_id=context.timestamp,
                apply_reserved_policy=False,
            )
            self._publish_pipeline_snapshot()
            self._delete_pipeline_registry_record()
            self._send_control_response("STOP", "ack")
            return True
        if command == CONTROL_KILL:
            self._broadcast_control(CONTROL_KILL)
            self._handle_kill_request(
                reason="KILL command received",
                event="control_kill",
                message_id=context.timestamp,
                apply_reserved_policy=False,
            )
            self._publish_pipeline_snapshot()
            self._delete_pipeline_registry_record()
            self._send_control_response("KILL", "ack")
            return True
        return super()._handle_control_command(command, context)
