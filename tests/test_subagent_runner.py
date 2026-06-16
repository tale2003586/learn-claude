import unittest

from agents.subagent.runner import TaskSubagentRunner
from models.provider import LLMResponse
from runtime.context import ContextBundle
from runtime.pipeline import Pipeline
from sessions import Session
from tools.executor import ToolExecutor
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


class TinyContextBuilder:
    def build(self, *, session, profile, **kwargs):
        return ContextBundle(messages=[
            {"role": "system", "content": profile.system_prompt},
            *session.messages,
        ])


class FinalAnswerProvider:
    def __init__(self, answer="subagent summary") -> None:
        self.answer = answer
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(
            content=self.answer,
            raw_message={"role": "assistant", "content": self.answer},
        )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in [
        "bash",
        "list_files",
        "read_file",
        "write_file",
        "edit_file",
        "git_status",
        "git_diff",
        "git_log",
        "git_branch",
        "git_add",
        "git_commit",
        "background_run",
        "check_background",
        "memorize",
        "recall_memory",
        "storage_list_files",
        "storage_read_file",
        "task",
        "parallel_tasks",
        "spawn_teammate",
        "tool_search",
    ]:
        registry.register(
            function_tool(name, f"{name} tool", {}, []),
            lambda **kwargs: "ok",
            enabled_modes={"coding"},
        )
    return registry


def _pipeline(provider=None) -> Pipeline:
    return Pipeline(
        tools=_registry(),
        provider=provider or FinalAnswerProvider(),
        model="fake-model",
        tool_executor=ToolExecutor([]),
        context_builder=TinyContextBuilder(),
        max_reasoning_steps=3,
    )


class SubagentRunnerTests(unittest.TestCase):
    def test_unknown_agent_type_returns_structured_error(self) -> None:
        runner = TaskSubagentRunner(base_pipeline=_pipeline())

        result = runner.run(prompt="inspect", agent_type="researcher")

        self.assertFalse(result.success)
        self.assertEqual("researcher", result.agent_type)
        self.assertIn("Unknown agent_type", result.error)

    def test_tool_whitelists_prevent_recursion_and_limit_write_tools(self) -> None:
        runner = TaskSubagentRunner(base_pipeline=_pipeline())

        explore = runner._filtered_tools("explore")._tools
        plan = runner._filtered_tools("plan")._tools
        code = runner._filtered_tools("code")._tools

        self.assertIn("read_file", explore)
        self.assertNotIn("write_file", explore)
        self.assertNotIn("edit_file", explore)
        self.assertNotIn("write_file", plan)
        self.assertNotIn("edit_file", plan)
        self.assertIn("write_file", code)
        self.assertIn("edit_file", code)
        for tools in (explore, plan, code):
            self.assertNotIn("task", tools)
            self.assertNotIn("parallel_tasks", tools)
            self.assertNotIn("spawn_teammate", tools)

    def test_run_uses_isolated_session_and_returns_summary(self) -> None:
        provider = FinalAnswerProvider("done from subagent")
        runner = TaskSubagentRunner(base_pipeline=_pipeline(provider))
        parent = Session(
            id="parent",
            metadata={
                "workspace_root": "/tmp/work",
                "workspace_display_name": "work",
                "user_id": "u1",
            },
        )

        result = runner.run(
            prompt="List files",
            description="explore files",
            agent_type="explore",
            parent_session=parent,
        )

        self.assertTrue(result.success)
        self.assertEqual("done from subagent", result.summary)
        self.assertEqual(1, len(provider.calls))
        messages = provider.calls[0]["messages"]
        self.assertTrue(any("List files" in message["content"] for message in messages))
        self.assertTrue(any(
            tool["function"]["name"] == "read_file"
            for tool in provider.calls[0]["tools"]
        ))


if __name__ == "__main__":
    unittest.main()
