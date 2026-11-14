"""Tests for the backend-neutral agent runtime surface."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import llm
import pytest

from weft.core.agents.backends.llm import LLMBackend
from weft.core.agents.runtime import (
    AgentExecutionResult,
    AgentRuntimeAdapter,
    NormalizedAgentMessage,
    NormalizedAgentWorkItem,
    clear_agent_runtime_registry,
    execute_agent_target,
    get_agent_runtime,
    normalize_agent_work_item,
    register_agent_runtime,
    start_agent_runtime_session,
)
from weft.core.agents.tools import ResolvedAgentTool, resolve_agent_tools
from weft.core.taskspec import AgentSection, AgentToolSection


class EchoRuntime:
    """Small real runtime used to verify registry and execution plumbing."""

    def execute(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
        bundle_root: str | None = None,
        spec_context: str | None = None,
    ) -> AgentExecutionResult:
        del tid, bundle_root, spec_context
        return AgentExecutionResult(
            runtime=agent.runtime,
            model=agent.model,
            output_mode=agent.output_mode,
            outputs=(
                {
                    "content": work_item.content,
                    "metadata": work_item.metadata,
                    "tools": [tool.name for tool in tools],
                },
            ),
            metadata=work_item.metadata,
        )


class EchoRuntimeWithoutBundleRoot:
    """Adapter using the supported execute signature without bundle provenance."""

    def execute(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
    ) -> AgentExecutionResult:
        return EchoRuntime().execute(
            agent=agent, work_item=work_item, tools=tools, tid=tid
        )


@pytest.fixture(autouse=True)
def clear_runtime_registry() -> None:
    clear_agent_runtime_registry()


def make_agent_section(**overrides: object) -> AgentSection:
    payload: dict[str, object] = {
        "runtime": "echo",
        "instructions": "default instructions",
    }
    payload.update(overrides)
    return AgentSection.model_validate(payload)


def test_runtime_registry_registers_and_rejects_duplicates() -> None:
    runtime = EchoRuntime()
    register_agent_runtime("echo", runtime)

    assert get_agent_runtime("echo") is runtime
    with pytest.raises(ValueError, match="already registered"):
        register_agent_runtime("echo", EchoRuntime())


def test_runtime_registry_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match="Unknown agent runtime"):
        get_agent_runtime("missing")


def test_start_session_rejects_runtime_without_persistent_capability() -> None:
    register_agent_runtime("echo", EchoRuntime())

    with pytest.raises(RuntimeError) as exc_info:
        start_agent_runtime_session(make_agent_section())
    assert type(exc_info.value) is RuntimeError
    assert "persistent sessions" in str(exc_info.value)
    assert "echo" in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_normalize_work_item_from_string() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(),
        "review this patch",
    )

    assert normalized.content == "review this patch"
    assert normalized.instructions == "default instructions"
    assert normalized.metadata == {}
    assert normalized.tool_allow is None
    assert normalized.tool_deny is None


def test_normalize_work_item_preserves_structured_messages() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(),
        {
            "messages": [
                "raw note",
                {"role": "system", "content": "Be brief"},
                {"role": "user", "content": {"path": "README.md"}},
            ]
        },
    )

    assert normalized.content == (
        "raw note",
        NormalizedAgentMessage(role="system", content="Be brief"),
        NormalizedAgentMessage(role="user", content={"path": "README.md"}),
    )


def test_normalize_work_item_requires_exactly_one_content_field() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        normalize_agent_work_item(
            make_agent_section(),
            {
                "task": "explicit task",
                "messages": [{"role": "user", "content": "ignored"}],
            },
        )


def test_normalize_work_item_renders_template_and_overrides_instructions() -> None:
    normalized = normalize_agent_work_item(
        make_agent_section(
            templates={
                "review": {
                    "instructions": "template instructions",
                    "prompt": "Patch:\n{{ patch }}",
                }
            }
        ),
        {
            "template": "review",
            "template_args": {"patch": "diff --git a b"},
        },
    )

    assert normalized.content == "Patch:\ndiff --git a b"
    assert normalized.instructions == "template instructions"


def test_normalize_work_item_rejects_unsupported_fields() -> None:
    with pytest.raises(ValueError, match="Unsupported agent work item field"):
        normalize_agent_work_item(
            make_agent_section(),
            {"task": "ok", "attachments": []},
        )


def test_normalize_work_item_rejects_extra_template_arguments() -> None:
    with pytest.raises(ValueError, match="Unexpected template argument"):
        normalize_agent_work_item(
            make_agent_section(
                templates={
                    "review": {
                        "prompt": "Patch:\n{{ patch }}",
                    }
                }
            ),
            {
                "template": "review",
                "template_args": {"patch": "diff", "extra": "nope"},
            },
        )


def test_normalize_work_item_rejects_message_without_role() -> None:
    with pytest.raises(TypeError, match="role"):
        normalize_agent_work_item(
            make_agent_section(),
            {
                "messages": [{"content": "no role"}],
            },
        )


@pytest.mark.parametrize("supports_bundle_root", [True, False])
def test_execute_agent_target_returns_internal_execution_result(
    supports_bundle_root: bool,
) -> None:
    runtime = (
        EchoRuntime()
        if supports_bundle_root
        # Runtime signature introspection accepts this narrower adapter contract [AR-5].
        else cast(AgentRuntimeAdapter, EchoRuntimeWithoutBundleRoot())
    )
    register_agent_runtime("echo", runtime)
    result = execute_agent_target(
        AgentSection(
            runtime="echo",
            model="unit-test-model",
            tools=(
                {
                    "name": "echo_payload",
                    "kind": "python",
                    "ref": "tests.tasks.sample_targets:echo_payload",
                },
            ),
            output_mode="json",
        ),
        {
            "task": "run me",
            "metadata": {"request_id": "abc-123"},
            "tool_overrides": {"allow": ["echo_payload"]},
        },
        tid="123",
    )

    assert result.runtime == "echo"
    assert result.output_mode == "json"
    assert result.outputs == (
        {
            "content": "run me",
            "metadata": {"request_id": "abc-123"},
            "tools": ["echo_payload"],
        },
    )
    assert result.aggregate_public_output() == {
        "content": "run me",
        "metadata": {"request_id": "abc-123"},
        "tools": ["echo_payload"],
    }
    assert result.metadata == {"request_id": "abc-123"}


def _write_bundle_llm_files(
    bundle_dir: Path,
    *,
    model_id: str,
    schema_field: str,
    tool_prefix: str,
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "bundle_plugin_mod.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "from collections.abc import Callable, Iterator",
                "from typing import Any",
                "import llm",
                "from llm.models import Model, Prompt, Response, ToolCall",
                "",
                "class BundleTestModel(Model):",
                f"    model_id = {model_id!r}",
                "    supports_schema = True",
                "    supports_tools = True",
                "",
                "    def execute(",
                "        self,",
                "        prompt: Prompt,",
                "        stream: bool,",
                "        response: Response,",
                "        conversation: Any,",
                "    ) -> Iterator[str]:",
                "        del stream, conversation",
                "        response.set_resolved_model(self.model_id)",
                "        if prompt.tool_results:",
                "            value = prompt.tool_results[0].output",
                "        elif prompt.tools:",
                "            response.add_tool_call(",
                "                ToolCall(",
                "                    name=prompt.tools[0].name,",
                "                    arguments={'payload': prompt.prompt},",
                "                    tool_call_id='bundle-call-1',",
                "                )",
                "            )",
                "            return",
                "        else:",
                "            value = prompt.prompt",
                "        assert prompt.schema is not None, 'expected resolved output_schema on prompt'",
                "        schema_dict = (",
                "            prompt.schema",
                "            if isinstance(prompt.schema, dict)",
                "            else prompt.schema.model_json_schema()",
                "        )",
                "        schema_fields = list(schema_dict.get('properties', {}).keys())",
                f"        assert schema_fields == [{schema_field!r}], schema_fields",
                "        payload = {schema_fields[0]: value}",
                "        response.response_json = payload",
                "        yield json.dumps(payload)",
                "",
                "@llm.hookimpl",
                "def register_models(register: Callable[[Model], None]) -> None:",
                "    register(BundleTestModel())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_dir / "bundle_schema_mod.py").write_text(
        "\n".join(
            [
                "from pydantic import BaseModel",
                "",
                "class OutputSchema(BaseModel):",
                f"    {schema_field}: str",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_dir / "bundle_tool_mod.py").write_text(
        "\n".join(
            [
                "def format_payload(payload: str) -> str:",
                '    """Format payload for bundle test."""',
                f"    return f'{tool_prefix}:{{payload}}'",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _unregister_added_llm_plugins(before_plugins: set[object]) -> None:
    for plugin in list(llm.plugins.pm.get_plugins()):
        if plugin not in before_plugins:
            llm.plugins.pm.unregister(plugin=plugin)


def test_llm_backend_resolves_bundle_plugin_and_output_schema_in_one_shot(
    tmp_path: Path,
) -> None:
    bundle_a = tmp_path / "bundle_a"
    _write_bundle_llm_files(
        bundle_a,
        model_id="weft-bundle-one-shot-model",
        schema_field="bundle_a_value",
        tool_prefix="bundle-a",
    )
    agent = AgentSection(
        runtime="llm",
        model="weft-bundle-one-shot-model",
        output_mode="json",
        output_schema="bundle_schema_mod:OutputSchema",
        runtime_config={"plugin_modules": ["bundle_plugin_mod"]},
    )
    backend = LLMBackend()
    before_plugins = set(llm.plugins.pm.get_plugins())
    try:
        backend.validate(agent=agent, preflight=True, bundle_root=str(bundle_a))
        result = backend.execute(
            agent=agent,
            work_item=normalize_agent_work_item(agent, "hello-bundle"),
            tools=(),
            tid="123",
            bundle_root=str(bundle_a),
        )
        assert result.aggregate_public_output() == {"bundle_a_value": "hello-bundle"}
    finally:
        _unregister_added_llm_plugins(before_plugins)


def test_llm_backend_session_propagates_bundle_root_for_tools_and_schema(
    tmp_path: Path,
) -> None:
    bundle_a = tmp_path / "bundle_session"
    _write_bundle_llm_files(
        bundle_a,
        model_id="weft-bundle-session-model",
        schema_field="session_value",
        tool_prefix="session-tool",
    )
    agent = AgentSection(
        runtime="llm",
        model="weft-bundle-session-model",
        conversation_scope="per_task",
        max_turns=3,
        output_mode="json",
        output_schema="bundle_schema_mod:OutputSchema",
        runtime_config={"plugin_modules": ["bundle_plugin_mod"]},
        tools=(
            AgentToolSection(
                name="format_payload",
                kind="python",
                ref="bundle_tool_mod:format_payload",
            ),
        ),
    )
    backend = LLMBackend()
    before_plugins = set(llm.plugins.pm.get_plugins())
    try:
        session = backend.start_session(
            agent=agent,
            tid="456",
            bundle_root=str(bundle_a),
        )
        try:
            result = session.execute(normalize_agent_work_item(agent, "turn-1"))
        finally:
            session.close()
        assert result.aggregate_public_output() == {
            "session_value": "session-tool:turn-1"
        }
    finally:
        _unregister_added_llm_plugins(before_plugins)


def test_llm_backend_isolates_colliding_bundle_module_names_across_bundles(
    tmp_path: Path,
) -> None:
    bundle_a = tmp_path / "bundle_iso_a"
    bundle_b = tmp_path / "bundle_iso_b"
    _write_bundle_llm_files(
        bundle_a,
        model_id="weft-bundle-iso-model-a",
        schema_field="alpha_field",
        tool_prefix="alpha-tool",
    )
    _write_bundle_llm_files(
        bundle_b,
        model_id="weft-bundle-iso-model-b",
        schema_field="beta_field",
        tool_prefix="beta-tool",
    )
    backend = LLMBackend()
    before_plugins = set(llm.plugins.pm.get_plugins())
    try:
        agent_a = AgentSection(
            runtime="llm",
            model="weft-bundle-iso-model-a",
            max_turns=3,
            output_mode="json",
            output_schema="bundle_schema_mod:OutputSchema",
            runtime_config={"plugin_modules": ["bundle_plugin_mod"]},
            tools=(
                AgentToolSection(
                    name="format_payload",
                    kind="python",
                    ref="bundle_tool_mod:format_payload",
                ),
            ),
        )
        agent_b = AgentSection(
            runtime="llm",
            model="weft-bundle-iso-model-b",
            max_turns=3,
            output_mode="json",
            output_schema="bundle_schema_mod:OutputSchema",
            runtime_config={"plugin_modules": ["bundle_plugin_mod"]},
            tools=(
                AgentToolSection(
                    name="format_payload",
                    kind="python",
                    ref="bundle_tool_mod:format_payload",
                ),
            ),
        )
        result_a = backend.execute(
            agent=agent_a,
            work_item=normalize_agent_work_item(agent_a, "check"),
            tools=resolve_agent_tools(agent_a.tools, bundle_root=str(bundle_a)),
            tid="789",
            bundle_root=str(bundle_a),
        )
        result_b = backend.execute(
            agent=agent_b,
            work_item=normalize_agent_work_item(agent_b, "check"),
            tools=resolve_agent_tools(agent_b.tools, bundle_root=str(bundle_b)),
            tid="790",
            bundle_root=str(bundle_b),
        )
        assert result_a.aggregate_public_output() == {"alpha_field": "alpha-tool:check"}
        assert result_b.aggregate_public_output() == {"beta_field": "beta-tool:check"}
    finally:
        _unregister_added_llm_plugins(before_plugins)
