import time
from typing import Any

from plugins.scheduler.planning import (
    capability_allows,
    is_forbidden_automation_tool,
)
from tools.executor import HookOutcome, ToolExecutionRequest, ToolHook


class ToolApprovalPolicyHook(ToolHook):
    name = "scheduled_agent_approval"

    def matches(self, request: ToolExecutionRequest) -> bool:
        return request.metadata.get("kind") == "scheduled_agent"

    def before(self, request: ToolExecutionRequest) -> HookOutcome:
        metadata = request.metadata
        if is_forbidden_automation_tool(request.tool_name):
            return HookOutcome(
                deny_reason="Error: Tool is forbidden for unattended automation."
            )
        limits = metadata.get("automation_limits", {})
        timeout_seconds = _positive_int(limits.get("timeout_seconds"), 300)
        started_at = float(metadata.get("automation_started_monotonic", time.monotonic()))
        if time.monotonic() - started_at > timeout_seconds:
            return HookOutcome(deny_reason="Error: Scheduled agent timeout exceeded.")

        used = int(metadata.get("automation_tool_calls_used", 0)) + 1
        metadata["automation_tool_calls_used"] = used
        if used > _positive_int(limits.get("max_tool_calls"), 16):
            return HookOutcome(deny_reason="Error: Scheduled agent tool-call budget exceeded.")

        capability = _find_capability(
            metadata.get("approved_capabilities", []),
            request.tool_name,
        )
        if capability is None:
            return self._request_approval(
                request,
                "Tool is not approved for this scheduled agent.",
            )
        try:
            allowed = capability_allows(capability, request.arguments)
        except ValueError as exc:
            return self._request_approval(request, str(exc))
        if not allowed:
            return self._request_approval(
                request,
                "Tool arguments exceed the approved capability scope.",
            )
        return HookOutcome()

    def _request_approval(
        self,
        request: ToolExecutionRequest,
        reason: str,
    ) -> HookOutcome:
        request.metadata["runtime_approval_request"] = {
            "tool": request.tool_name,
            "arguments": request.arguments,
            "reason": reason,
        }
        return HookOutcome(
            deny_reason=f"Error: Scheduled agent paused for approval. {reason}"
        )


def _find_capability(
    capabilities: Any,
    tool_name: str,
) -> dict[str, Any] | None:
    if not isinstance(capabilities, list):
        return None
    for capability in capabilities:
        if (
            isinstance(capability, dict)
            and capability.get("tool") == tool_name
        ):
            return capability
    return None


def _positive_int(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback
