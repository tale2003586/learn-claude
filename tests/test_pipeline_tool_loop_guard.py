import unittest
from types import SimpleNamespace

from runtime.pipeline import Pipeline
from models.provider import LLMResponse, ToolCall
from sessions.session import Session
from tools.executor import ToolExecutor
from tools.hooks import ToolLoopGuardHook
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


class ContextBuilder:
    def build(self, **kwargs):
        return SimpleNamespace(messages=kwargs["session"].messages)


class RepeatingProvider:
    def __init__(self, name: str, arguments=None) -> None:
        self.name = name
        self.arguments = arguments or {}
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        arguments = (
            self.arguments(self.calls)
            if callable(self.arguments)
            else self.arguments
        )
        return _tool_response(self.calls, self.name, arguments)


class ScriptedProvider:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def chat(self, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _tool_response(index: int, name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(
            id=f"call-{index}",
            name=name,
            arguments=arguments,
        )],
        raw_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call-{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": "{}",
                },
            }],
        },
    )


def _final_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        raw_message={
            "role": "assistant",
            "content": content,
        },
    )


def _registry_with_tool(
    name: str,
    *,
    enabled_modes: set[str],
    always_on: bool = False,
    handler=None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        function_tool(name, f"{name} test tool", {}, []),
        handler or (lambda **kwargs: "ok"),
        enabled_modes=enabled_modes,
        always_on=always_on,
    )
    return registry


def _pipeline(registry, provider, *, hooks=None, max_reasoning_steps=24) -> Pipeline:
    return Pipeline(
        tools=registry,
        provider=provider,
        model="test-model",
        tool_executor=ToolExecutor(hooks or []),
        context_builder=ContextBuilder(),
        max_reasoning_steps=max_reasoning_steps,
    )


class PipelineToolLoopGuardTests(unittest.TestCase):
    def test_repeated_invisible_tool_request_stops_turn(self) -> None:
        registry = _registry_with_tool("bash", enabled_modes={"coding"})
        provider = RepeatingProvider("bash", {"command": "pwd"})
        pipeline = _pipeline(registry, provider)
        session = Session(id="task:test", current_mode="coding")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="coding"))

        self.assertEqual(2, provider.calls)
        self.assertIn("重复请求当前不可用的工具 `bash`", reply)
        self.assertEqual("agent_loop_guard", session.messages[-1]["metadata"]["kind"])
        self.assertEqual("unavailable_tool_loop", session.messages[-1]["metadata"]["reason"])
        tool_outputs = [
            message["content"]
            for message in session.messages
            if message["role"] == "tool"
        ]
        self.assertEqual(2, len(tool_outputs))
        self.assertTrue(all("not visible in this turn" in output for output in tool_outputs))

    def test_tool_search_unlock_then_deferred_tool_call_still_completes(self) -> None:
        registry = _registry_with_tool("bash", enabled_modes={"coding"})
        registry.register(
            function_tool("tool_search", "search tools", {}, []),
            lambda **kwargs: "registry handles this",
            enabled_modes={"coding"},
        )
        bash_calls = []
        registry._tools["bash"].handler = lambda **kwargs: bash_calls.append(kwargs) or "done"
        provider = ScriptedProvider([
            _tool_response(1, "tool_search", {"query": "select:bash"}),
            _tool_response(2, "bash", {"command": "pwd"}),
            _final_response("completed"),
        ])
        pipeline = _pipeline(registry, provider)
        session = Session(id="task:test", current_mode="coding")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="coding"))

        self.assertEqual("completed", reply)
        self.assertEqual(3, provider.calls)
        self.assertEqual("pwd", bash_calls[0]["command"])
        self.assertIs(session, bash_calls[0]["_session"])
        self.assertEqual(["bash"], session.metadata["unlocked_tools"])

    def test_tool_loop_hook_denial_stops_turn(self) -> None:
        registry = _registry_with_tool(
            "echo",
            enabled_modes={"bot"},
            always_on=True,
        )
        provider = RepeatingProvider("echo", {"text": "same"})
        pipeline = _pipeline(
            registry,
            provider,
            hooks=[ToolLoopGuardHook(repeat_limit=3)],
        )
        session = Session(id="web:test", current_mode="bot")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="bot"))

        self.assertEqual(3, provider.calls)
        self.assertIn("重复调用同一工具", reply)
        self.assertEqual("repeated_tool_call", session.messages[-1]["metadata"]["reason"])

    def test_reasoning_step_limit_stops_argument_churn(self) -> None:
        registry = _registry_with_tool(
            "echo",
            enabled_modes={"bot"},
            always_on=True,
        )
        provider = RepeatingProvider("echo", lambda index: {"index": index})
        pipeline = _pipeline(registry, provider, max_reasoning_steps=3)
        session = Session(id="web:test", current_mode="bot")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="bot"))

        self.assertEqual(3, provider.calls)
        self.assertIn("工具推理步骤超过上限 (3)", reply)
        self.assertEqual("reasoning_step_limit", session.messages[-1]["metadata"]["reason"])

    def test_tool_loop_history_resets_between_user_turns(self) -> None:
        registry = _registry_with_tool(
            "echo",
            enabled_modes={"bot"},
            always_on=True,
        )
        provider = ScriptedProvider([
            _tool_response(1, "echo", {"text": "same"}),
            _final_response("first completed"),
            _tool_response(2, "echo", {"text": "same"}),
            _final_response("second completed"),
        ])
        pipeline = _pipeline(
            registry,
            provider,
            hooks=[ToolLoopGuardHook(repeat_limit=2)],
        )
        session = Session(id="web:test", current_mode="bot")

        first = pipeline.run(session, SimpleNamespace(tool_mode="bot"))
        second = pipeline.run(session, SimpleNamespace(tool_mode="bot"))

        self.assertEqual("first completed", first)
        self.assertEqual("second completed", second)
        self.assertEqual(4, provider.calls)

    def test_registry_distinguishes_hidden_and_forbidden_tools(self) -> None:
        registry = _registry_with_tool("bash", enabled_modes={"coding"})
        session = Session(id="web:test", current_mode="bot")

        hidden = registry.execution_error_for_turn("bash", session=session, mode="coding")
        forbidden = registry.execution_error_for_turn("bash", session=session, mode="bot")
        unknown = registry.execution_error_for_turn("imaginary", session=session, mode="bot")

        self.assertIn("not visible in this turn", hidden)
        self.assertEqual("Tool 'bash' is not allowed in bot mode.", forbidden)
        self.assertEqual("Unknown tool: imaginary", unknown)


if __name__ == "__main__":
    unittest.main()
