import unittest

from plugins.scheduler.planning import (
    LLMTaskPlanningClient,
    ScheduledTaskPlanner,
    ToolCapabilityAuditor,
    capability_allows,
    merge_approved_capabilities,
)
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    risk: str,
    enabled_modes: set[str] | None = None,
) -> None:
    registry.register(
        function_tool(name, f"{name} description", {}, []),
        lambda **kwargs: "ok",
        risk=risk,
        enabled_modes=enabled_modes,
        source=f"test:{name}",
    )


class ToolRegistryCatalogTests(unittest.TestCase):
    def test_catalog_exposes_metadata_without_handlers(self) -> None:
        registry = ToolRegistry()
        _register(registry, "web_search", risk="low", enabled_modes={"bot"})
        _register(registry, "bash", risk="high", enabled_modes={"coding"})

        bot_catalog = registry.catalog(mode="bot")

        self.assertEqual(["web_search"], [item["name"] for item in bot_catalog])
        self.assertEqual("low", bot_catalog[0]["risk"])
        self.assertEqual(["bot"], bot_catalog[0]["enabled_modes"])
        self.assertNotIn("handler", bot_catalog[0])


class ToolCapabilityAuditorTests(unittest.TestCase):
    def test_audit_classifies_low_high_forbidden_and_unknown_tools(self) -> None:
        registry = ToolRegistry()
        _register(registry, "web_search", risk="low")
        _register(registry, "bash", risk="high")
        _register(registry, "schedule_create", risk="low")
        auditor = ToolCapabilityAuditor(registry)

        audit = auditor.audit([
            "web_search",
            "bash",
            "schedule_create",
            "invented_tool",
            "web_search",
        ])

        self.assertEqual("blocked", audit.approval_status)
        self.assertEqual(["web_search", "bash", "schedule_create", "invented_tool"], audit.requested_tools)
        self.assertEqual(["web_search"], [item.tool for item in audit.auto_approved])
        self.assertEqual(["bash"], [item.tool for item in audit.requires_approval])
        self.assertEqual(["schedule_create"], [item.tool for item in audit.forbidden])
        self.assertEqual(["invented_tool"], [item.tool for item in audit.unknown])

    def test_planning_catalog_hides_forbidden_scheduler_tools(self) -> None:
        registry = ToolRegistry()
        _register(registry, "web_search", risk="low")
        _register(registry, "schedule_delete", risk="low")
        _register(registry, "spawn_teammate", risk="normal")
        auditor = ToolCapabilityAuditor(registry)

        names = [item["name"] for item in auditor.planning_catalog()]

        self.assertEqual(["web_search"], names)

    def test_approval_merge_requires_scope_for_high_risk_tools(self) -> None:
        registry = ToolRegistry()
        _register(registry, "web_search", risk="low")
        _register(registry, "bash", risk="high")
        audit = ToolCapabilityAuditor(registry).audit(["web_search", "bash"])

        with self.assertRaisesRegex(ValueError, "explicit commands"):
            merge_approved_capabilities(audit, [{"tool": "bash", "scope": {}}])

        capabilities, status = merge_approved_capabilities(audit, [{
            "tool": "bash",
            "scope": {"commands": ["python scripts/report.py"]},
        }])

        self.assertEqual("active", status)
        self.assertEqual(["bash", "web_search"], [
            item["tool"] for item in capabilities
        ])
        bash_capability = capabilities[0]
        self.assertTrue(capability_allows(
            bash_capability,
            {"command": "python scripts/report.py"},
        ))
        self.assertFalse(capability_allows(
            bash_capability,
            {"command": "python scripts/other.py"},
        ))


class ScheduledTaskPlannerTests(unittest.TestCase):
    def test_planner_normalizes_limits_and_runs_deterministic_audit(self) -> None:
        class PlanningClient:
            def __init__(self) -> None:
                self.calls = []

            def plan(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "summary": "Daily AI digest",
                    "requested_tools": ["web_search", "read_file", "web_search"],
                    "limits": {
                        "max_reasoning_steps": 999,
                        "max_tool_calls": "8",
                        "timeout_seconds": 1,
                    },
                    "rationale": "Search current material and inspect local notes.",
                }

        registry = ToolRegistry()
        _register(registry, "web_search", risk="low")
        _register(registry, "read_file", risk="normal")
        client = PlanningClient()
        draft = ScheduledTaskPlanner(planning_client=client).create_draft(
            task_prompt="Generate a daily AI report.",
            auditor=ToolCapabilityAuditor(registry),
        )

        self.assertEqual(["web_search", "read_file"], draft.plan.requested_tools)
        self.assertEqual({
            "max_reasoning_steps": 30,
            "max_tool_calls": 8,
            "timeout_seconds": 30,
        }, draft.plan.limits)
        self.assertEqual("awaiting_approval", draft.audit.approval_status)
        self.assertEqual(["web_search"], [item.tool for item in draft.audit.auto_approved])
        self.assertEqual(["read_file"], [item.tool for item in draft.audit.requires_approval])
        self.assertEqual(["read_file", "web_search"], [
            item["name"] for item in client.calls[0]["tool_catalog"]
        ])

    def test_planner_rejects_non_list_tool_output(self) -> None:
        class PlanningClient:
            def plan(self, **kwargs):
                return {"requested_tools": "web_search"}

        with self.assertRaisesRegex(ValueError, "requested_tools"):
            ScheduledTaskPlanner(planning_client=PlanningClient()).create_draft(
                task_prompt="Generate a daily report.",
                auditor=ToolCapabilityAuditor(ToolRegistry()),
            )


class LLMTaskPlanningClientTests(unittest.TestCase):
    def test_llm_client_extracts_json_from_markdown_fence(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                return type("Response", (), {
                    "content": (
                        "```json\n"
                        '{"summary":"Digest","requested_tools":["web_search"],'
                        '"limits":{},"rationale":"Need current sources."}\n'
                        "```"
                    ),
                })()

        provider = Provider()
        result = LLMTaskPlanningClient(
            provider=provider,
            model="planner-model",
        ).plan(
            task_prompt="Create a digest.",
            tool_catalog=[{"name": "web_search", "risk": "low"}],
        )

        self.assertEqual(["web_search"], result["requested_tools"])
        self.assertEqual("planner-model", provider.calls[0]["model"])
        self.assertEqual([], provider.calls[0]["tools"])


if __name__ == "__main__":
    unittest.main()
