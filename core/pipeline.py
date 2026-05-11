from coding_runtime.background_task import BG
from core.compact import auto_compact, estimate_tokens, micro_compact
from config import THRESHOLD
from bus.team_bus import BUS
from tools.executor import ToolExecutionRequest


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
        self.tool_executor = tool_executor
        self.memory_lifecycle = memory_lifecycle

    def run(self, session, profile) -> str:
        self._run_turn(session, profile)
        return get_last_assistant_text(session.messages)

    def _run_turn(self, session, profile) -> None:
        self._before_turn(session)

        while True:
            turn_context = self._before_reasoning(session, profile)

            response = self._reasoning_step(
                session=session,
                context=turn_context,
                profile=profile,
            )

            self._after_reasoning_step(session, response)

            if not response.tool_calls:
                self._after_turn(session)
                return

            manual_compact = self._execute_tool_calls(session, response, profile)

            if manual_compact:
                session.messages[:] = auto_compact(session.messages)
                session.mark_compacted()

    def _before_turn(self, session) -> None:
        self.tools.reset_turn_unlocks(session)
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

    def _reasoning_step(self, *, session, context, profile):
        return self.provider.chat(
            model=self.model,
            messages=context.messages,
            tools=self.tools.schemas_for_turn(session, profile.tool_mode),
            tool_choice="auto",
            max_tokens=self.max_tokens,
        )

    def _after_reasoning_step(self, session, response) -> None:
        if response.raw_message:
            session.messages.append(response.raw_message)
        else:
            session.messages.append({
                "role": "assistant",
                "content": response.content,
            })

    def _execute_tool_calls(self, session, response, profile) -> bool:
        manual_compact = False

        for call in response.tool_calls:
            if call.name == "compact":
                manual_compact = True
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
                request = ToolExecutionRequest(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    session_id=session.id,
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
        return manual_compact

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
