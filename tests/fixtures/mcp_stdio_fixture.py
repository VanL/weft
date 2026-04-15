"""Tiny stdio MCP fixture used by delegated-runtime tests.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5]
- docs/specifications/13A-Agent_Runtime_Planned.md [AR-A3], [AR-A4]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FIXTURE_TOOL_NAME = "return_token"
FIXTURE_SERVER_NAME = "weft-mcp-fixture"
FIXTURE_PROTOCOL_VERSION = "2025-11-05"


def fixture_server_script_path() -> Path:
    """Return the absolute path to this fixture server script."""
    return Path(__file__).resolve()


def call_fixture_tool(
    server_config: Mapping[str, Any],
    *,
    token: str,
) -> str:
    """Call the fixture MCP tool through a real stdio server subprocess."""
    command = _server_command(server_config)
    env = os.environ.copy()
    server_env = server_config.get("env")
    if isinstance(server_env, Mapping):
        env.update({str(key): str(value) for key, value in server_env.items()})
    cwd = _server_cwd(server_config)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": FIXTURE_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "weft-provider-cli-fixture",
                        "version": "0.0.0",
                    },
                },
            },
        )
        _read_response(process.stdout, expected_id=1)
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        tools_response = _read_response(process.stdout, expected_id=2)
        tools = tools_response.get("result", {}).get("tools", [])
        if not isinstance(tools, list) or not any(
            isinstance(tool, dict) and tool.get("name") == FIXTURE_TOOL_NAME
            for tool in tools
        ):
            raise RuntimeError(
                f"{FIXTURE_TOOL_NAME!r} not advertised by fixture server"
            )
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": FIXTURE_TOOL_NAME,
                    "arguments": {"token": token},
                },
            },
        )
        call_response = _read_response(process.stdout, expected_id=3)
        content = call_response.get("result", {}).get("content", [])
        if not isinstance(content, list):
            raise RuntimeError("fixture MCP server returned invalid content")
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    return text
        raise RuntimeError("fixture MCP server did not return text content")
    finally:
        _close_process(process)


def main(argv: list[str] | None = None) -> int:
    """Run the tiny stdio MCP fixture server."""
    del argv
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = json.loads(line)
        if not isinstance(request, dict):
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            protocol_version = FIXTURE_PROTOCOL_VERSION
            params = request.get("params")
            if isinstance(params, dict):
                requested_version = params.get("protocolVersion")
                if isinstance(requested_version, str) and requested_version.strip():
                    protocol_version = requested_version.strip()
            _write_message(
                sys.stdout,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": protocol_version,
                        "capabilities": {
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": FIXTURE_SERVER_NAME,
                            "version": "0.0.0",
                        },
                    },
                },
            )
            continue
        if method == "ping":
            _write_message(
                sys.stdout,
                {"jsonrpc": "2.0", "id": request_id, "result": {}},
            )
            continue
        if method == "roots/list":
            _write_message(
                sys.stdout,
                {"jsonrpc": "2.0", "id": request_id, "result": {"roots": []}},
            )
            continue
        if method == "tools/list":
            _write_message(
                sys.stdout,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": FIXTURE_TOOL_NAME,
                                "description": (
                                    "Return the exact token argument unchanged."
                                ),
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "token": {"type": "string"},
                                    },
                                    "required": ["token"],
                                },
                            }
                        ]
                    },
                },
            )
            continue
        if method == "tools/call":
            params = request.get("params")
            token = ""
            tool_name = None
            if isinstance(params, dict):
                tool_name = params.get("name")
                arguments = params.get("arguments")
                if isinstance(arguments, dict):
                    raw_token = arguments.get("token")
                    if isinstance(raw_token, str):
                        token = raw_token
            if tool_name != FIXTURE_TOOL_NAME:
                _write_message(
                    sys.stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": f"Unknown fixture tool: {tool_name}",
                        },
                    },
                )
                continue
            _write_message(
                sys.stdout,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": token,
                            }
                        ],
                        "structuredContent": {"token": token},
                    },
                },
            )
            continue
        if request_id is None:
            continue
        _write_message(
            sys.stdout,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            },
        )
    return 0


def _server_command(server_config: Mapping[str, Any]) -> list[str]:
    command = server_config.get("command")
    if not isinstance(command, str) or not command.strip():
        raise RuntimeError("fixture MCP server config is missing command")
    args = server_config.get("args") or ()
    if isinstance(args, str):
        raise RuntimeError("fixture MCP server args must be a sequence of strings")
    command_parts = [command]
    for value in args:
        if not isinstance(value, str):
            raise RuntimeError("fixture MCP server args must be strings")
        command_parts.append(value)
    return command_parts


def _server_cwd(server_config: Mapping[str, Any]) -> str | None:
    value = server_config.get("cwd")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("fixture MCP server cwd must be a non-empty string")
    return value


def _write_message(stream: Any, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(payload, sort_keys=True) + "\n")
    stream.flush()


def _read_response(stream: Any, *, expected_id: int) -> dict[str, Any]:
    while True:
        raw_line = stream.readline()
        if raw_line == "":
            raise RuntimeError("fixture MCP server closed its stdout unexpectedly")
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        if payload.get("id") != expected_id:
            continue
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            raise RuntimeError(
                str(message)
                if isinstance(message, str)
                else "fixture MCP request failed"
            )
        return payload


def _close_process(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
