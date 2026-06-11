from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any


DEFAULT_TOTAL_BUDGET_CHARS = 24000


@dataclass(frozen=True)
class SectionBudgetRule:
    name: str
    budget_chars: int
    floor_chars: int
    strategy: str


@dataclass(frozen=True)
class BudgetedText:
    name: str
    raw_text: str
    rendered_text: str
    budget_chars: int | None = None
    floor_chars: int = 0
    strategy: str = "none"
    truncated: bool = False
    reduction: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextBudgeter:
    def __init__(
        self,
        *,
        enabled: bool = False,
        total_budget_chars: int | None = None,
        rules: dict[str, SectionBudgetRule] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.total_budget_chars = total_budget_chars
        self.rules = dict(rules or {})

    @classmethod
    def from_env(cls) -> "ContextBudgeter":
        enabled = _env_bool("CONTEXT_ENABLE_SECTION_BUDGET", default=False)
        total_budget = _env_int("CONTEXT_BUDGET_CHARS", DEFAULT_TOTAL_BUDGET_CHARS)
        return cls(
            enabled=enabled,
            total_budget_chars=total_budget,
            rules={
                "mode_instructions": SectionBudgetRule(
                    name="mode_instructions",
                    budget_chars=_env_int("CONTEXT_MODE_INSTRUCTIONS_BUDGET", 3000),
                    floor_chars=_env_int("CONTEXT_MODE_INSTRUCTIONS_FLOOR", 1000),
                    strategy="head",
                ),
                "project_instructions": SectionBudgetRule(
                    name="project_instructions",
                    budget_chars=_env_int("CONTEXT_PROJECT_INSTRUCTIONS_BUDGET", 3000),
                    floor_chars=_env_int("CONTEXT_PROJECT_INSTRUCTIONS_FLOOR", 1000),
                    strategy="head",
                ),
                "memory": SectionBudgetRule(
                    name="memory",
                    budget_chars=_env_int("CONTEXT_MEMORY_BUDGET", 2500),
                    floor_chars=_env_int("CONTEXT_MEMORY_FLOOR", 500),
                    strategy="head_tail",
                ),
                "task_runtime_events": SectionBudgetRule(
                    name="task_runtime_events",
                    budget_chars=_env_int("CONTEXT_TASK_RUNTIME_EVENTS_BUDGET", 2000),
                    floor_chars=_env_int("CONTEXT_TASK_RUNTIME_EVENTS_FLOOR", 300),
                    strategy="tail",
                ),
            },
        )

    def apply(self, name: str, text: str, *, raw_text: str | None = None) -> BudgetedText:
        raw = str(text if raw_text is None else raw_text or "")
        rendered = str(text or "")
        rule = self.rules.get(name)
        if rule is None:
            return BudgetedText(name=name, raw_text=raw, rendered_text=rendered)

        target = max(1, int(rule.budget_chars))
        floor = max(0, int(rule.floor_chars))
        metadata = {
            "budget_enabled": self.enabled,
            "strategy": rule.strategy,
            "floor_chars": floor,
        }

        if not self.enabled:
            return BudgetedText(
                name=name,
                raw_text=raw,
                rendered_text=rendered,
                budget_chars=None,
                floor_chars=floor,
                strategy=rule.strategy,
                truncated=False,
                metadata=metadata,
            )

        if len(rendered) <= target:
            return BudgetedText(
                name=name,
                raw_text=raw,
                rendered_text=rendered,
                budget_chars=target,
                floor_chars=floor,
                strategy=rule.strategy,
                truncated=False,
                metadata=metadata,
            )

        effective_target = max(target, min(floor, len(rendered)))
        clipped = _clip(rendered, effective_target, strategy=rule.strategy)
        reduction = {
            "section": name,
            "reason": "section_budget",
            "before_chars": len(rendered),
            "after_chars": len(clipped),
            "budget_chars": target,
            "floor_chars": floor,
            "effective_budget_chars": effective_target,
            "strategy": rule.strategy,
        }
        return BudgetedText(
            name=name,
            raw_text=raw,
            rendered_text=clipped,
            budget_chars=target,
            floor_chars=floor,
            strategy=rule.strategy,
            truncated=True,
            reduction=reduction,
            metadata=metadata,
        )


def _clip(text: str, limit: int, *, strategy: str) -> str:
    if len(text) <= limit:
        return text
    marker = "\n...[truncated]"
    if limit <= len(marker) + 8:
        return text[:limit]

    if strategy == "tail":
        keep = limit - len(marker)
        return marker + text[-keep:].lstrip()

    if strategy == "head_tail":
        marker = "\n...[truncated]...\n"
        keep = max(0, limit - len(marker))
        head = keep // 2
        tail = keep - head
        return text[:head].rstrip() + marker + text[-tail:].lstrip()

    keep = limit - len(marker)
    return text[:keep].rstrip() + marker


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return int(default)
    try:
        return int(value)
    except ValueError:
        return int(default)
