from bus.user_bus import MessageBus
from runtime import AppRuntime
from tools.tool_registry import build_lead_tool_registry
from agent_loop import AgentLoop
from pipeline import Pipeline
from modes.router import ModeRouter
from session import SessionManager




def build_runtime() -> AppRuntime:
    bus = MessageBus()
    sessions = SessionManager()
    router = ModeRouter()
    tools = build_lead_tool_registry()
    pipeline = Pipeline(tools)
    loop = AgentLoop(bus, sessions, pipeline, router)

    return AppRuntime(
        bus=bus,
        loop=loop,
    )
