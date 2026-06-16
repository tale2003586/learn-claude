from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import traceback
import uuid
from typing import Any

from agents.subagent.tools import (
    DEFAULT_SUBTASK_AGENT_TYPE,
    SUBTASK_SYSTEM_PROMPTS,
    SUBTASK_TOOL_WHITELIST,
)
from modes.base import ModeProfile
from runtime.context import ContextBuilder
from runtime.pipeline import Pipeline, get_last_assistant_text
from sessions import Session
from tools.tool_registry import ToolRegistry


@dataclass
class SubagentResult:
    agent_type: str
    success: bool
    summary: str
    files_touched: list[str] = field(default_factory=list)
    tool_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskSubagentRunner:
    """Run a focused, short-lived subagent with isolated context."""

    def __init__(
        self,
        *,
        base_pipeline: Pipeline,
        max_reasoning_steps: int | None = None,
    ) -> None:
        self.base_pipeline = base_pipeline
        self.max_reasoning_steps = (
            max_reasoning_steps
            if max_reasoning_steps is not None
            else min(base_pipeline.max_reasoning_steps, 12)
        )

    def run(
        self,
        *,
        prompt: str,
        agent_type: str = DEFAULT_SUBTASK_AGENT_TYPE,
        description: str = "",
        parent_session=None,
    ) -> SubagentResult:
        requested_agent_type = agent_type
        agent_type = _normalize_agent_type(agent_type)
        if agent_type is None:
            return SubagentResult(
                agent_type=str(requested_agent_type or ""),
                success=False,
                summary="",
                files_touched=[],
                tool_count=0,
                error=f"Unknown agent_type: {requested_agent_type}",
            )
        session = self._new_session(
            prompt=prompt,
            agent_type=agent_type,
            description=description,
            parent_session=parent_session,
        )
        pipeline = self._sub_pipeline(agent_type)
        profile = self._profile(agent_type)

        try:
            summary = pipeline.run(session, profile)
            return SubagentResult(
                agent_type=agent_type,
                success=True,
                summary=summary or get_last_assistant_text(session.messages),
                files_touched=_extract_files_touched(session.messages),
                tool_count=_count_tool_calls(session.messages),
            )
        except Exception as exc:
            return SubagentResult(
                agent_type=agent_type,
                success=False,
                summary="",
                files_touched=_extract_files_touched(session.messages),
                tool_count=_count_tool_calls(session.messages),
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )

    def _sub_pipeline(self, agent_type: str) -> Pipeline:
        base_runner = self.base_pipeline.agent_runner
        return Pipeline(
            tools=self._filtered_tools(agent_type),
            provider=base_runner.provider,
            model=base_runner.model,
            tool_executor=base_runner.tool_executor,
            context_builder=ContextBuilder(
                memory_store=None,
                security_auto_context_enabled=False,
            ),
            memory_lifecycle=None,
            model_pool=base_runner.model_pool,
            reflection_agent=base_runner.reflection_agent,
            max_tokens=base_runner.max_tokens,
            max_reasoning_steps=self.max_reasoning_steps,
        )

    def _filtered_tools(self, agent_type: str) -> ToolRegistry:
        allowed = SUBTASK_TOOL_WHITELIST.get(agent_type, set())
        registry = ToolRegistry()
        for name, tool in self.base_pipeline.agent_runner.tools._tools.items():
            if name not in allowed:
                continue
            registry.register(
                tool.schema,
                tool.handler,
                risk=tool.risk,
                enabled_modes=set(tool.enabled_modes) if tool.enabled_modes else None,
                source=f"subagent:{agent_type}",
                always_on=tool.always_on,
                session_scoped=tool.session_scoped,
                admin_only=tool.admin_only,
            )
        return registry

    def _profile(self, agent_type: str) -> ModeProfile:
        return ModeProfile(
            name=f"subagent:{agent_type}",
            tool_mode="coding",
            system_prompt=SUBTASK_SYSTEM_PROMPTS[agent_type],
        )

    def _new_session(
        self,
        *,
        prompt: str,
        agent_type: str,
        description: str,
        parent_session=None,
    ) -> Session:
        metadata = {
            "kind": "subagent",
            "agent_type": agent_type,
            "description": description,
            "user_role": "admin",
        }
        if parent_session is not None:
            metadata["parent_session_id"] = getattr(parent_session, "id", "")
            parent_metadata = getattr(parent_session, "metadata", {}) or {}
            for key in (
                "user_id",
                "user_role",
                "workspace_root",
                "workspace_display_name",
                "workspace_allowed_root",
                "workspace_source",
                "workspace_requested",
            ):
                if key in parent_metadata:
                    metadata[key] = parent_metadata[key]
        session = Session(
            id=f"subtask:{agent_type}:{uuid.uuid4().hex[:8]}",
            current_mode="coding",
            metadata=metadata,
        )
        session.add_message(
            "user",
            _subtask_prompt(prompt=prompt, agent_type=agent_type, description=description),
            metadata={"kind": "subtask_prompt"},
        )
        return session


def _subtask_prompt(*, prompt: str, agent_type: str, description: str) -> str:
    title = description.strip() or agent_type
    return (
        "<subtask>\n"
        f"Description: {title}\n"
        f"Agent type: {agent_type}\n\n"
        f"{prompt.strip()}\n"
        "</subtask>"
    )


def _normalize_agent_type(agent_type: str | None) -> str | None:
    value = (agent_type or "").strip() or DEFAULT_SUBTASK_AGENT_TYPE
    if value not in SUBTASK_TOOL_WHITELIST:
        return None
    return value


def _count_tool_calls(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            count += len(calls)
    return count


def _extract_files_touched(messages: list[dict[str, Any]]) -> list[str]:
    paths: set[str] = set()
    for message in messages:
        if message.get("role") != "tool":
            continue
        args = message.get("final_arguments")
        if isinstance(args, dict):
            _collect_paths(args, paths)
        content = str(message.get("content") or "")
        for match in re.findall(r"(?:Wrote|Edited)\s+(?:\d+\s+bytes\s+to\s+)?([^\n]+)", content):
            cleaned = match.strip()
            if cleaned and not cleaned.startswith("Error:"):
                paths.add(cleaned)
    return sorted(paths)


def _collect_paths(value: Any, paths: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"path", "file", "filename"} and isinstance(item, str):
                paths.add(item)
            elif key == "paths" and isinstance(item, list):
                for path in item:
                    if isinstance(path, str):
                        paths.add(path)
            else:
                _collect_paths(item, paths)
    elif isinstance(value, list):
        for item in value:
            _collect_paths(item, paths)
