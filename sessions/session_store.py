import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id             TEXT PRIMARY KEY,
                    current_mode   TEXT NOT NULL,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    last_compacted TEXT,
                    metadata       TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    session_id   TEXT NOT NULL,
                    seq          INTEGER NOT NULL,
                    role         TEXT NOT NULL,
                    timestamp    TEXT,
                    message_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, seq),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            self._conn.commit()

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, current_mode, created_at, updated_at, last_compacted, metadata
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            msg_rows = self._conn.execute(
                """
                SELECT message_json
                FROM messages
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            ).fetchall()

        return {
            "id": row["id"],
            "current_mode": row["current_mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_compacted": row["last_compacted"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "messages": [
                json.loads(msg_row["message_json"])
                for msg_row in msg_rows
            ],
        }

    def save_session(self, session: Any) -> None:
        metadata_json = json.dumps(
            session.metadata or {},
            ensure_ascii=False,
            default=str,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    id, current_mode, created_at, updated_at, last_compacted, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_mode = excluded.current_mode,
                    updated_at = excluded.updated_at,
                    last_compacted = excluded.last_compacted,
                    metadata = excluded.metadata
                """,
                (
                    session.id,
                    session.current_mode,
                    session.created_at,
                    session.updated_at,
                    session.last_compacted,
                    metadata_json,
                ),
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session.id,),
            )
            self._conn.executemany(
                """
                INSERT INTO messages (session_id, seq, role, timestamp, message_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        session.id,
                        seq,
                        str(message.get("role", "")),
                        message.get("timestamp"),
                        json.dumps(message, ensure_ascii=False, default=str),
                    )
                    for seq, message in enumerate(session.messages)
                ],
            )
            self._conn.commit()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, current_mode, created_at, updated_at, last_compacted, metadata
                FROM sessions
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "current_mode": row["current_mode"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_compacted": row["last_compacted"],
                "metadata": json.loads(row["metadata"] or "{}"),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
