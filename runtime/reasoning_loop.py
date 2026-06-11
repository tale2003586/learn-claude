import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from runtime.compact import auto_compact, estimate_tokens
from runtime.trace.events import (
    CONTEXT_BUILD_COMPLETED,
    CONTEXT_BUILD_STARTED,
    CONTEXT_SANITIZED,
    MODEL_ROUTE_ATTEMPTS,
    MODEL_CALL_COMPLETED,
    MODEL_CALL_FAILED,
    MODEL_CALL_STARTED,
    REASONING_STEP_COMPLETED,
    REASONING_STEP_STARTED,
    TOOL_CALL_COMPLETED,
    TOOL_CALL_FAILED,
    TOOL_CALL_STARTED,
)
from runtime.trace.trace_store import event_preview
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
        run_state=None,
        trace_store=None,
    ) -> None:
        reasoning_steps = 0
        unavailable_attempts: dict[str, int] = {}
        empty_model_responses = 0

        while True:
            reasoning_steps += 1
            if self._reasoning_budget_exceeded(session, reasoning_steps):
                self._trace(trace_store, run_state, "reasoning_budget_exceeded", {
                    "attempted_step": reasoning_steps,
                    "max_reasoning_steps": self.max_reasoning_steps,
                })
                self._stop_turn(
                    session,
                    (
                        "本轮已停止：工具推理步骤超过上限 "
                        f"({self.max_reasoning_steps})，已触发循环保护。"
                    ),
                    reason="reasoning_step_limit",
                    after_turn=after_turn,
                    on_text=on_text,
                    run_state=run_state,
                    trace_store=trace_store,
                )
                return
            if run_state is not None:
                run_state.record_reasoning_step(reasoning_steps)
                if trace_store is not None:
                    trace_store.write_run_state(run_state)
            self._trace(trace_store, run_state, "reasoning_step_started", {
                "step": reasoning_steps,
                "session_id": session.id,
                "message_count": len(session.messages),
            })
            self._trace(
                trace_store,
                run_state,
                REASONING_STEP_STARTED,
                {
                    "message_count": len(session.messages),
                },
                step=reasoning_steps,
                span_id=_step_span_id(run_state, reasoning_steps),
            )
            context_started = time.perf_counter()
            context_span_id = _context_span_id(run_state, reasoning_steps)
            step_span_id = _step_span_id(run_state, reasoning_steps)
            self._trace(
                trace_store,
                run_state,
                CONTEXT_BUILD_STARTED,
                {
                    "message_count_before": len(session.messages),
                },
                step=reasoning_steps,
                span_id=context_span_id,
                parent_span_id=step_span_id,
            )
            turn_context = build_context(session, profile)
            context_messages = getattr(turn_context, "messages", [])
            context_report = _context_report_payload(
                getattr(turn_context, "report", None)
            )
            self._trace(
                trace_store,
                run_state,
                CONTEXT_BUILD_COMPLETED,
                {
                    "duration_ms": _elapsed_ms(context_started),
                    "message_count_before": len(session.messages),
                    "message_count_after": len(context_messages),
                    "context_summary": _context_summary(context_messages),
                    "context_report": context_report,
                },
                step=reasoning_steps,
                span_id=context_span_id,
                parent_span_id=step_span_id,
            )

            response = self._reasoning_step(
                session=session,
                context=turn_context,
                profile=profile,
                resolve_provider=resolve_provider,
                on_text=on_text,
                run_state=run_state,
                trace_store=trace_store,
                reasoning_step=reasoning_steps,
            )

            if _is_empty_response(response):
                empty_model_responses += 1
                self._trace(
                    trace_store,
                    run_state,
                    REASONING_STEP_COMPLETED,
                    {
                        "reason": "empty_model_response",
                        "tool_call_count": 0,
                        "attempt": empty_model_responses,
                    },
                    step=reasoning_steps,
                    span_id=_step_span_id(run_state, reasoning_steps),
                )
                self._trace(trace_store, run_state, "empty_model_response", {
                    "step": reasoning_steps,
                    "attempt": empty_model_responses,
                })
                if empty_model_responses >= 2:
                    self._stop_turn(
                        session,
                        "本轮已停止：模型连续返回空回复且没有工具调用。",
                        reason="empty_model_response",
                        after_turn=after_turn,
                        on_text=on_text,
                        run_state=run_state,
                        trace_store=trace_store,
                    )
                    return
                session.add_message(
                    "user",
                    (
                        "<runtime-retry reason=\"empty_model_response\">\n"
                        "Your previous response was empty and contained no tool calls. "
                        "Continue the task by either calling an appropriate tool or "
                        "providing a concrete final answer.\n"
                        "</runtime-retry>"
                    ),
                    metadata={
                        "kind": "runtime_retry",
                        "reason": "empty_model_response",
                    },
                )
                continue

            empty_model_responses = 0
            self._after_reasoning_step(session, response)

            if not response.tool_calls:
                self._trace(
                    trace_store,
                    run_state,
                    REASONING_STEP_COMPLETED,
                    {
                        "reason": "assistant_final_message",
                        "tool_call_count": 0,
                    },
                    step=reasoning_steps,
                    span_id=_step_span_id(run_state, reasoning_steps),
                )
                self._trace(trace_store, run_state, "reasoning_loop_completed", {
                    "step": reasoning_steps,
                    "reason": "assistant_final_message",
                })
                after_turn(session)
                return

            execution = self._execute_tool_calls(
                session,
                response,
                profile,
                run_state=run_state,
                trace_store=trace_store,
                reasoning_step=reasoning_steps,
            )
            self._trace(
                trace_store,
                run_state,
                REASONING_STEP_COMPLETED,
                {
                    "reason": "tool_calls_executed",
                    "tool_call_count": len(response.tool_calls),
                    "loop_guard_denied": execution.loop_guard_denied,
                    "manual_compact": execution.manual_compact,
                },
                step=reasoning_steps,
                span_id=_step_span_id(run_state, reasoning_steps),
            )
            if after_tool_calls is not None and after_tool_calls(
                session,
                response,
                execution,
            ):
                self._trace(trace_store, run_state, "reasoning_loop_paused", {
                    "reason": "after_tool_calls_callback",
                })
                after_turn(session)
                return
            if execution.loop_guard_denied:
                self._stop_turn(
                    session,
                    "本轮已停止：模型重复调用同一工具，已触发循环保护。请调整请求后重试。",
                    reason="repeated_tool_call",
                    after_turn=after_turn,
                    on_text=on_text,
                    run_state=run_state,
                    trace_store=trace_store,
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
                        run_state=run_state,
                        trace_store=trace_store,
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
                run_state=run_state,
                trace_store=trace_store,
            ):
                return

            if execution.manual_compact:
                self._trace(trace_store, run_state, "manual_compact_applied", {
                    "step": reasoning_steps,
                })
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
        run_state=None,
        trace_store=None,
        reasoning_step: int = 0,
    ):
        provider, model = resolve_provider(session, profile)
        use_stream = on_text is not None and hasattr(provider, "stream_chat")
        method = provider.chat
        if use_stream:
            method = provider.stream_chat
        tools = self.tools.schemas_for_turn(session, profile.tool_mode)
        context_messages, dropped_messages = _sanitize_context_messages(context.messages)
        context_summary = _context_summary(context_messages)
        if dropped_messages:
            self._trace(
                trace_store,
                run_state,
                CONTEXT_SANITIZED,
                {
                    "dropped_count": len(dropped_messages),
                    "dropped_messages": dropped_messages,
                    "context_summary": context_summary,
                },
                step=reasoning_step,
                span_id=_context_span_id(run_state, reasoning_step),
                parent_span_id=_step_span_id(run_state, reasoning_step),
            )
        provider_name = type(provider).__name__
        span_id = _model_span_id(run_state, reasoning_step)
        parent_span_id = _step_span_id(run_state, reasoning_step)
        started = time.perf_counter()
        self._trace(trace_store, run_state, "model_requested", {
            "model": model,
            "provider": provider_name,
            "tool_mode": profile.tool_mode,
            "tool_count": len(tools),
            "tool_names": _tool_names(tools),
            "message_count": len(context_messages),
            "max_tokens": self.max_tokens,
            "stream": use_stream,
            "context_summary": context_summary,
        })
        self._trace(
            trace_store,
            run_state,
            MODEL_CALL_STARTED,
            {
                "model": model,
                "provider": provider_name,
                "tool_mode": profile.tool_mode,
                "tool_count": len(tools),
                "tool_names": _tool_names(tools),
                "message_count": len(context_messages),
                "max_tokens": self.max_tokens,
                "stream": use_stream,
                "context_summary": context_summary,
            },
            step=reasoning_step,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )
        try:
            response = method(
                model=model,
                messages=context_messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=self.max_tokens,
                **({"on_text": on_text} if use_stream else {}),
            )
        except Exception as exc:
            route_attempts = getattr(exc, "attempts", None)
            if route_attempts:
                self._trace(
                    trace_store,
                    run_state,
                    MODEL_ROUTE_ATTEMPTS,
                    {
                        "purpose": getattr(exc, "purpose", ""),
                        "attempts": route_attempts,
                    },
                    step=reasoning_step,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                )
            self._trace(
                trace_store,
                run_state,
                MODEL_CALL_FAILED,
                {
                    "model": model,
                    "provider": provider_name,
                    "duration_ms": _elapsed_ms(started),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "route_attempts": route_attempts or [],
                    "context_summary": context_summary,
                },
                step=reasoning_step,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
            raise
        if on_text is not None and not use_stream and response.content:
            on_text(response.content)
        completed_payload = {
            "model": model,
            "provider": provider_name,
            "duration_ms": _elapsed_ms(started),
            "content_preview": event_preview(response.content),
            "tool_call_count": len(response.tool_calls),
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments_preview": event_preview(call.arguments),
                }
                for call in response.tool_calls
            ],
            "usage": _usage_payload(getattr(response, "usage", None)),
            "provider_metadata": getattr(response, "provider_metadata", {}) or {},
        }
        self._trace(
            trace_store,
            run_state,
            MODEL_CALL_COMPLETED,
            completed_payload,
            step=reasoning_step,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )
        self._trace(trace_store, run_state, "model_returned", {
            "content_preview": event_preview(response.content),
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in response.tool_calls
            ],
            "tool_call_count": len(response.tool_calls),
        })
        return response

    def _after_reasoning_step(self, session, response) -> None:
        if response.raw_message:
            session.messages.append(response.raw_message)
        elif response.content is not None or response.tool_calls:
            session.messages.append({
                "role": "assistant",
                "content": response.content or "",
            })

    def _execute_tool_calls(
        self,
        session,
        response,
        profile,
        *,
        run_state=None,
        trace_store=None,
        reasoning_step: int = 0,
    ) -> ToolExecutionSummary:
        summary = ToolExecutionSummary()

        for call in response.tool_calls:
            span_id = _tool_span_id(run_state, reasoning_step, call.id)
            parent_span_id = _step_span_id(run_state, reasoning_step)
            self._trace(
                trace_store,
                run_state,
                TOOL_CALL_STARTED,
                {
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "arguments_preview": event_preview(call.arguments),
                    "source": str((session.metadata or {}).get("kind", "passive")),
                },
                step=reasoning_step,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
            if call.name == "compact":
                summary.manual_compact = True
                started = time.perf_counter()
                output = "Manual compact requested."
                if run_state is not None:
                    run_state.record_tool(call.name)
                    if trace_store is not None:
                        trace_store.write_run_state(run_state)
                duration_ms = _elapsed_ms(started)
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
                self._trace(
                    trace_store,
                    run_state,
                    TOOL_CALL_COMPLETED,
                    {
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "status": "success",
                        "duration_ms": duration_ms,
                        "final_arguments_preview": event_preview(call.arguments),
                        "output_preview": output,
                        "pre_hook_trace": [],
                        "post_hook_trace": [],
                    },
                    step=reasoning_step,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                )
                self._trace(trace_store, run_state, "tool_executed", {
                    "call_id": call.id,
                    "name": call.name,
                    "status": "success",
                    "final_arguments": call.arguments,
                    "output_preview": output,
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
                if run_state is not None:
                    run_state.record_tool(call.name)
                    if trace_store is not None:
                        trace_store.write_run_state(run_state)
                tool_payload = {
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "execution_error": execution_error,
                    "final_arguments_preview": event_preview(result.final_arguments),
                    "output_preview": event_preview(output),
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                    "pre_hook_trace": [
                        item.__dict__ for item in result.pre_hook_trace
                    ],
                    "post_hook_trace": [
                        item.__dict__ for item in result.post_hook_trace
                    ],
                }
                self._trace(
                    trace_store,
                    run_state,
                    (
                        TOOL_CALL_FAILED
                        if result.status == "error"
                        else TOOL_CALL_COMPLETED
                    ),
                    tool_payload,
                    step=reasoning_step,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                )
                self._trace(trace_store, run_state, "tool_executed", {
                    "call_id": call.id,
                    "name": call.name,
                    "status": result.status,
                    "execution_error": execution_error,
                    "final_arguments": result.final_arguments,
                    "output_preview": event_preview(output),
                    "pre_hook_trace": [
                        item.__dict__ for item in result.pre_hook_trace
                    ],
                    "post_hook_trace": [
                        item.__dict__ for item in result.post_hook_trace
                    ],
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
        return reasoning_steps > self.max_reasoning_steps

    def _stop_turn(
        self,
        session,
        message: str,
        *,
        reason: str,
        after_turn: Callable,
        on_text: Callable[[str], None] | None,
        run_state=None,
        trace_store=None,
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
        if run_state is not None:
            run_state.stop(reason, message)
            if trace_store is not None:
                trace_store.write_run_state(run_state)
        self._trace(trace_store, run_state, "run_stopped", {
            "reason": reason,
            "message_preview": event_preview(message),
        })
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
        run_state=None,
        trace_store=None,
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
        self._trace(trace_store, run_state, "reflection_decision", {
            "action": action,
            "reason": reason,
            "message_preview": event_preview(message),
            "instruction_preview": event_preview(instruction),
        })

        if action in {"stop", "ask_user"}:
            self._stop_turn(
                session,
                message or reason or "本轮已停止：reflection agent 建议暂停当前流程。",
                reason=f"reflection_{action}",
                after_turn=after_turn,
                on_text=on_text,
                run_state=run_state,
                trace_store=trace_store,
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

    def _trace(
        self,
        trace_store,
        run_state,
        event_name: str,
        payload: dict,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        step: int | None = None,
    ) -> None:
        if trace_store is not None and run_state is not None:
            trace_store.append_event(
                run_state,
                event_name,
                payload,
                span_id=span_id,
                parent_span_id=parent_span_id,
                step=step,
            )


def _usage_payload(usage) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {
            "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
            "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    try:
        return asdict(usage)
    except TypeError:
        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _context_summary(messages: list[dict]) -> dict:
    by_role: dict[str, int] = {}
    empty_assistant_messages = 0
    tool_result_messages = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        by_role[role] = by_role.get(role, 0) + 1
        if role == "assistant" and _is_empty_assistant_message(message):
            empty_assistant_messages += 1
        if role == "tool":
            tool_result_messages += 1
    return {
        "message_count": len(messages or []),
        "roles": by_role,
        "assistant_messages": by_role.get("assistant", 0),
        "user_messages": by_role.get("user", 0),
        "tool_messages": tool_result_messages,
        "empty_assistant_messages": empty_assistant_messages,
        "estimated_tokens": estimate_tokens(messages or []),
    }


def _context_report_payload(report) -> dict:
    if report is None:
        return {}
    if hasattr(report, "to_dict"):
        return report.to_dict()
    if isinstance(report, dict):
        return dict(report)
    return getattr(report, "__dict__", {}) or {}


def _sanitize_context_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    sanitized = []
    dropped = []
    for index, message in enumerate(messages or []):
        if isinstance(message, dict) and _is_empty_assistant_message(message):
            dropped.append({
                "index": index,
                "role": "assistant",
                "reason": "empty_assistant_message",
            })
            continue
        sanitized.append(message)
    return sanitized, dropped


def _is_empty_assistant_message(message: dict) -> bool:
    if str(message.get("role") or "") != "assistant":
        return False
    if message.get("tool_calls"):
        return False
    content = message.get("content")
    if content is None:
        return True
    if isinstance(content, str):
        return content == ""
    if isinstance(content, list):
        return len(content) == 0
    return False


def _is_empty_response(response) -> bool:
    if getattr(response, "tool_calls", None):
        return False
    content = getattr(response, "content", None)
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        return len(content) == 0
    return False


def _tool_names(tools: list[dict]) -> list[str]:
    names = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
        else:
            name = str(tool.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _context_span_id(run_state, step: int) -> str:
    run_id = getattr(run_state, "run_id", "run")
    return f"{run_id}:context:{step}"


def _step_span_id(run_state, step: int) -> str:
    run_id = getattr(run_state, "run_id", "run")
    return f"{run_id}:step:{step}"


def _model_span_id(run_state, step: int) -> str:
    run_id = getattr(run_state, "run_id", "run")
    return f"{run_id}:model:{step}"


def _tool_span_id(run_state, step: int, call_id: str) -> str:
    run_id = getattr(run_state, "run_id", "run")
    suffix = str(call_id or "unknown").replace(":", "_")
    return f"{run_id}:tool:{step}:{suffix}"
