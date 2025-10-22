"""Debugger task that exposes diagnostic commands via interactive interface."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, cast

from .consumer import Consumer
from .sessions import CommandSession, InProcessCommandSession


class Debugger(Consumer):
    """Task that answers diagnostic commands without spawning a subprocess."""

    _COMMANDS = ("menu", "help", "ping", "info", "queues")

    def _interactive_ensure_session(self, message_id: int) -> CommandSession:
        if getattr(self, "_interactive_session", None) is not None:
            return cast(CommandSession, self._interactive_session)

        session = InProcessCommandSession(self._handle_session_input)
        self._interactive_session = cast(CommandSession, session)
        self._interactive_runner = None
        self._interactive_started = True
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="work_started", message_id=message_id)
        return self._interactive_session

    def _handle_session_input(self, raw: str) -> tuple[str | None, str | None, bool]:
        responses: list[str] = []
        for line in filter(None, raw.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"command": line}
            response = self._handle_command(payload)
            responses.append(json.dumps(response, ensure_ascii=False))
        stdout = "\n".join(responses) + ("\n" if responses else "")
        done = (
            any(resp.get("exit") for resp in (json.loads(r) for r in responses))
            if responses
            else False
        )
        return (stdout or None, None, done)

    def _handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("command", "")).strip().lower()

        if command in {"", "menu", "help"}:
            return {
                "command": "menu",
                "commands": list(self._COMMANDS),
            }

        if command == "ping":
            return {
                "command": "ping",
                "response": "pong",
                "timestamp": time.time_ns(),
            }

        if command == "info":
            return {
                "command": "info",
                "tid": self.tid,
                "name": self.taskspec.name,
                "status": self.taskspec.state.status,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "metadata": self.taskspec.metadata,
                "timestamp": time.time_ns(),
            }

        if command == "queues":
            return {
                "command": "queues",
                "queues": dict(self._queue_names),
                "timestamp": time.time_ns(),
            }

        return {
            "command": command,
            "error": "unknown_command",
            "available": list(self._COMMANDS),
        }


__all__ = ["Debugger"]
