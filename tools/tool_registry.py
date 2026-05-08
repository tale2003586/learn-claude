from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class ToolSpec:
    name: str
    schema: dict
    handler: Callable[..., str]
    risk: str = "normal"
    enabled_modes: set[str] | None = None
    source: str = "local"

    def enabled_for(self, mode: str) -> bool:
        return self.enabled_modes is None or mode in self.enabled_modes


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        schema: dict,
        handler: Callable[..., str],
        *,
        risk: str = "normal",
        enabled_modes: set[str] | None = None,
        source: str = "local",
    ) -> None:
        name = schema["function"]["name"]
        self._tools[name] = ToolSpec(
            name=name,
            schema=schema,
            handler=handler,
            risk=risk,
            enabled_modes=enabled_modes,
            source=source,
        )

    def schemas_for_mode(self, mode: str = "coding") -> list[dict]:
        return [
            tool.schema
            for tool in self._tools.values()
            if tool.enabled_for(mode)
        ]

    def execute(self, name: str, args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"

        try:
            return tool.handler(**args)
        except Exception as e:
            return f"Error: {e}"


from .schema import LEAD_TOOLS
from .tools import make_lead_handlers
from teammate import TEAM


def build_lead_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    handlers = make_lead_handlers(TEAM)

    for schema in LEAD_TOOLS:
        name = schema["function"]["name"]
        handler = handlers.get(name)
        if handler is None:
            continue

        registry.register(
            schema,
            handler,
            risk=_risk_for_tool(name),
            enabled_modes=_modes_for_tool(name),
            source="lead",
        )

    return registry


def _risk_for_tool(name: str) -> str:
    if name in {"bash", "write_file", "edit_file", "background_run"}:
        return "high"
    if name in {"read_file", "task_list", "task_get", "check_background"}:
        return "low"
    return "normal"


def _modes_for_tool(name: str) -> set[str]:
    coding_tools = {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "load_skill",
        "task_create",
        "task_update",
        "task_list",
        "task_get",
        "claim_task",
        "background_run",
        "check_background",
        "compact",
        "spawn_teammate",
        "list_teammates",
        "broadcast",
        "send_message",
        "read_inbox",
        "shutdown_request",
        "shutdown_status",
        "plan_approval",
    }

    bot_tools = {
        "load_skill",
        "task_create",
        "task_update",
        "task_list",
        "task_get",
        "send_message",
        "read_inbox",
        "check_background",
    }

    enabled = set()
    if name in coding_tools:
        enabled.add("coding")
    if name in bot_tools:
        enabled.add("bot")
    if name in {"memorize", "recall_memory"}:
        enabled.update({"bot", "coding"})
    return enabled
