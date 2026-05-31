# session.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sessions.session_store import SessionStore


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
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._store = SessionStore(db_path or Path.cwd() / ".sessions" / "sessions.db")

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            loaded = self._store.load_session(session_id)
            if loaded is None:
                self._sessions[session_id] = Session(id=session_id)
            else:
                self._sessions[session_id] = Session(
                    id=loaded["id"],
                    messages=loaded["messages"],
                    current_mode=loaded["current_mode"],
                    created_at=loaded["created_at"],
                    updated_at=loaded["updated_at"],
                    last_compacted=loaded["last_compacted"],
                    metadata=loaded["metadata"],
                )
        return self._sessions[session_id]

    def save(self, session: Session) -> None:
        session.touch()
        self._sessions[session.id] = session
        self._store.save_session(session)

    def list_sessions(self) -> list[dict[str, Any]]:
        return self._store.list_sessions()

    def delete(self, session_id: str) -> bool:
        self._sessions.pop(session_id, None)
        return self._store.delete_session(session_id)

    def close(self) -> None:
        self._store.close()
