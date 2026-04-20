"""Tests for submission-time TaskSpec materialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weft.core.taskspec import ParameterizationArgumentSection, TaskSpec
from weft.core.taskspec.parameterization import (
    SpecParameterizationRequest,
    materialize_taskspec_template,
    parse_declared_parameterization_args,
)


def _create_parameterized_taskspec(
    bundle_root: Path,
    *,
    adapter_ref: str = "helper_module:materialize",
) -> TaskSpec:
    payload = {
        "name": "parameterized-task",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "parameterization": {
                "adapter_ref": adapter_ref,
                "arguments": {
                    "provider": {
                        "type": "string",
                        "default": "codex",
                        "choices": ["codex", "gemini"],
                    },
                    "model": {
                        "type": "string",
                    },
                },
            },
        },
        "metadata": {},
    }
    taskspec = TaskSpec.model_validate(
        payload,
        context={"template": True, "auto_expand": False},
    )
    taskspec.set_bundle_root(bundle_root)
    return taskspec


def test_parse_declared_parameterization_args_applies_defaults_and_preserves_unknown_tokens() -> (
    None
):
    arguments = {
        "provider": ParameterizationArgumentSection(
            type="string",
            default="codex",
            choices=("codex", "gemini"),
        ),
        "model": ParameterizationArgumentSection(type="string"),
    }

    parsed, remaining = parse_declared_parameterization_args(
        ["--prompt", "Summarize", "--provider", "gemini"],
        arguments,
    )

    assert parsed == {"provider": "gemini"}
    assert remaining == ["--prompt", "Summarize"]


def test_parse_declared_parameterization_args_rejects_choice_mismatch() -> None:
    arguments = {
        "provider": ParameterizationArgumentSection(
            type="string",
            required=True,
            choices=("codex", "gemini"),
        )
    }

    with pytest.raises(ValueError, match="must be one of"):
        parse_declared_parameterization_args(
            ["--provider", "qwen"],
            arguments,
        )


def test_materialize_taskspec_template_supports_bundle_local_adapter(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import copy",
                "",
                "def materialize(request):",
                "    payload = copy.deepcopy(dict(request.taskspec_payload))",
                "    payload['name'] = f\"example-{request.arguments['provider']}\"",
                "    payload['spec']['function_target'] = 'tests.tasks.sample_targets:echo_payload'",
                "    payload['description'] = request.arguments['provider']",
                "    return payload",
                "",
            ]
        ),
        encoding="utf-8",
    )
    taskspec = _create_parameterized_taskspec(bundle_root)

    materialized = materialize_taskspec_template(
        taskspec,
        arguments={"provider": "gemini"},
        context_root=str(tmp_path),
    )

    assert materialized.name == "example-gemini"
    assert materialized.description == "gemini"
    assert materialized.tid is None
    assert materialized.spec.type == "function"
    assert (
        materialized.spec.function_target == "tests.tasks.sample_targets:echo_payload"
    )
    assert materialized.spec.parameterization is None
    assert materialized.get_bundle_root() == taskspec.get_bundle_root()


def test_materialize_taskspec_template_rejects_spec_type_change(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def materialize(request):",
                "    return {",
                "        'name': 'wrong',",
                "        'spec': {'type': 'command', 'process_target': 'echo'},",
                "        'metadata': {},",
                "    }",
                "",
            ]
        ),
        encoding="utf-8",
    )
    taskspec = _create_parameterized_taskspec(bundle_root)

    with pytest.raises(ValueError, match="must preserve spec.type"):
        materialize_taskspec_template(
            taskspec,
            arguments={"provider": "codex"},
            context_root=str(tmp_path),
        )


def test_materialize_taskspec_template_request_payload_is_json_like_copy(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    output_path = bundle_root / "request.json"
    (bundle_root / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import json",
                "from pathlib import Path",
                "",
                "def materialize(request):",
                "    Path(request.context_root).joinpath('request.json').write_text(",
                "        json.dumps(dict(request.taskspec_payload), sort_keys=True),",
                "        encoding='utf-8',",
                "    )",
                "    return request.taskspec_payload",
                "",
            ]
        ),
        encoding="utf-8",
    )
    taskspec = _create_parameterized_taskspec(bundle_root)

    materialize_taskspec_template(
        taskspec,
        arguments={"provider": "codex"},
        context_root=str(bundle_root),
    )

    recorded = json.loads(output_path.read_text(encoding="utf-8"))
    assert recorded["name"] == "parameterized-task"
    assert (
        recorded["spec"]["parameterization"]["arguments"]["provider"]["default"]
        == "codex"
    )


def test_spec_parameterization_request_is_constructible() -> None:
    request = SpecParameterizationRequest(
        arguments={"provider": "codex"},
        context_root="/tmp",
        spec_name="example",
        taskspec_payload={"name": "example"},
    )

    assert request.arguments == {"provider": "codex"}
