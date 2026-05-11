from dataclasses import dataclass, field

from memory.store import MemoryStore


@dataclass
class PromotionResult:
    promoted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class TaskMemoryPromoter:
    def __init__(self, global_memory: MemoryStore) -> None:
        self.global_memory = global_memory

    def promote(
        self,
        *,
        task_id: str,
        task_memory: MemoryStore,
        task_summary: str,
    ) -> PromotionResult:
        result = PromotionResult()
        candidates = self._collect_candidates(task_memory, task_summary)
        for candidate in candidates:
            save_result = self.global_memory.append_pending(
                candidate,
                tag="task",
                source_ref=f"task:{task_id}",
            )
            if save_result.startswith("Saved"):
                result.promoted.append(candidate)
            else:
                result.skipped.append(candidate)
        return result

    def _collect_candidates(self, task_memory: MemoryStore, task_summary: str) -> list[str]:
        candidates: list[str] = []
        candidates.extend(_bullet_items(task_memory.read_pending()))
        recent = task_memory.read_recent_context().strip()
        if recent:
            candidates.append("Task recent context: " + _trim(recent, 600))
        if task_summary.strip():
            candidates.append("Task summary: " + _trim(task_summary.strip(), 800))
        return _dedupe(candidates)


def _bullet_items(markdown: str) -> list[str]:
    items: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        stripped = stripped[1:].strip()
        if stripped:
            items.append(stripped)
    return items


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... ({len(text) - limit} chars omitted)"
