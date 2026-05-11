from dataclasses import dataclass

from memory.store import MemoryStore


@dataclass
class MemoryLifecycleResult:
    pending_added: int = 0
    history_updated: bool = False
    recent_context_updated: bool = False


class MemoryLifecycle:
    """Small Markdown memory lifecycle.

    First version:
    - explicit "remember" user requests go directly to MEMORY.md
    - likely durable preferences/project conventions go to PENDING.md
    - every completed turn appends a compact HISTORY.md entry
    - RECENT_CONTEXT.md keeps a tiny rolling summary of the latest turn
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

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

        history = self._format_history_entry(user_text, assistant_text)
        if history:
            self.store.append_history(history, source_ref=source_ref)
            result.history_updated = True

        recent = self._format_recent_context(session, user_text, assistant_text)
        self.store.write_recent_context(recent)
        result.recent_context_updated = True
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

    def _format_history_entry(self, user_text: str, assistant_text: str) -> str:
        parts = []
        if user_text:
            parts.append(f"USER: {self._trim(user_text, 800)}")
        if assistant_text:
            parts.append(f"ASSISTANT: {self._trim(assistant_text, 800)}")
        return "\n\n".join(parts)

    def _format_recent_context(self, session, user_text: str, assistant_text: str) -> str:
        lines = [
            f"- session: `{session.id}`",
            f"- mode: `{session.current_mode}`",
        ]
        if user_text:
            lines.append(f"- latest_user: {self._trim(user_text, 300)}")
        if assistant_text:
            lines.append(f"- latest_assistant: {self._trim(assistant_text, 300)}")
        return "\n".join(lines)

    def _trim(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + f"... ({len(text) - limit} chars omitted)"
