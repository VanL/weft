"""llm-backed agent runtime adapter.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Sequence
from typing import Any

import llm

from weft.core.agent_runtime import (
    AgentExecutionResult,
    AgentRuntimeSession,
    NormalizedAgentMessage,
    NormalizedAgentWorkItem,
)
from weft.core.agent_tools import ResolvedAgentTool, resolve_agent_tools
from weft.core.taskspec import AgentSection


class LLMBackend:
    """Thin adapter around the llm Python API (Spec: [AR-7])."""

    def execute(
        self,
        *,
        agent: AgentSection,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
    ) -> AgentExecutionResult:
        model = self._resolve_model(agent)
        conversation = model.conversation(chain_limit=agent.max_turns)
        return self._execute_with_conversation(
            agent=agent,
            conversation=conversation,
            work_item=work_item,
            tools=tools,
            tid=tid,
        )

    def start_session(
        self,
        *,
        agent: AgentSection,
        tid: str | None,
    ) -> AgentRuntimeSession:
        model = self._resolve_model(agent)
        conversation = model.conversation(chain_limit=agent.max_turns)
        return LLMBackendSession(
            agent=agent,
            conversation=conversation,
            tid=tid,
        )

    def _resolve_model(self, agent: AgentSection) -> Any:
        self._validate_agent(agent)
        self._register_plugin_modules(agent.runtime_config.get("plugin_modules"))
        if not agent.model:
            raise ValueError("llm backend requires spec.agent.model")
        return llm.get_model(agent.model)

    @staticmethod
    def _validate_agent(agent: AgentSection) -> None:
        if agent.output_mode not in {"text", "json", "messages"}:
            raise ValueError(f"Unsupported llm output_mode in MVP: {agent.output_mode}")

    @staticmethod
    def _register_plugin_modules(plugin_modules: Any) -> None:
        if plugin_modules is None:
            return
        if isinstance(plugin_modules, str) or not isinstance(plugin_modules, Sequence):
            raise TypeError("llm runtime_config.plugin_modules must be a list")
        for module_name in plugin_modules:
            if not isinstance(module_name, str) or not module_name:
                raise TypeError("llm runtime_config.plugin_modules must be a list")
            module = importlib.import_module(module_name)
            if module not in llm.plugins.pm.get_plugins():
                llm.plugins.pm.register(module, name=module.__name__)

    @staticmethod
    def _resolve_output_schema(schema: dict[str, Any] | str | None) -> Any:
        if schema is None:
            return None
        if isinstance(schema, dict):
            return schema
        if isinstance(schema, str):
            module_name, attr_name = schema.rsplit(":", 1)
            module = importlib.import_module(module_name)
            return getattr(module, attr_name)
        raise TypeError("Unsupported output_schema type")

    @staticmethod
    def _to_llm_tool(tool: ResolvedAgentTool) -> llm.Tool:
        return llm.Tool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            implementation=tool.implementation,
        )

    def _execute_with_conversation(
        self,
        *,
        agent: AgentSection,
        conversation: Any,
        work_item: NormalizedAgentWorkItem,
        tools: Sequence[ResolvedAgentTool],
        tid: str | None,
    ) -> AgentExecutionResult:
        del tid
        model = conversation.model
        if tools and not model.supports_tools:
            raise ValueError(f"Model does not support tools: {agent.model}")

        schema = self._resolve_output_schema(agent.output_schema)
        if schema is not None and not model.supports_schema:
            raise ValueError(f"Model does not support schema output: {agent.model}")

        tool_trace: list[dict[str, Any]] = []
        chain = conversation.chain(
            prompt=self._content_to_prompt(work_item.content),
            system=work_item.instructions,
            stream=False,
            schema=schema if agent.output_mode == "json" else None,
            tools=[self._to_llm_tool(tool) for tool in tools],
            chain_limit=agent.max_turns,
            options=dict(agent.options),
            after_call=lambda _tool, tool_call, tool_result: tool_trace.append(
                {
                    "name": tool_call.name,
                    "status": (
                        "failed" if tool_result.exception is not None else "completed"
                    ),
                }
            ),
        )
        responses = list(chain.responses())
        if not responses:
            raise RuntimeError("llm backend returned no responses")
        final_response = responses[-1]

        return AgentExecutionResult(
            runtime=agent.runtime,
            model=agent.model,
            output_mode=agent.output_mode,
            outputs=self._extract_outputs(
                responses,
                output_mode=agent.output_mode,
            ),
            metadata=dict(work_item.metadata),
            usage=self._extract_usage(final_response),
            tool_trace=tuple(tool_trace),
            artifacts=(),
        )

    @staticmethod
    def _content_to_prompt(
        content: str | tuple[str | NormalizedAgentMessage, ...],
    ) -> str:
        if isinstance(content, str):
            return content

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item.content, str):
                content_text = item.content
            else:
                content_text = json.dumps(item.content, separators=(",", ":"))
            parts.append(f"{item.role}: {content_text}")
        return "\n\n".join(parts)

    def _extract_outputs(
        self,
        responses: Sequence[Any],
        *,
        output_mode: str,
    ) -> tuple[Any, ...]:
        if output_mode == "text":
            return (responses[-1].text(),)
        if output_mode == "json":
            return (self._response_payload(responses[-1]),)
        if output_mode == "messages":
            return tuple(
                {
                    "role": "assistant",
                    "content": self._response_payload(response),
                }
                for response in responses
            )
        raise ValueError(f"Unsupported llm output_mode in MVP: {output_mode}")

    @staticmethod
    def _response_payload(response: Any) -> Any:
        payload = response.json()
        if payload is not None:
            return payload
        text = response.text()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any] | None:
        usage = response.usage()
        payload: dict[str, Any] = {}
        if usage.input is not None:
            payload["input_tokens"] = usage.input
        if usage.output is not None:
            payload["output_tokens"] = usage.output
        if usage.details is not None:
            payload["details"] = usage.details
        return payload or None


class LLMBackendSession:
    """Persistent llm conversation session for `conversation_scope=per_task`."""

    def __init__(
        self,
        *,
        agent: AgentSection,
        conversation: Any,
        tid: str | None,
    ) -> None:
        self._agent = agent
        self._conversation = conversation
        self._tid = tid
        self._backend = LLMBackend()

    def execute(self, work_item: NormalizedAgentWorkItem) -> AgentExecutionResult:
        tools = resolve_agent_tools(
            self._agent.tools,
            allow=work_item.tool_allow,
            deny=work_item.tool_deny,
        )
        return self._backend._execute_with_conversation(
            agent=self._agent,
            conversation=self._conversation,
            work_item=work_item,
            tools=tools,
            tid=self._tid,
        )

    def close(self) -> None:
        """Release session resources.

        `llm.Conversation` does not currently expose an explicit close hook.
        """
