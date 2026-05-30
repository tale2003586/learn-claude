from dataclasses import dataclass
from datetime import datetime, timezone

from memory.archive_store import ArchivedRecentTurn, MemoryArchiveStore
from memory.history_summary import HistorySummarizer
from memory.store import MemoryStore


@dataclass
class MemoryLifecycleResult:
    pending_added: int = 0
    history_updated: bool = False
    recent_context_updated: bool = False
    archived_count: int = 0


class MemoryLifecycle:
    """Derived memory lifecycle layered on top of the raw session transcript."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        summarizer: HistorySummarizer | None = None,
        archive_store: MemoryArchiveStore | None = None,
        recent_limit: int = 6,
    ) -> None:
        self.store = store
        self.summarizer = summarizer or HistorySummarizer()
        self.archive_store = archive_store
        self.recent_limit = max(1, recent_limit)

    def after_turn(self, session) -> MemoryLifecycleResult:
        user_text = self._last_text(session.messages, "user")
        assistant_text = self._last_text(session.messages, "assistant")
        if not user_text and not assistant_text:
            return MemoryLifecycleResult()

        result = MemoryLifecycleResult()
        source_ref = f"{session.id}:{max(0, len(session.messages) - 1)}"

        explicit = self._extract_explicit_memory(user_text)
        if explicit:
            save_result = self.store.append("memory", explicit)
            if save_result.startswith("Saved"):
                result.pending_added += 1
        else:
            candidate = self._extract_candidate(user_text)
            if candidate:
                save_result = self.store.append_pending(
                    candidate,
                    tag=self._tag_for_candidate(candidate),
                    source_ref=source_ref,
                )
                if save_result.startswith("Saved"):
                    result.pending_added += 1

        assistant_summary = self.summarizer.summarize(assistant_text)
        history = self._format_history_entry(user_text, assistant_summary)
        if history:
            self.store.append_history(history, source_ref=source_ref)
            result.history_updated = True

        recent_turns = self.store.read_recent_turns()
        recent_turns.append(
            self._format_recent_turn(
                session,
                user_text,
                assistant_summary,
                source_ref=source_ref,
            )
        )
        evicted_turns = recent_turns[:-self.recent_limit]
        self.store.write_recent_turns(recent_turns[-self.recent_limit :])
        result.recent_context_updated = True

        if self.archive_store:
            for turn in evicted_turns:
                archived = self.archive_store.append(ArchivedRecentTurn(**turn))
                if archived:
                    result.archived_count += 1
        return result

    def _last_text(self, messages: list[dict], role: str) -> str:
        for message in reversed(messages):
            if message.get("role") != role:
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
        return ""

    def _extract_explicit_memory(self, text: str) -> str:
        markers = [
            "记住",
            "请记住",
            "帮我记住",
            "以后记得",
            "remember that",
            "please remember",
        ]
        lowered = text.lower()
        for marker in markers:
            idx = lowered.find(marker.lower())
            if idx >= 0:
                return text[idx + len(marker):].strip(" ：:，,。.\n")
        return ""

    def _extract_candidate(self, text: str) -> str:
        if not text:
            return ""
        keywords = [
            "我喜欢",
            "我不喜欢",
            "我希望",
            "我的偏好",
            "以后",
            "这个项目",
            "代码风格",
            "测试优先",
            "用 pytest",
            "prefer",
            "preference",
        ]
        if any(keyword in text.lower() for keyword in keywords):
            return text.strip()
        return ""

    def _tag_for_candidate(self, text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ["项目", "代码", "pytest", "测试", "风格"]):
            return "project"
        if any(word in lowered for word in ["喜欢", "偏好", "prefer", "preference"]):
            return "preference"
        return "candidate"

    def _format_history_entry(self, user_text: str, assistant_summary: str) -> str:
        parts = []
        if user_text:
            parts.append(f"USER:\n{user_text}")
        if assistant_summary:
            parts.append(f"ASSISTANT_SUMMARY:\n{assistant_summary}")
        return "\n\n".join(parts)

    def _format_recent_turn(
        self,
        session,
        user_text: str,
        assistant_summary: str,
        *,
        source_ref: str,
    ) -> dict:
        return {
            "session_id": session.id,
            "mode": session.current_mode,
            "user_text": user_text,
            "assistant_summary": assistant_summary,
            "source_ref": source_ref,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {},
        }

    def _trim(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + f"... ({len(text) - limit} chars omitted)"
