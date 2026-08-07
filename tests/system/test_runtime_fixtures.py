"""Contract tests for delegated-runtime fixture helpers."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pytest

from tests.fixtures import mcp_stdio_fixture, provider_cli_fixture

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            {"command": "fixture", "args": "--not-a-sequence"},
            "fixture MCP server args must be a sequence of strings",
        ),
        (
            {"command": "fixture", "args": [1]},
            "fixture MCP server args must be strings",
        ),
    ],
)
def test_mcp_server_command_rejects_invalid_argument_shapes_as_type_errors(
    config: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError) as exc_info:
        mcp_stdio_fixture._server_command(config)
    assert type(exc_info.value) is TypeError
    assert str(exc_info.value) == message


def test_mcp_response_reports_untyped_remote_error_as_runtime_error() -> None:
    stream = StringIO('{"id": 1, "error": {"message": 42}}\n')

    with pytest.raises(RuntimeError) as exc_info:
        mcp_stdio_fixture._read_response(stream, expected_id=1)
    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == "fixture MCP request failed"


def test_mcp_tool_call_rejects_invalid_remote_content_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(
        stdin=StringIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    responses = iter(
        (
            {},
            {"result": {"tools": [{"name": mcp_stdio_fixture.FIXTURE_TOOL_NAME}]}},
            {"result": {"content": {}}},
        )
    )
    monkeypatch.setattr(mcp_stdio_fixture, "_server_command", lambda _config: ["x"])
    monkeypatch.setattr(mcp_stdio_fixture, "_server_cwd", lambda _config: None)
    monkeypatch.setattr(
        mcp_stdio_fixture.subprocess, "Popen", lambda *_a, **_k: process
    )
    monkeypatch.setattr(
        mcp_stdio_fixture,
        "_read_response",
        lambda _stream, *, expected_id: next(responses),
    )
    monkeypatch.setattr(mcp_stdio_fixture, "_close_process", lambda _process: None)

    with pytest.raises(RuntimeError) as exc_info:
        mcp_stdio_fixture.call_fixture_tool({}, token="token")
    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == "fixture MCP server returned invalid content"


@pytest.mark.parametrize(
    ("raw_value", "message"),
    [
        ("[]", "Claude MCP config must be a JSON object"),
        ('{"mcpServers": [1]}', "Claude MCP config must contain mcpServers"),
    ],
)
def test_claude_mcp_config_rejects_invalid_shapes_as_value_errors(
    raw_value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        provider_cli_fixture._load_claude_mcp_servers(raw_value)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message


@pytest.mark.parametrize("raw_value", ["{}", '{"mcpServers": []}'])
def test_claude_mcp_config_normalizes_missing_or_falsey_servers(
    raw_value: str,
) -> None:
    assert provider_cli_fixture._load_claude_mcp_servers(raw_value) == {}


def test_provider_fixture_mcp_failure_returns_clean_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FixtureFailure(Exception):
        pass

    monkeypatch.setattr(
        provider_cli_fixture,
        "call_fixture_tool",
        lambda _config, *, token: (_ for _ in ()).throw(
            FixtureFailure(f"failed token {token}")
        ),
    )

    result = provider_cli_fixture._execute_fixture_request(
        provider_name="fixture",
        prompt="use_mcp: secret-token",
        model=None,
        options={"mcp_servers": {"fixture": {}}},
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "fixture MCP call failed: failed token secret-token\n"


def test_provider_fixture_uses_first_usable_mcp_server_in_declaration_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_usable = {"command": "first"}
    second_usable = {"command": "second"}
    calls: list[tuple[dict[str, str], str]] = []

    def fake_call_fixture_tool(config: dict[str, str], *, token: str) -> str:
        calls.append((config, token))
        return "mcp-result"

    monkeypatch.setattr(
        provider_cli_fixture,
        "call_fixture_tool",
        fake_call_fixture_tool,
    )

    result = provider_cli_fixture._execute_fixture_request(
        provider_name="fixture",
        prompt="use_mcp: requested-token",
        model=None,
        options={
            "mcp_servers": {
                "unusable": ["not", "a", "mapping"],
                "first": first_usable,
                "second": second_usable,
            }
        },
    )

    assert calls == [(first_usable, "requested-token")]
    assert isinstance(result, str)
    assert json.loads(result)["mcp_result"] == "mcp-result"


def test_provider_fixture_mcp_fatal_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FixtureFatal(BaseException):
        pass

    failure = FixtureFatal("fatal fixture failure")
    monkeypatch.setattr(
        provider_cli_fixture,
        "call_fixture_tool",
        lambda _config, *, token: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(FixtureFatal) as exc_info:
        provider_cli_fixture._execute_fixture_request(
            provider_name="fixture",
            prompt="use_mcp: token",
            model=None,
            options={"mcp_servers": {"fixture": {}}},
        )

    assert exc_info.value is failure
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
