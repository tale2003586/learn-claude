from coding_runtime.background_task import BG
from core.compact import auto_compact, estimate_tokens, micro_compact
from config import THRESHOLD
from bus.team_bus import BUS
from tools.executor import ToolExecutionRequest
from dataclasses import dataclass, field
import time
from typing import Callable


DEFAULT_MAX_REASONING_STEPS = 24
MAX_UNAVAILABLE_TOOL_ATTEMPTS = 2


@dataclass
class ToolExecutionSummary:
    manual_compact: bool = False
    unavailable_tools: list[str] = field(default_factory=list)
    loop_guard_denied: bool = False


class Pipeline:
    def __init__(
        self,
        tools,
        provider,
        model: str,
        tool_executor=None,
        context_builder=None,
        memory_lifecycle=None,
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
        reasoning_steps = 0
        unavailable_attempts: dict[str, int] = {}

        while True:
            reasoning_steps += 1
            if self._reasoning_budget_exceeded(session, reasoning_steps):
                self._stop_turn(
                    session,
                    (
                        "本轮已停止：工具推理步骤超过上限 "
                        f"({self.max_reasoning_steps})，已触发循环保护。"
                    ),
                    reason="reasoning_step_limit",
                    on_text=on_text,
                )
                return
            self._check_automation_budget(session, reasoning_steps)
            turn_context = self._before_reasoning(session, profile)

            response = self._reasoning_step(
                session=session,
                context=turn_context,
                profile=profile,
                on_text=on_text,
            )

            self._after_reasoning_step(session, response)

            if not response.tool_calls:
                self._after_turn(session)
                return

            execution = self._execute_tool_calls(session, response, profile)
            if self._automation_should_pause(session):
                self._after_turn(session)
                return

            if execution.loop_guard_denied:
                self._stop_turn(
                    session,
                    "本轮已停止：模型重复调用同一工具，已触发循环保护。请调整请求后重试。",
                    reason="repeated_tool_call",
                    on_text=on_text,
                )
                return

            for tool_name in execution.unavailable_tools:
                unavailable_attempts[tool_name] = unavailable_attempts.get(tool_name, 0) + 1
                if unavailable_attempts[tool_name] >= MAX_UNAVAILABLE_TOOL_ATTEMPTS:
                    self._stop_turn(
                        session,
                        (
                            "本轮已停止：模型重复请求当前不可用的工具 "
                            f"`{tool_name}`。请切换到允许该工具的模式，"
                            "或让助手使用 `tool_search` 选择当前模式可用的工具。"
                        ),
                        reason="unavailable_tool_loop",
                        on_text=on_text,
                    )
                    return

            if execution.manual_compact:
                session.messages[:] = auto_compact(session.messages)
                session.mark_compacted()

    def _before_turn(self, session) -> None:
        self.tools.reset_turn_unlocks(session)
        reset_tool_hooks = getattr(self.tool_executor, "reset_turn", None)
        if reset_tool_hooks is not None:
            reset_tool_hooks(session.id)
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

    def _reasoning_step(self, *, session, context, profile, on_text=None):
        use_stream = on_text is not None and hasattr(self.provider, "stream_chat")
        method = self.provider.chat
        if use_stream:
            method = self.provider.stream_chat
        response = method(
            model=self.model,
            messages=context.messages,
            tools=self.tools.schemas_for_turn(session, profile.tool_mode),
            tool_choice="auto",
            max_tokens=self.max_tokens,
            **({"on_text": on_text} if use_stream else {}),
        )
        if on_text is not None and not use_stream and response.content:
            on_text(response.content)
        return response

    def _after_reasoning_step(self, session, response) -> None:
        if response.raw_message:
            session.messages.append(response.raw_message)
        else:
            session.messages.append({
                "role": "assistant",
                "content": response.content,
            })

    def _execute_tool_calls(self, session, response, profile) -> ToolExecutionSummary:
        summary = ToolExecutionSummary()

        for call in response.tool_calls:
            if call.name == "compact":
                summary.manual_compact = True
                output = "Manual compact requested."
                session.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": output,
                    "status": "success",
                    "final_arguments": call.arguments,
                    "pre_hook_trace": [],
                    "post_hook_trace": [],
                })
            else:
                execution_error = self._tool_execution_error(
                    call.name,
                    session=session,
                    mode=profile.tool_mode,
                )
                request = ToolExecutionRequest(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    session_id=session.id,
                    source=str((session.metadata or {}).get("kind", "passive")),
                    metadata=session.metadata,
                )
                result = self.tool_executor.execute(
                    request,
                    lambda name, args: self.tools.execute(
                        name,
                        args,
                        session=session,
                        mode=profile.tool_mode,
                    ),
                )
                output = result.output
                if execution_error:
                    summary.unavailable_tools.append(call.name)
                if any(
                    item.hook_name == "tool_loop_guard" and item.decision == "deny"
                    for item in result.pre_hook_trace
                ):
                    summary.loop_guard_denied = True

                session.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": output,
                    "status": result.status,
                    "final_arguments": result.final_arguments,
                    "pre_hook_trace": [
                        item.__dict__ for item in result.pre_hook_trace
                    ],
                    "post_hook_trace": [
                        item.__dict__ for item in result.post_hook_trace
                    ],
                })
        return summary

    def _tool_execution_error(self, name: str, *, session, mode: str) -> str | None:
        checker = getattr(self.tools, "execution_error_for_turn", None)
        if checker is None:
            return None
        return checker(name, session=session, mode=mode)

    def _reasoning_budget_exceeded(self, session, reasoning_steps: int) -> bool:
        metadata = session.metadata or {}
        return (
            metadata.get("kind") != "scheduled_agent"
            and reasoning_steps > self.max_reasoning_steps
        )

    def _stop_turn(
        self,
        session,
        message: str,
        *,
        reason: str,
        on_text: Callable[[str], None] | None,
    ) -> None:
        session.add_message(
            "assistant",
            message,
            metadata={
                "kind": "agent_loop_guard",
                "reason": reason,
            },
        )
        if on_text is not None:
            on_text(message)
        self._after_turn(session)

    def _check_automation_budget(self, session, reasoning_steps: int) -> None:
        metadata = session.metadata or {}
        if metadata.get("kind") != "scheduled_agent":
            return
        limits = metadata.get("automation_limits", {})
        max_steps = max(1, int(limits.get("max_reasoning_steps", 12)))
        if reasoning_steps > max_steps:
            raise RuntimeError("Scheduled agent reasoning-step budget exceeded.")
        started_at = float(metadata.get("automation_started_monotonic", time.monotonic()))
        timeout_seconds = max(1, int(limits.get("timeout_seconds", 300)))
        if time.monotonic() - started_at > timeout_seconds:
            raise RuntimeError("Scheduled agent timeout exceeded.")

    def _automation_should_pause(self, session) -> bool:
        metadata = session.metadata or {}
        return (
            metadata.get("kind") == "scheduled_agent"
            and bool(metadata.get("runtime_approval_request"))
        )

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
