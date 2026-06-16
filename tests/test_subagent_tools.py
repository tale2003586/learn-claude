import json
import unittest
from types import SimpleNamespace

from agents.subagent.runner import TaskSubagentRunner
from runtime.pipeline import Pipeline
from tools.executor import ToolExecutor
from tools.handlers import configure_subagent_runner, make_lead_handlers
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


class ContextBuilder:
    def build(self, **kwargs):
        return SimpleNamespace(messages=kwargs["session"].messages)


class DummyProvider:
    pass


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            to_dict=lambda: {
                "agent_type": kwargs["agent_type"],
                "success": True,
                "summary": "done",
                "files_touched": [],
                "tool_count": 0,
                "error": None,
            }
        )


class FakeTeam:
    def member_names(self):
        return []

    def spawn(self, name, role, prompt):
        return f"spawned {name}"

    def list_all(self):
        return "No teammates."


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ["read_file", "edit_file", "task", "spawn_teammate", "tool_search"]:
        registry.register(
            function_tool(name, f"{name} tool", {}, []),
            lambda **kwargs: "ok",
            enabled_modes={"coding"},
        )
    return registry


def _pipeline(registry: ToolRegistry) -> Pipeline:
    return Pipeline(
        tools=registry,
        provider=DummyProvider(),
        model="test-model",
        tool_executor=ToolExecutor([]),
        context_builder=ContextBuilder(),
    )


class SubagentToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_subagent_runner(None)

    def test_subagent_tool_filter_excludes_team_tools(self) -> None:
        runner = TaskSubagentRunner(base_pipeline=_pipeline(_registry()))

        tools = runner._filtered_tools("code")

        self.assertIn("read_file", tools._tools)
        self.assertIn("edit_file", tools._tools)
        self.assertIn("tool_search", tools._tools)
        self.assertNotIn("task", tools._tools)
        self.assertNotIn("spawn_teammate", tools._tools)

    def test_task_handler_invokes_configured_runner(self) -> None:
        fake_runner = FakeRunner()
        configure_subagent_runner(fake_runner)
        handlers = make_lead_handlers(FakeTeam())

        output = handlers["task"](
            prompt="inspect auth",
            description="auth review",
            agent_type="explore",
            _session=SimpleNamespace(id="parent"),
        )
        payload = json.loads(output)

        self.assertTrue(payload["success"])
        self.assertEqual("explore", payload["agent_type"])
        self.assertEqual("inspect auth", fake_runner.calls[0]["prompt"])
        self.assertEqual("parent", fake_runner.calls[0]["parent_session"].id)


if __name__ == "__main__":
    unittest.main()
