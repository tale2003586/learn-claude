import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_LIMITS = {
    "max_reasoning_steps": 12,
    "max_tool_calls": 16,
    "timeout_seconds": 300,
}
LIMIT_BOUNDS = {
    "max_reasoning_steps": (1, 30),
    "max_tool_calls": (1, 50),
    "timeout_seconds": (30, 1800),
}
FORBIDDEN_AUTOMATION_TOOLS = {
    "broadcast",
    "claim_task",
    "compact",
    "idle",
    "list_teammates",
    "plan_approval",
    "plan_approval_request",
    "read_inbox",
    "send_message",
    "shutdown_request",
    "shutdown_response",
    "shutdown_status",
    "spawn_teammate",
    "task",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "tool_search",
}
COMMAND_SCOPED_TOOLS = {
    "background_run",
    "bash",
}
PATH_SCOPED_TOOLS = {
    "edit_file",
    "read_file",
    "write_file",
}


class TaskPlanningClient(Protocol):
    def plan(
        self,
        *,
        task_prompt: str,
        tool_catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ScheduledTaskPlan:
    task_prompt: str
    summary: str
    requested_tools: list[str]
    limits: dict[str, int]
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_prompt": self.task_prompt,
            "summary": self.summary,
            "requested_tools": list(self.requested_tools),
            "limits": dict(self.limits),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CapabilityDecision:
    tool: str
    risk: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "tool": self.tool,
            "risk": self.risk,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass
class CapabilityAudit:
    requested_tools: list[str]
    auto_approved: list[CapabilityDecision] = field(default_factory=list)
    requires_approval: list[CapabilityDecision] = field(default_factory=list)
    forbidden: list[CapabilityDecision] = field(default_factory=list)
    unknown: list[CapabilityDecision] = field(default_factory=list)

    @property
    def approval_status(self) -> str:
        if self.forbidden or self.unknown:
            return "blocked"
        if self.requires_approval:
            return "awaiting_approval"
        return "active"

    @property
    def approved_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "tool": item.tool,
                "risk": item.risk,
                "scope": {},
            }
            for item in self.auto_approved
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_status": self.approval_status,
            "requested_tools": list(self.requested_tools),
            "approved_capabilities": self.approved_capabilities,
            "auto_approved": [item.to_dict() for item in self.auto_approved],
            "requires_approval": [
                item.to_dict() for item in self.requires_approval
            ],
            "forbidden": [item.to_dict() for item in self.forbidden],
            "unknown": [item.to_dict() for item in self.unknown],
        }


@dataclass(frozen=True)
class ScheduledTaskDraft:
    plan: ScheduledTaskPlan
    audit: CapabilityAudit

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "audit": self.audit.to_dict(),
        }


class ToolCapabilityAuditor:
    def __init__(self, tool_registry, *, mode: str | None = None) -> None:
        self.tool_registry = tool_registry
        self.mode = mode

    def catalog(self) -> list[dict[str, Any]]:
        return self.tool_registry.catalog(mode=self.mode)

    def planning_catalog(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.catalog()
            if not is_forbidden_automation_tool(item["name"])
        ]

    def audit(self, requested_tools: list[str]) -> CapabilityAudit:
        normalized = _normalize_tool_names(requested_tools)
        catalog = {item["name"]: item for item in self.catalog()}
        audit = CapabilityAudit(requested_tools=normalized)

        for name in normalized:
            item = catalog.get(name)
            if item is None:
                audit.unknown.append(CapabilityDecision(
                    tool=name,
                    risk="unknown",
                    source="unknown",
                    reason="Tool is not registered.",
                ))
                continue

            risk = _normalize_risk(item.get("risk"))
            decision = CapabilityDecision(
                tool=name,
                risk=risk,
                source=str(item.get("source") or "local"),
                reason="",
            )
            if is_forbidden_automation_tool(name):
                audit.forbidden.append(CapabilityDecision(
                    tool=name,
                    risk=risk,
                    source=decision.source,
                    reason="Tool is forbidden for unattended automation.",
                ))
                continue
            if risk == "low":
                audit.auto_approved.append(CapabilityDecision(
                    tool=name,
                    risk=risk,
                    source=decision.source,
                    reason="Low-risk capability is auto-approved.",
                ))
                continue
            audit.requires_approval.append(CapabilityDecision(
                tool=name,
                risk=risk,
                source=decision.source,
                reason=f"{risk.title()}-risk capability requires user approval.",
            ))

        return audit


class LLMTaskPlanningClient:
    def __init__(self, *, provider=None, model: str | None = None) -> None:
        self.provider = provider
        self.model = model

    def plan(
        self,
        *,
        task_prompt: str,
        tool_catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        provider, model = self._provider_and_model()
        catalog_text = json.dumps(tool_catalog, ensure_ascii=False, indent=2)[:30000]
        response = provider.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You plan unattended scheduled agent tasks. Return one JSON "
                        "object only. Select the minimum registered tools needed for "
                        "the task. Never invent tools. Report persistence is handled "
                        "by the scheduler, so do not request a report-writing tool. "
                        "Return keys: summary, requested_tools, limits, rationale. "
                        "limits may contain max_reasoning_steps, max_tool_calls, and "
                        "timeout_seconds."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Scheduled task:\n{task_prompt}\n\n"
                        f"Available tools:\n{catalog_text}"
                    ),
                },
            ],
            tools=[],
            tool_choice="none",
            max_tokens=max(
                300,
                int(os.environ.get("SCHEDULER_PLANNER_MAX_TOKENS", "1000")),
            ),
        )
        return _extract_json_object(response.content or "")

    def _provider_and_model(self):
        if self.provider is not None and self.model:
            return self.provider, self.model
        if self.provider is not None:
            from config import MODEL

            return self.provider, self.model or os.environ.get("SCHEDULER_PLANNER_MODEL") or MODEL

        from config import MODEL_POOL

        return (
            MODEL_POOL.routed_provider("scheduler_plan"),
            self.model
            or os.environ.get("SCHEDULER_PLANNER_MODEL")
            or MODEL_POOL.model_for("scheduler_plan"),
        )


class ScheduledTaskPlanner:
    def __init__(self, *, planning_client: TaskPlanningClient | None = None) -> None:
        self.planning_client = planning_client or LLMTaskPlanningClient()

    def create_draft(
        self,
        *,
        task_prompt: str,
        auditor: ToolCapabilityAuditor,
    ) -> ScheduledTaskDraft:
        task_prompt = _normalize_task_prompt(task_prompt)
        raw_plan = self.planning_client.plan(
            task_prompt=task_prompt,
            tool_catalog=auditor.planning_catalog(),
        )
        plan = _normalize_plan(task_prompt, raw_plan)
        return ScheduledTaskDraft(
            plan=plan,
            audit=auditor.audit(plan.requested_tools),
        )


def merge_approved_capabilities(
    audit: CapabilityAudit,
    submitted_capabilities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if audit.forbidden or audit.unknown:
        raise ValueError("Blocked task plans cannot be approved.")
    if not isinstance(submitted_capabilities, list):
        raise ValueError("capabilities must be a list.")

    required = {item.tool: item for item in audit.requires_approval}
    approved = {
        item["tool"]: item
        for item in audit.approved_capabilities
    }
    for raw_capability in submitted_capabilities:
        capability = _normalize_submitted_capability(raw_capability)
        name = capability["tool"]
        if name not in required:
            raise ValueError(f"Tool does not require approval or was not requested: {name}")
        _validate_approval_scope(name, required[name].risk, capability["scope"])
        capability["risk"] = required[name].risk
        approved[name] = capability

    missing = sorted(set(required) - set(approved))
    status = "awaiting_approval" if missing else "active"
    return [approved[name] for name in sorted(approved)], status


def capability_allows(
    capability: dict[str, Any],
    arguments: dict[str, Any],
) -> bool:
    name = str(capability.get("tool", "")).strip()
    scope = capability.get("scope")
    scope = scope if isinstance(scope, dict) else {}

    if name in COMMAND_SCOPED_TOOLS:
        command = str(arguments.get("command", "")).strip()
        return command in scope.get("commands", [])
    if name in PATH_SCOPED_TOOLS:
        path = _normalize_relative_path(arguments.get("path"))
        return any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in scope.get("paths", [])
        )
    return True


def _normalize_task_prompt(task_prompt: str) -> str:
    task_prompt = str(task_prompt).strip()
    if not task_prompt:
        raise ValueError("task_prompt is required.")
    if len(task_prompt) > 4000:
        raise ValueError("task_prompt is too long.")
    return task_prompt


def _normalize_plan(
    task_prompt: str,
    raw_plan: dict[str, Any],
) -> ScheduledTaskPlan:
    if not isinstance(raw_plan, dict):
        raise ValueError("Planner response must be an object.")
    summary = str(raw_plan.get("summary", "")).strip()
    if not summary:
        summary = task_prompt[:200]
    if len(summary) > 500:
        summary = summary[:500]
    rationale = str(raw_plan.get("rationale", "")).strip()[:2000]
    return ScheduledTaskPlan(
        task_prompt=task_prompt,
        summary=summary,
        requested_tools=_normalize_tool_names(raw_plan.get("requested_tools", [])),
        limits=_normalize_limits(raw_plan.get("limits")),
        rationale=rationale,
    )


def _normalize_tool_names(raw_tools: Any) -> list[str]:
    if not isinstance(raw_tools, list):
        raise ValueError("requested_tools must be a list.")
    names = []
    seen = set()
    for raw_name in raw_tools:
        name = str(raw_name).strip()
        if not name or name in seen:
            continue
        if len(name) > 100:
            raise ValueError("Tool name is too long.")
        seen.add(name)
        names.append(name)
    if len(names) > 20:
        raise ValueError("requested_tools may contain at most 20 tools.")
    return names


def _normalize_limits(raw_limits: Any) -> dict[str, int]:
    raw_limits = raw_limits if isinstance(raw_limits, dict) else {}
    limits = {}
    for name, default_value in DEFAULT_LIMITS.items():
        lower, upper = LIMIT_BOUNDS[name]
        try:
            value = int(raw_limits.get(name, default_value))
        except (TypeError, ValueError):
            value = default_value
        limits[name] = max(lower, min(value, upper))
    return limits


def _normalize_risk(raw_risk: Any) -> str:
    risk = str(raw_risk or "normal").strip().lower()
    return risk if risk in {"low", "normal", "high"} else "normal"


def is_forbidden_automation_tool(name: str) -> bool:
    return name.startswith("schedule_") or name in FORBIDDEN_AUTOMATION_TOOLS


def _normalize_submitted_capability(raw_capability: Any) -> dict[str, Any]:
    if not isinstance(raw_capability, dict):
        raise ValueError("Each capability must be an object.")
    name = str(raw_capability.get("tool", "")).strip()
    if not name:
        raise ValueError("Capability tool is required.")
    scope = raw_capability.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError(f"Capability scope must be an object: {name}")
    normalized_scope = {}
    if name in COMMAND_SCOPED_TOOLS:
        normalized_scope["commands"] = _normalize_string_list(
            scope.get("commands"),
            label=f"{name} scope.commands",
        )
    elif name in PATH_SCOPED_TOOLS:
        normalized_scope["paths"] = [
            _normalize_relative_path(path)
            for path in _normalize_string_list(
                scope.get("paths"),
                label=f"{name} scope.paths",
            )
        ]
    else:
        normalized_scope = scope
    return {
        "tool": name,
        "risk": "normal",
        "scope": normalized_scope,
    }


def _validate_approval_scope(tool: str, risk: str, scope: dict[str, Any]) -> None:
    if tool in COMMAND_SCOPED_TOOLS and not scope.get("commands"):
        raise ValueError(f"High-risk tool requires explicit commands: {tool}")
    if tool in PATH_SCOPED_TOOLS and not scope.get("paths"):
        raise ValueError(f"File tool requires explicit paths: {tool}")
    if risk != "high":
        return
    if tool not in COMMAND_SCOPED_TOOLS | PATH_SCOPED_TOOLS:
        raise ValueError(f"High-risk tool does not have a supported approval scope: {tool}")


def _normalize_string_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    items = []
    for raw_item in value:
        item = str(raw_item).strip()
        if item and item not in items:
            items.append(item)
    return items


def _normalize_relative_path(value: Any) -> str:
    parts = []
    for part in str(value or "").strip().replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("Capability path cannot escape the workspace.")
        parts.append(part)
    if not parts:
        raise ValueError("Capability path is required.")
    return "/".join(parts)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Planner response did not contain a JSON object.")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("Planner response contained invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Planner response must be a JSON object.")
    return value
