"""Namespace adapters for the public Python client."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weft.commands import manager as managers
from weft.commands import queue as queues
from weft.commands import specs, tasks
from weft.commands import status as system
from weft.commands.types import (
    EndpointResolution,
    ManagerSnapshot,
    QueueAliasRecord,
    QueueBroadcastReceipt,
    QueueDeleteReceipt,
    QueueEntry,
    QueueInfo,
    QueueMoveReceipt,
    QueueWriteReceipt,
    SpecRecord,
    SpecValidationResult,
    SystemLoadResult,
    SystemStatusSnapshot,
    SystemTidyResult,
    TaskSnapshot,
)

from ._types import ClientContextHandle


@dataclass(slots=True)
class TasksNamespace:
    client: ClientContextHandle

    def list(
        self,
        *,
        status: str | None = None,
        include_terminal: bool = False,
    ) -> list[TaskSnapshot]:
        return tasks.list_task_snapshots(
            status_filter=status,
            include_terminal=include_terminal,
            context=self.client.context,
        )

    def stats(
        self,
        *,
        status: str | None = None,
        include_terminal: bool = False,
    ) -> dict[str, int]:
        return tasks.task_stats(
            status_filter=status,
            include_terminal=include_terminal,
            context=self.client.context,
        )

    def status(
        self,
        tid: str,
        *,
        include_process: bool = False,
    ) -> TaskSnapshot | None:
        return tasks.task_snapshot(
            tid,
            include_process=include_process,
            context=self.client.context,
        )

    def watch(
        self,
        tid: str,
        *,
        include_process: bool = False,
    ) -> Iterator[TaskSnapshot]:
        yield from tasks.watch_task_status(
            tid,
            include_process=include_process,
            context=self.client.context,
        )

    def resolve_tid(
        self,
        *,
        tid: str | None = None,
        pid: int | None = None,
        reverse: str | None = None,
    ) -> str | None:
        return tasks.resolve_tid(
            tid=tid,
            pid=pid,
            reverse=reverse,
            context=self.client.context,
        )

    def stop(self, tid: str) -> None:
        tasks.stop_task(tid, context=self.client.context)

    def stop_many(
        self,
        *,
        tids: Sequence[str] | None = None,
        all_tasks: bool = False,
        pattern: str | None = None,
    ) -> int:
        if all_tasks:
            resolved = [item.tid for item in self.list(include_terminal=True)]
        elif pattern is not None:
            resolved = tasks.filter_tids_by_pattern(
                self.list(include_terminal=True),
                pattern,
            )
        else:
            resolved = list(tids or [])
        return tasks.stop_tasks(resolved, context=self.client.context)

    def kill(self, tid: str) -> None:
        tasks.kill_task(tid, context=self.client.context)

    def kill_many(
        self,
        *,
        tids: Sequence[str] | None = None,
        all_tasks: bool = False,
        pattern: str | None = None,
    ) -> int:
        if all_tasks:
            resolved = [item.tid for item in self.list(include_terminal=True)]
        elif pattern is not None:
            resolved = tasks.filter_tids_by_pattern(
                self.list(include_terminal=True),
                pattern,
            )
        else:
            resolved = list(tids or [])
        return tasks.kill_tasks(resolved, context=self.client.context)


@dataclass(slots=True)
class QueueAliasesNamespace:
    client: ClientContextHandle

    def add(self, alias: str, target: str) -> QueueAliasRecord:
        return queues.add_alias(self.client.context, alias, target)

    def list(self, *, target: str | None = None) -> list[QueueAliasRecord]:
        return queues.list_alias_records(self.client.context, target=target)

    def remove(self, alias: str) -> None:
        queues.remove_alias(self.client.context, alias)


@dataclass(slots=True)
class QueuesNamespace:
    client: ClientContextHandle
    aliases: QueueAliasesNamespace = field(init=False)

    def __post_init__(self) -> None:
        self.aliases = QueueAliasesNamespace(self.client)

    def read(
        self,
        name: str,
        *,
        all_messages: bool = False,
        message_id: int | None = None,
        since: int | None = None,
    ) -> list[QueueEntry]:
        return queues.read_queue(
            self.client.context,
            name,
            all_messages=all_messages,
            message_id=message_id,
            since=since,
        )

    def write(self, name: str, message: str) -> QueueWriteReceipt:
        return queues.write_queue(self.client.context, name, message)

    def write_endpoint(self, name: str, message: str) -> QueueWriteReceipt:
        return queues.write_endpoint(self.client.context, name, message)

    def peek(
        self,
        name: str,
        *,
        all_messages: bool = False,
        message_id: int | None = None,
        since: int | None = None,
    ) -> list[QueueEntry]:
        return queues.peek_queue(
            self.client.context,
            name,
            all_messages=all_messages,
            message_id=message_id,
            since=since,
        )

    def move(
        self,
        source: str,
        destination: str,
        *,
        limit: int | None = None,
        all_messages: bool = False,
        message_id: int | None = None,
        since: int | None = None,
    ) -> QueueMoveReceipt:
        return queues.move_queue_messages(
            self.client.context,
            source,
            destination,
            limit=limit,
            all_messages=all_messages,
            message_id=message_id,
            since=since,
        )

    def list(
        self,
        *,
        pattern: str | None = None,
        include_stats: bool = False,
        include_endpoints: bool = False,
    ) -> list[QueueInfo]:
        return queues.list_queue_infos(
            self.client.context,
            pattern=pattern,
            include_stats=include_stats,
            include_endpoints=include_endpoints,
        )

    def resolve(self, endpoint_name: str) -> EndpointResolution | None:
        return queues.resolve_queue_endpoint(self.client.context, endpoint_name)

    def watch(
        self,
        name: str,
        *,
        limit: int | None = None,
        interval: float = 0.5,
        peek: bool = False,
        since: int | None = None,
        move_to: str | None = None,
    ) -> Iterator[QueueEntry]:
        yield from queues.watch_queue_entries(
            self.client.context,
            name,
            limit=limit,
            interval=interval,
            peek=peek,
            since=since,
            move_to=move_to,
        )

    def delete(
        self,
        name: str | None = None,
        *,
        all_queues: bool = False,
        message_id: int | None = None,
    ) -> QueueDeleteReceipt:
        return queues.delete_queue_messages(
            self.client.context,
            name,
            all_queues=all_queues,
            message_id=message_id,
        )

    def broadcast(
        self,
        message: str,
        *,
        pattern: str | None = None,
    ) -> QueueBroadcastReceipt:
        return queues.broadcast(self.client.context, message, pattern=pattern)


@dataclass(slots=True)
class ManagersNamespace:
    client: ClientContextHandle

    def start(self) -> ManagerSnapshot:
        return managers.start_manager(self.client.context)

    def serve(self) -> None:
        managers.serve_manager(self.client.context)

    def stop(self, tid: str, *, force: bool = False, timeout: float = 5.0) -> None:
        managers.stop_manager(self.client.context, tid, force=force, timeout=timeout)

    def list(self, *, include_stopped: bool = False) -> list[ManagerSnapshot]:
        return managers.list_managers(
            self.client.context,
            include_stopped=include_stopped,
        )

    def status(self, tid: str) -> ManagerSnapshot | None:
        return managers.manager_status(self.client.context, tid)


@dataclass(slots=True)
class SpecsNamespace:
    client: ClientContextHandle

    def create(
        self,
        name: str,
        source: Path | dict[str, Any],
        *,
        spec_type: str = "task",
        force: bool = False,
    ) -> SpecRecord:
        return specs.create_spec_record(
            name,
            source,
            spec_type=spec_type,
            force=force,
            context=self.client.context,
        )

    def list(self, *, spec_type: str | None = None) -> list[SpecRecord]:
        return specs.list_spec_records(
            spec_type=spec_type,
            context=self.client.context,
        )

    def show(
        self,
        name: str,
        *,
        spec_type: str | None = None,
    ) -> dict[str, Any]:
        return specs.show_spec(
            name,
            spec_type=spec_type,
            context=self.client.context,
        )

    def delete(self, name: str, *, spec_type: str | None = None) -> Path:
        return specs.delete_spec(
            name,
            spec_type=spec_type,
            context=self.client.context,
        )

    def validate(
        self,
        source: Path | dict[str, Any],
        *,
        spec_type: str | None = None,
        load_runner: bool = False,
        preflight: bool = False,
    ) -> SpecValidationResult:
        return specs.validate_spec_source(
            source,
            spec_type=spec_type,
            load_runner=load_runner,
            preflight=preflight,
            context=self.client.context,
        )

    def generate(self, *, spec_type: str = "task") -> dict[str, Any]:
        return specs.generate_spec(spec_type)


@dataclass(slots=True)
class SystemNamespace:
    client: ClientContextHandle

    def status(self) -> SystemStatusSnapshot:
        return system.system_status(self.client.context)

    def tidy(self) -> SystemTidyResult:
        return system.tidy_system(self.client.context)

    def dump(self, *, output: str | Path | None = None) -> Path:
        return system.dump_system(self.client.context, output=output)

    def builtins(self) -> list[dict[str, Any]]:
        return system.list_builtins()

    def load(
        self,
        *,
        input_file: str | Path | None = None,
        dry_run: bool = False,
    ) -> SystemLoadResult:
        return system.load_system(
            self.client.context,
            input_file=input_file,
            dry_run=dry_run,
        )
