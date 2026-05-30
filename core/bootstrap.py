from bus.user_bus import MessageBus
from config import MODEL, WORKDIR, client
from core.agent_loop import AgentLoop
from core.context import ContextBuilder
from core.pipeline import Pipeline
from core.provider import OpenAICompatibleProvider
from core.runtime import AppRuntime
from tools.hooks import FileWriteScopeHook, ToolLoopGuardHook, ToolTraceHook
from tools.executor import ToolExecutor
from tools.tool_registry import build_lead_tool_registry
from modes.router import ModeRouter
from sessions import SessionManager
from tasksessions import TaskSessionRunner
from memory.archive_store import MemoryArchiveStore
from memory.history_summary import HistorySummarizer
from memory.lifecycle import MemoryLifecycle
from memory.store import MemoryStore
from plugins import PluginManager
from plugins.shell_safety import ShellSafetyPlugin
from plugins.status_commands import StatusCommandsPlugin
from plugins.web_search import WebSearchPlugin
from plugins.scheduler import SchedulerPlugin


def build_runtime() -> AppRuntime:
    bus = MessageBus()
    sessions = SessionManager()
    router = ModeRouter()
    tools = build_lead_tool_registry()

    provider = OpenAICompatibleProvider(client)

    memory_store = MemoryStore()
    memory_archive_store = MemoryArchiveStore()
    context_builder = ContextBuilder(memory_store=memory_store)
    memory_lifecycle = MemoryLifecycle(
        memory_store,
        summarizer=HistorySummarizer(provider=provider, model=MODEL),
        archive_store=memory_archive_store,
    )

    plugin_manager = PluginManager(
        [
            ShellSafetyPlugin(),
            StatusCommandsPlugin(),
            WebSearchPlugin(),
            SchedulerPlugin(),
        ],
        workspace=WORKDIR,
        tool_registry=tools,
        sessions=sessions,
        memory_store=memory_store,
    )

    executor = ToolExecutor([
        FileWriteScopeHook(),
        ToolLoopGuardHook(),
        ToolTraceHook(),
        *plugin_manager.tool_hooks,
    ])

    pipeline = Pipeline(
        tools=tools,
        provider=provider,
        model=MODEL,
        max_tokens=8000,
        context_builder=context_builder,
        memory_lifecycle=memory_lifecycle,
        tool_executor=executor,
    )

    task_session_runner = TaskSessionRunner(
        sessions=sessions,
        base_pipeline=pipeline,
        global_memory=memory_store,
    )

    loop = AgentLoop(
        bus,
        sessions,
        pipeline,
        router,
        plugin_manager,
        task_session_runner,
    )

    return AppRuntime(
        bus=bus,
        loop=loop,
    )
