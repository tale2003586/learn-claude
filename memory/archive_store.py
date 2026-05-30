import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import WORKDIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ArchivedRecentTurn:
    session_id: str
    mode: str
    user_text: str
    assistant_summary: str
    source_ref: str
    created_at: str
    archived_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryArchiveStore:
    """SQLite archive for turns evicted from the rolling recent context."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or WORKDIR / ".sessions" / "sessions.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_summary TEXT NOT NULL,
                    source_ref TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_archive_session_created
                ON memory_archive(session_id, created_at DESC)
                """
            )
            conn.commit()

    def append(self, turn: ArchivedRecentTurn) -> bool:
        payload = asdict(turn)
        payload["metadata"] = json.dumps(
            payload["metadata"], ensure_ascii=False, sort_keys=True
        )
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO memory_archive (
                    session_id,
                    mode,
                    user_text,
                    assistant_summary,
                    source_ref,
                    created_at,
                    archived_at,
                    metadata
                ) VALUES (
                    :session_id,
                    :mode,
                    :user_text,
                    :assistant_summary,
                    :source_ref,
                    :created_at,
                    :archived_at,
                    :metadata
                )
                """,
                payload,
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_recent(
        self, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT
                id,
                session_id,
                mode,
                user_text,
                assistant_summary,
                source_ref,
                created_at,
                archived_at,
                metadata
            FROM memory_archive
        """
        params: list[Any] = []
        if session_id:
            sql += " WHERE session_id = ?"
            params.append(session_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, limit))

        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                **dict(row),
                "metadata": json.loads(row["metadata"] or "{}"),
            }
            for row in rows
        ]
