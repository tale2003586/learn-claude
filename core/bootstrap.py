from bus.user_bus import MessageBus
from coding_runtime.teammate import TEAM
from config import (
    MODEL,
    MODEL_POOL,
    REFLECTION_ENABLED,
    REFLECTION_MAX_TOKENS,
    REFLECTION_MIN_REASONING_STEPS,
    WORKDIR,
)
from core.agent_loop import AgentLoop
from core.context import ContextBuilder
from core.agent_spec import AgentSpec
from core.model_task_runner import ModelTaskRunner
from core.pipeline import Pipeline
from core.reflection import ReflectionAgent
from core.runtime import AppRuntime
from tools.hooks import FileWriteScopeHook, ToolLoopGuardHook, ToolTraceHook
from tools.executor import ToolExecutor
from tools.handlers import cleanup_expired_sandboxes
from tools.tool_registry import build_lead_tool_registry
from modes.router import ModeRouter
from modes.hybrid_classifier import HybridModeClassifier
from sessions import SessionManager
from tasksessions import TaskSessionRunner
from memory.archive_store import MemoryArchiveStore
from memory.history_summary import HistorySummarizer
from memory.lifecycle import MemoryLifecycle
from memory.store import MemoryStore
from memory.scoped_store import ScopedMemoryStore
from plugins import PluginManager
from plugins.shell_safety import ShellSafetyPlugin
from plugins.status_commands import StatusCommandsPlugin
from plugins.web_search import WebSearchPlugin
from plugins.markdown_pdf import MarkdownPdfPlugin
from plugins.scheduler import SchedulerPlugin
from plugins.scheduler.agent_runner import ScheduledAgentRunner


def build_runtime() -> AppRuntime:
    cleanup_expired_sandboxes()
    bus = MessageBus()
    sessions = SessionManager()
    tools = build_lead_tool_registry(TEAM)

    provider = MODEL_POOL.routed_provider("chat")
    router = ModeRouter(
        hybrid_classifier=HybridModeClassifier(
            provider=MODEL_POOL.routed_provider("hybrid"),
            model=MODEL_POOL.model_for("hybrid"),
        ),
    )

    memory_store = ScopedMemoryStore(WORKDIR, legacy_store=MemoryStore())
    memory_archive_store = MemoryArchiveStore()
    context_builder = ContextBuilder(memory_store=memory_store)
    model_task_runner = ModelTaskRunner(
        model_pool=MODEL_POOL,
        default_max_tokens=800,
    )
    memory_lifecycle = MemoryLifecycle(
        memory_store,
        summarizer=HistorySummarizer(
            runner=model_task_runner,
            spec=AgentSpec(
                name="history_summarizer",
                profile=None,
                model_purpose="summary",
                max_tokens=220,
            ),
        ),
        archive_store=memory_archive_store,
    )

    scheduler_plugin = SchedulerPlugin()
    plugin_manager = PluginManager(
        [
            ShellSafetyPlugin(),
            StatusCommandsPlugin(),
            WebSearchPlugin(),
            MarkdownPdfPlugin(),
            scheduler_plugin,
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
    reflection_agent = None
    if REFLECTION_ENABLED:
        reflection_agent = ReflectionAgent(
            provider=MODEL_POOL.routed_provider("reflection"),
            model=MODEL_POOL.model_for("reflection"),
            max_tokens=REFLECTION_MAX_TOKENS,
            min_reasoning_steps=REFLECTION_MIN_REASONING_STEPS,
        )

    pipeline = Pipeline(
        tools=tools,
        provider=provider,
        model=MODEL,
        model_pool=MODEL_POOL,
        max_tokens=8000,
        context_builder=context_builder,
        memory_lifecycle=memory_lifecycle,
        tool_executor=executor,
        reflection_agent=reflection_agent,
    )
    TEAM.configure(
        model_pool=MODEL_POOL,
        tool_executor=executor,
        reflection_agent=reflection_agent,
        max_tokens=pipeline.max_tokens,
        max_reasoning_steps=50,
    )

    task_session_runner = TaskSessionRunner(
        sessions=sessions,
        base_pipeline=pipeline,
        global_memory=memory_store,
    )
    scheduler_plugin.bind_agent_runner(ScheduledAgentRunner(
        store=scheduler_plugin.store,
        sessions=sessions,
        base_pipeline=pipeline,
        global_memory=memory_store,
        workspace=WORKDIR,
    ))

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
