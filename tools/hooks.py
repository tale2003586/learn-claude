
from collections import deque
import json
import time
from pathlib import Path
from typing import Any

from config import WORKDIR
from tools.executor import HookOutcome, ToolExecutionRequest, ToolExecutionResult, ToolHook


class ShellSafetyHook(ToolHook):
    name = "shell_safety"

    def matches(self, request: ToolExecutionRequest) -> bool:
        return request.tool_name == "bash"

    def before(self, request: ToolExecutionRequest) -> HookOutcome:
        command = str(request.arguments.get("command", ""))
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(item in command for item in dangerous):
            return HookOutcome(
                deny_reason="Error: Dangerous shell command blocked by shell_safety hook."
            )
        return HookOutcome()


class FileWriteScopeHook(ToolHook):
    name = "file_write_scope"
    write_tools = {"write_file", "edit_file"}

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or WORKDIR).resolve()

    def matches(self, request: ToolExecutionRequest) -> bool:
        return request.tool_name in self.write_tools

    def before(self, request: ToolExecutionRequest) -> HookOutcome:
        raw_path = request.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return HookOutcome(deny_reason="Error: Missing file path.")

        target = (self.workspace / raw_path).resolve()
        if not target.is_relative_to(self.workspace):
            return HookOutcome(
                deny_reason=f"Error: Write path escapes workspace: {raw_path}"
            )
        return HookOutcome()


class ToolLoopGuardHook(ToolHook):
    name = "tool_loop_guard"

    def __init__(self, repeat_limit: int = 3, window_size: int = 6) -> None:
        self.repeat_limit = max(2, repeat_limit)
        self.window_size = max(self.repeat_limit, window_size)
        self._recent: dict[str, deque[str]] = {}

    def matches(self, request: ToolExecutionRequest) -> bool:
        return True

    def before(self, request: ToolExecutionRequest) -> HookOutcome:
        key = request.session_id or "_global"
        fingerprint = self._fingerprint(request)
        recent = self._recent.setdefault(key, deque(maxlen=self.window_size))
        repeats = sum(1 for item in recent if item == fingerprint)
        recent.append(fingerprint)
        if repeats + 1 >= self.repeat_limit:
            return HookOutcome(
                deny_reason=(
                    "Error: Repeated tool call blocked by tool_loop_guard. "
                    "Summarize progress or try a different approach."
                )
            )
        return HookOutcome()

    def _fingerprint(self, request: ToolExecutionRequest) -> str:
        return json.dumps(
            {
                "tool": request.tool_name,
                "arguments": request.arguments,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )


class ToolTraceHook(ToolHook):
    name = "tool_trace"

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def matches(self, request: ToolExecutionRequest) -> bool:
        return True

    def after(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        self.records.append({
            "timestamp": time.time(),
            "session_id": request.session_id,
            "source": request.source,
            "call_id": request.call_id,
            "tool_name": request.tool_name,
            "status": result.status,
            "final_arguments": result.final_arguments,
            "result_preview": str(result.output)[:500],
        })
