from dataclasses import dataclass, field
import time
from typing import Callable

from core.compact import auto_compact
from tools.executor import ToolExecutionRequest


DEFAULT_MAX_REASONING_STEPS = 24
MAX_UNAVAILABLE_TOOL_ATTEMPTS = 2


@dataclass
class ToolExecutionSummary:
    manual_compact: bool = False
    unavailable_tools: list[str] = field(default_factory=list)
    loop_guard_denied: bool = False
    tool_results: list[dict] = field(default_factory=list)


class ReasoningLoop:
    """Run the model/tool reasoning loop for one agent turn.

    Pipeline owns turn setup, context policies, and memory lifecycle. This class
    owns the repeated model call -> tool execution -> model call cycle so other
    agent runtimes can reuse it without copying Pipeline internals.
    """

    def __init__(
        self,
        *,
        tools,
        tool_executor,
        max_tokens: int = 8000,
        max_reasoning_steps: int = DEFAULT_MAX_REASONING_STEPS,
    ) -> None:
        self.tools = tools
        self.tool_executor = tool_executor
        self.max_tokens = max_tokens
        self.max_reasoning_steps = max(1, int(max_reasoning_steps))

    def run(
        self,
        *,
        session,
        profile,
        build_context: Callable,
        resolve_provider: Callable,
        after_turn: Callable,
        after_tool_calls: Callable | None = None,
        reflection_agent=None,
        on_text: Callable[[str], None] | None = None,
    ) -> None:
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
                    after_turn=after_turn,
                    on_text=on_text,
                )
                return
            self._check_automation_budget(session, reasoning_steps)
            turn_context = build_context(session, profile)

            response = self._reasoning_step(
                session=session,
                context=turn_context,
                profile=profile,
                resolve_provider=resolve_provider,
                on_text=on_text,
            )

            self._after_reasoning_step(session, response)

            if not response.tool_calls:
                after_turn(session)
                return

            execution = self._execute_tool_calls(session, response, profile)
            if after_tool_calls is not None and after_tool_calls(
                session,
                response,
                execution,
            ):
                after_turn(session)
                return
            if self._automation_should_pause(session):
                after_turn(session)
                return

            if execution.loop_guard_denied:
                self._stop_turn(
                    session,
                    "本轮已停止：模型重复调用同一工具，已触发循环保护。请调整请求后重试。",
                    reason="repeated_tool_call",
                    after_turn=after_turn,
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
                        after_turn=after_turn,
                        on_text=on_text,
                    )
                    return

            if self._apply_reflection(
                reflection_agent,
                session=session,
                profile=profile,
                response=response,
                execution=execution,
                reasoning_steps=reasoning_steps,
                after_turn=after_turn,
                on_text=on_text,
            ):
                return

            if execution.manual_compact:
                session.messages[:] = auto_compact(session.messages)
                session.mark_compacted()

    def _reasoning_step(
        self,
        *,
        session,
        context,
        profile,
        resolve_provider: Callable,
        on_text=None,
    ):
        provider, model = resolve_provider(session, profile)
        use_stream = on_text is not None and hasattr(provider, "stream_chat")
        method = provider.chat
        if use_stream:
            method = provider.stream_chat
        response = method(
            model=model,
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
                summary.tool_results.append({
                    "name": call.name,
                    "output": output,
                    "status": "success",
                    "final_arguments": call.arguments,
                })
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
                summary.tool_results.append({
                    "name": call.name,
                    "output": output,
                    "status": result.status,
                    "final_arguments": result.final_arguments,
                })

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
        after_turn: Callable,
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
        after_turn(session)

    def _apply_reflection(
        self,
        reflection_agent,
        *,
        session,
        profile,
        response,
        execution: ToolExecutionSummary,
        reasoning_steps: int,
        after_turn: Callable,
        on_text: Callable[[str], None] | None,
    ) -> bool:
        if reflection_agent is None:
            return False

        should_reflect = getattr(reflection_agent, "should_reflect", None)
        if should_reflect is not None and not should_reflect(
            session=session,
            profile=profile,
            response=response,
            execution=execution,
            reasoning_steps=reasoning_steps,
        ):
            return False

        decision = reflection_agent.reflect(
            session=session,
            profile=profile,
            response=response,
            execution=execution,
            reasoning_steps=reasoning_steps,
        )
        action = str(getattr(decision, "action", "continue") or "continue").lower()
        instruction = str(getattr(decision, "instruction", "") or "").strip()
        message = str(getattr(decision, "message", "") or "").strip()
        reason = str(getattr(decision, "reason", "") or "").strip()

        if action in {"stop", "ask_user"}:
            self._stop_turn(
                session,
                message or reason or "本轮已停止：reflection agent 建议暂停当前流程。",
                reason=f"reflection_{action}",
                after_turn=after_turn,
                on_text=on_text,
            )
            return True

        if instruction:
            session.messages.append({
                "role": "user",
                "content": (
                    "<reflection-instruction>\n"
                    f"{instruction}\n"
                    "</reflection-instruction>"
                ),
            })
        return False

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
