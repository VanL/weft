"""Microsandbox runner option validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft_microsandbox._options import parse_options_from_payload

pytestmark = [pytest.mark.shared]


def _command_payload(**options: object) -> dict[str, object]:
    return {
        "spec": {
            "type": "command",
            "process_target": "python",
            "runner": {
                "name": "microsandbox",
                "options": {"image": "python:3.12-alpine", **options},
            },
            "env": {"TOKEN": "explicit"},
            "limits": {},
        }
    }


def _agent_payload(**options: object) -> dict[str, object]:
    return {
        "spec": {
            "type": "agent",
            "persistent": False,
            "agent": {
                "runtime": "provider_cli",
                "authority_class": "general",
                "conversation_scope": "per_message",
                "runtime_config": {"provider": "gemini"},
            },
            "runner": {
                "name": "microsandbox",
                "options": {"image": "agent:latest", **options},
            },
            "env": {},
            "limits": {},
        }
    }


def test_command_mode_derives_tool_and_defaults_to_no_network() -> None:
    options = parse_options_from_payload(_command_payload())

    assert options.mode == "tool"
    assert options.network == "none"
    assert options.workspace_mode == "none"
    assert options.env == {"TOKEN": "explicit"}


def test_agent_mode_requires_guest_executable() -> None:
    with pytest.raises(ValueError, match="requires spec.runner.options.executable"):
        parse_options_from_payload(_agent_payload())


def test_agent_mode_accepts_guest_executable_without_host_resolution() -> None:
    options = parse_options_from_payload(_agent_payload(executable="gemini"))

    assert options.mode == "agent"
    assert options.executable == "gemini"


def test_unknown_option_fails() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_options_from_payload(_command_payload(privileged=True))


def test_max_connections_zero_conflicts_with_network_allow() -> None:
    payload = _command_payload(network="allow")
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["limits"] = {"max_connections": 0}

    with pytest.raises(ValueError, match="conflicts"):
        parse_options_from_payload(payload)


def test_nonzero_max_connections_is_rejected() -> None:
    payload = _command_payload()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["limits"] = {"max_connections": 5}

    with pytest.raises(ValueError, match="max_connections only when it is 0"):
        parse_options_from_payload(payload)


def test_workspace_mount_requires_guest_cwd(tmp_path: Path) -> None:
    payload = _command_payload(workspace_mode="mount-read-only")
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["working_dir"] = str(tmp_path)

    with pytest.raises(ValueError, match="require spec.runner.options.cwd"):
        parse_options_from_payload(payload)


def test_mounts_default_to_read_only(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    options = parse_options_from_payload(
        _command_payload(
            mounts=[
                {
                    "source": str(source),
                    "target": "/input",
                }
            ]
        )
    )

    assert len(options.mounts) == 1
    assert options.mounts[0].source == str(source.resolve())
    assert options.mounts[0].target == "/input"
    assert options.mounts[0].read_only is True


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"mode": 1}, "spec.runner.options.mode must be 'tool' or 'agent'"),
        ({"network": 1}, "spec.runner.options.network must be 'none' or 'allow'"),
        (
            {"workspace_mode": 1},
            "spec.runner.options.workspace_mode must be a string",
        ),
        (
            {"mounts": 1},
            "spec.runner.options.mounts must be a list of mount objects",
        ),
        (
            {"mounts": [1]},
            "spec.runner.options.mounts[0] must be an object",
        ),
        (
            {"mounts": [{"source": ".", "target": "/workspace", "read_only": 1}]},
            "spec.runner.options.mounts[0].read_only must be a boolean",
        ),
    ],
)
def test_options_reject_wrong_value_shapes_as_value_error(
    options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_options_from_payload(_command_payload(**options))
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"spec": []}, "spec must be an object"),
        (
            {"spec": {"type": "command", "runner": []}},
            "spec.runner must be an object",
        ),
        (
            {
                "spec": {
                    "type": "command",
                    "runner": {"name": "microsandbox", "options": []},
                }
            },
            "spec.runner.options must be an object",
        ),
        (
            {
                "spec": {
                    "type": "agent",
                    "runner": {
                        "name": "microsandbox",
                        "options": {"image": "agent:latest"},
                    },
                    "agent": [],
                }
            },
            "spec.agent must be an object",
        ),
        (
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "microsandbox",
                        "options": {"image": "python:3.12-alpine"},
                    },
                    "env": ["not-a-mapping"],
                }
            },
            "spec.env must be an object",
        ),
    ],
)
def test_payload_parser_rejects_non_object_sections_as_value_error(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_options_from_payload(payload)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None
