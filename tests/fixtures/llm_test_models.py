"""Deterministic llm models used for agent runtime tests."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator, Mapping
from typing import Any

import llm
from llm.models import Model, Prompt, Response, ToolCall
from pydantic import BaseModel

PLUGIN_NAME = "weft-test-llm-models"
TEST_MODEL_ID = "weft-test-agent-model"


class DeterministicAgentModel(Model):
    """Small deterministic model for llm backend integration tests."""

    model_id = TEST_MODEL_ID
    supports_schema = True
    supports_tools = True

    class Options(BaseModel):
        temperature: float | None = None

    def execute(
        self,
        prompt: Prompt,
        stream: bool,
        response: Response,
        conversation: Any,
    ) -> Iterator[str]:
        del stream
        response.set_resolved_model(self.model_id)
        response.set_usage(input=11, output=7)
        history = [
            prior_response.prompt.prompt
            for prior_response in getattr(conversation, "responses", [])
            if getattr(prior_response, "prompt", None) is not None
        ]

        if prompt.tool_results:
            tool_output = prompt.tool_results[0].output
            if tool_output == "__loop__" and prompt.tools:
                response.add_tool_call(
                    ToolCall(
                        name=prompt.tools[0].name,
                        arguments={"payload": "__loop__"},
                        tool_call_id="loop-call",
                    )
                )
                return
            if prompt.schema is not None:
                payload = {"summary": tool_output}
                response.response_json = payload
                yield json.dumps(payload)
                return
            yield tool_output
            return

        if prompt.schema is not None:
            payload = {"summary": prompt.prompt, "status": "ok"}
            response.response_json = payload
            yield json.dumps(payload)
            return

        task = prompt.prompt
        if task == "__history__":
            yield "history:" + "|".join(history)
            return

        if task.startswith("inspect_json:"):
            payload = {
                "task": task.removeprefix("inspect_json:").strip(),
                "system": prompt.system,
                "temperature": getattr(prompt.options, "temperature", None),
                "tools": [tool.name for tool in (prompt.tools or [])],
                "history": history,
            }
            response.response_json = payload
            yield json.dumps(payload)
            return

        if task.startswith("inspect_text:"):
            yield json.dumps(
                {
                    "task": task.removeprefix("inspect_text:").strip(),
                    "system": prompt.system,
                    "temperature": getattr(prompt.options, "temperature", None),
                    "tools": [tool.name for tool in (prompt.tools or [])],
                    "history": history,
                },
                sort_keys=True,
            )
            return

        if task.startswith("json:"):
            payload = {"task": task.removeprefix("json:").strip()}
            response.response_json = payload
            yield json.dumps(payload)
            return

        if prompt.tools:
            response.add_tool_call(
                ToolCall(
                    name=prompt.tools[0].name,
                    arguments=_tool_arguments_from_task(task),
                    tool_call_id="tool-call-1",
                )
            )
            return

        yield f"text:{task}"


@llm.hookimpl
def register_models(register) -> None:  # noqa: ANN001
    """Register deterministic test models through llm's real hook surface."""
    register(DeterministicAgentModel())


def register_test_model_plugin() -> None:
    """Register the test plugin with llm's live plugin manager."""
    module = importlib.import_module(__name__)
    if module not in llm.plugins.pm.get_plugins():
        llm.plugins.pm.register(module, name=PLUGIN_NAME)


def unregister_test_model_plugin() -> None:
    """Remove the test plugin from llm's live plugin manager."""
    llm.plugins.pm.unregister(name=PLUGIN_NAME)


def _tool_arguments_from_task(task: str) -> dict[str, Any]:
    if task.startswith("tool_json:"):
        payload = json.loads(task.removeprefix("tool_json:").strip())
        if not isinstance(payload, Mapping):
            raise TypeError("tool_json payload must decode to an object")
        return dict(payload)
    if task.startswith("tool_text:"):
        return {"payload": task.removeprefix("tool_text:").strip()}
    return {"payload": task}
