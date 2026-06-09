from coding_runtime.background_task import BG
from core.compact import auto_compact, estimate_tokens, micro_compact
from config import THRESHOLD
from bus.team_bus import BUS
from core.agent_runner import AgentRunner
from core.agent_spec import AgentSpec
from core.reasoning_loop import DEFAULT_MAX_REASONING_STEPS
from typing import Callable


class Pipeline:
    def __init__(
        self,
        tools,
        provider,
        model: str,
        tool_executor=None,
        context_builder=None,
        memory_lifecycle=None,
        model_pool=None,
        reflection_agent=None,
        max_tokens: int = 8000,
        max_reasoning_steps: int = DEFAULT_MAX_REASONING_STEPS,
    ) -> None:
        if tool_executor is None:
            raise ValueError("Pipeline requires a tool_executor.")
        if context_builder is None:
            raise ValueError("Pipeline requires a context_builder.")
        self.tools = tools
        self.provider = provider
        self.model = model
        self.context_builder = context_builder
        self.max_tokens = max_tokens
        self.max_reasoning_steps = max(1, int(max_reasoning_steps))
        self.tool_executor = tool_executor
        self.memory_lifecycle = memory_lifecycle
        self.model_pool = model_pool
        self.agent_runner = AgentRunner(
            tools=self.tools,
            tool_executor=self.tool_executor,
            provider=self.provider,
            model=self.model,
            model_pool=self.model_pool,
            context_builder=self.context_builder,
            reflection_agent=reflection_agent,
            max_tokens=self.max_tokens,
            max_reasoning_steps=self.max_reasoning_steps,
        )

    def run(
        self,
        session,
        profile,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        self._run_turn(session, profile, on_text=on_text)
        return get_last_assistant_text(session.messages)

    def _run_turn(
        self,
        session,
        profile,
        on_text: Callable[[str], None] | None = None,
    ) -> None:
        self._before_turn(session)
        self.agent_runner.run_turn(
            session=session,
            spec=self._agent_spec(session, profile),
            build_context=self._before_reasoning,
            after_turn=self._after_turn,
            on_text=on_text,
        )

    def _before_turn(self, session) -> None:
        self.agent_runner.reset_turn_state(session)
        micro_compact(session.messages)
        if estimate_tokens(session.messages) > THRESHOLD:
            print("auto Compacting...")
            session.messages[:] = auto_compact(session.messages)
            session.mark_compacted()

    def _before_reasoning(self, session, profile):
        micro_compact(session.messages)
        if estimate_tokens(session.messages) > THRESHOLD:
            print("auto Compacting...")
            session.messages[:] = auto_compact(session.messages)
            session.mark_compacted()

        if self._should_include_task_runtime_events(session, profile):
            notifs = BG.drain_notifications()
            inbox = BUS.read_inbox("lead")
        else:
            notifs = []
            inbox = []

        return self.context_builder.build(
            session=session,
            profile=profile,
            inbox=inbox,
            background_results=notifs,
        )

    def _agent_spec(self, session, profile) -> AgentSpec:
        return AgentSpec(
            name=str(getattr(profile, "name", "main") or "main"),
            profile=profile,
            model_purpose=self._model_purpose(session, profile),
            max_tokens=self.max_tokens,
            max_reasoning_steps=self.max_reasoning_steps,
        )

    def _model_purpose(self, session, profile) -> str:
        metadata = session.metadata or {}
        if metadata.get("kind") == "scheduled_agent":
            return "scheduled_agent"
        if profile.tool_mode == "coding":
            return "coding"
        return "chat"

    def _after_turn(self, session) -> None:
        if self.memory_lifecycle is not None:
            self.memory_lifecycle.after_turn(session)
        session.touch()

    def _should_include_task_runtime_events(self, session, profile) -> bool:
        metadata = session.metadata or {}
        return (
            metadata.get("kind") == "task_session"
            and profile.tool_mode == "coding"
        )


def get_last_assistant_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return message_text(message)
    return ""


def message_text(message: dict) -> str:
    """Extract text content from a message (handles various formats)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "".join(parts)
    return ""
