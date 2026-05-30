from dataclasses import dataclass
from typing import Callable, Any

ALWAYS_ON_TOOLS = {
    "recall_memory",
    "memorize",
    "tool_search",
}

PRELOADED_TOOLS_BY_MODE = {
    "bot": {
        "load_skill",
    },
    "coding": {
        "read_file",
        "load_skill",
        "task_create",
        "task_update",
        "task_list",
        "task_get",
        "claim_task",
        "check_background",
        "read_inbox",
        "compact",
    },
}

DEFERRED_TOOLS = {
    "bash",
    "write_file",
    "edit_file",
    "background_run",
    "spawn_teammate",
    "list_teammates",
    "broadcast",
    "shutdown_request",
    "shutdown_status",
    "plan_approval",
    "task",
    "claim_task",
}

UNLOCKED_TOOLS_KEY = "unlocked_tools"
SESSION_SCOPED_TOOLS = {
    "memorize",
    "recall_memory",
}


@dataclass
class ToolSpec:
    name: str
    schema: dict
    handler: Callable[..., str]
    risk: str = "normal"
    enabled_modes: set[str] | None = None
    source: str = "local"
    always_on: bool = False

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
        always_on: bool = False,
    ) -> None:
        name = schema["function"]["name"]
        self._tools[name] = ToolSpec(
            name=name,
            schema=schema,
            handler=handler,
            risk=risk,
            enabled_modes=enabled_modes,
            source=source,
            always_on=always_on,
        )

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def schemas_for_mode(self, mode: str = "coding") -> list[dict]:
        return [
            tool.schema
            for tool in self._tools.values()
            if tool.enabled_for(mode)
        ]

    def schemas_for_turn(self, session, mode: str = "coding") -> list[dict]:
        visible_names = self.visible_names_for_turn(session, mode)
        return [
            tool.schema
            for name, tool in self._tools.items()
            if name in visible_names
        ]

    def visible_names_for_turn(self, session, mode: str = "coding") -> set[str]:
        allowed = {
            name
            for name, tool in self._tools.items()
            if tool.enabled_for(mode)
        }
        unlocked = set((session.metadata or {}).get(UNLOCKED_TOOLS_KEY, []))
        visible = (
            ALWAYS_ON_TOOLS
            | {name for name, tool in self._tools.items() if tool.always_on}
            | PRELOADED_TOOLS_BY_MODE.get(mode, set())
            | unlocked
        )
        return visible & allowed

    def reset_turn_unlocks(self, session) -> None:
        session.metadata[UNLOCKED_TOOLS_KEY] = []

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        session=None,
        mode: str = "coding",
    ) -> str:
        if name == "tool_search":
            return self._tool_search(args.get("query", ""), session=session, mode=mode)

        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        if session is not None and name not in self.visible_names_for_turn(session, mode):
            return (
                f"Tool '{name}' is not visible in this turn. "
                f"Call tool_search with query='select:{name}' first."
            )

        try:
            handler_args = dict(args)
            if name in SESSION_SCOPED_TOOLS:
                handler_args["_session"] = session
            return tool.handler(**handler_args)
        except Exception as e:
            return f"Error: {e}"

    def _tool_search(self, query: str, *, session=None, mode: str = "coding") -> str:
        query = (query or "").strip()
        allowed = {
            name
            for name, tool in self._tools.items()
            if tool.enabled_for(mode)
        }

        if query.lower().startswith("select:"):
            name = query.split(":", 1)[1].strip()
            if name not in self._tools:
                return f"Unknown tool: {name}"
            if name not in allowed:
                return f"Tool '{name}' is not allowed in {mode} mode."
            if session is None:
                return "Cannot unlock tool without a session."
            unlocked = list(session.metadata.get(UNLOCKED_TOOLS_KEY, []))
            if name not in unlocked:
                unlocked.append(name)
            session.metadata[UNLOCKED_TOOLS_KEY] = unlocked
            return (
                f"Unlocked tool for this turn: {name}. "
                "You may call it in the next reasoning step."
            )

        visible = self.visible_names_for_turn(session, mode) if session is not None else set()
        matches = []
        lowered_query = query.lower()
        for name, tool in self._tools.items():
            if name not in allowed:
                continue
            if name in visible:
                continue
            description = tool.schema["function"].get("description", "")
            haystack = f"{name} {description}".lower()
            if not lowered_query or lowered_query in haystack:
                matches.append((name, description))

        if not matches:
            return "No matching deferred tools are available in this mode."

        lines = ["Deferred tools available. Unlock one with select:<tool_name>:"]
        for name, description in matches[:12]:
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)


from .schema import LEAD_TOOLS
from .handlers import make_lead_handlers
from coding_runtime.teammate import TEAM


def build_lead_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    handlers = make_lead_handlers(TEAM)

    for schema in LEAD_TOOLS:
        name = schema["function"]["name"]
        handler = handlers.get(name)
        if handler is None and name != "tool_search":
            continue

        registry.register(
            schema,
            handler or (lambda **kw: "tool_search is handled by ToolRegistry."),
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
    if name == "tool_search":
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
    }

    enabled = set()
    if name in coding_tools:
        enabled.add("coding")
    if name in bot_tools:
        enabled.add("bot")
    if name in {"memorize", "recall_memory", "tool_search"}:
        enabled.update({"bot", "coding"})
    return enabled
