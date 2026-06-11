from coding_runtime.background_task import BG
from runtime.compact import auto_compact, estimate_tokens, micro_compact
from config import THRESHOLD
from bus.team_bus import BUS
from runtime.agent_runner import AgentRunner
from runtime.agent_spec import AgentSpec
from runtime.context import ContextBundle
from runtime.reasoning_loop import DEFAULT_MAX_REASONING_STEPS
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
        self.memory_lifecycle = memory_lifecycle
        self.agent_runner = AgentRunner(
            tools=tools,
            tool_executor=tool_executor,
            provider=provider,
            model=model,
            model_pool=model_pool,
            context_builder=context_builder,
            reflection_agent=reflection_agent,
            max_tokens=max_tokens,
            max_reasoning_steps=max_reasoning_steps,
        )

    @property
    def max_tokens(self) -> int:
        return self.agent_runner.max_tokens

    @property
    def max_reasoning_steps(self) -> int:
        return self.agent_runner.max_reasoning_steps

    def fork(
        self,
        *,
        context_builder,
        memory_lifecycle=None,
    ) -> "Pipeline":
        return Pipeline(
            tools=self.agent_runner.tools,
            provider=self.agent_runner.provider,
            model=self.agent_runner.model,
            tool_executor=self.agent_runner.tool_executor,
            context_builder=context_builder,
            memory_lifecycle=memory_lifecycle,
            model_pool=self.agent_runner.model_pool,
            reflection_agent=self.agent_runner.reflection_agent,
            max_tokens=self.agent_runner.max_tokens,
            max_reasoning_steps=self.agent_runner.max_reasoning_steps,
        )

    def provider_and_model_for(self, purpose: str = "chat"):
        return self.agent_runner.provider_and_model_for_purpose(purpose)

    def run(
        self,
        session,
        profile,
        on_text: Callable[[str], None] | None = None,
        run_state=None,
        trace_store=None,
    ) -> str:
        self._run_turn(
            session,
            profile,
            on_text=on_text,
            run_state=run_state,
            trace_store=trace_store,
        )
        return get_last_assistant_text(session.messages)

    def _run_turn(
        self,
        session,
        profile,
        on_text: Callable[[str], None] | None = None,
        run_state=None,
        trace_store=None,
    ) -> None:
        self._before_turn(session)
        self.agent_runner.run_turn(
            session=session,
            spec=self._agent_spec(session, profile),
            build_context=self._before_reasoning,
            after_turn=self._after_turn,
            on_text=on_text,
            run_state=run_state,
            trace_store=trace_store,
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

        context = self.agent_runner.context_builder.build(
            session=session,
            profile=profile,
            inbox=inbox,
            background_results=notifs,
        )
        return self._with_tool_catalog(context, session, profile)

    def _with_tool_catalog(self, context, session, profile):
        catalog = self._tool_catalog(session, profile)
        if not catalog:
            return context
        messages = list(getattr(context, "messages", []) or [])
        messages.append({
            "role": "user",
            "content": catalog,
        })
        return ContextBundle(
            messages=messages,
            report=getattr(context, "report", None),
        )

    def _tool_catalog(self, session, profile) -> str:
        tools = self.agent_runner.tools
        render = getattr(tools, "tool_catalog_text", None)
        if render is None:
            return ""
        return render(
            session,
            str(getattr(profile, "tool_mode", "bot") or "bot"),
        )

    def _agent_spec(self, session, profile) -> AgentSpec:
        return AgentSpec(
            name=str(getattr(profile, "name", "main") or "main"),
            profile=profile,
            model_purpose=self._model_purpose(session, profile),
            max_tokens=self.agent_runner.max_tokens,
            max_reasoning_steps=self.agent_runner.max_reasoning_steps,
        )

    def _model_purpose(self, session, profile) -> str:
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
