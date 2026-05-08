# session.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    current_mode: str = "hybrid"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_compacted: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: Any, **extra: Any) -> None:
        message = {
            "role": role,
            "content": content,
            "timestamp": _now_iso(),
        }
        message.update(extra)
        self.messages.append(message)
        self.touch()

    def set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.touch()

    def mark_compacted(self) -> None:
        self.last_compacted = _now_iso()
        self.touch()

    def touch(self) -> None:
        self.updated_at = _now_iso()


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    def save(self, session: Session) -> None:
        # Phase 1: memory only, no-op.
        pass
