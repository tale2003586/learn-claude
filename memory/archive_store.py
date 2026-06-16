import json
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import WORKDIR
from runtime.db import connect, resolve_database_config, row_get, sql


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
    """Archive for turns evicted from the rolling recent context."""

    def __init__(self, db_path: Path | str | None = None):
        self.config = resolve_database_config(
            db_path,
            default_sqlite_path=WORKDIR / ".sessions" / "memory_archive.db",
            env_names=("MEMORY_ARCHIVE_DATABASE_URL", "SESSION_DATABASE_URL", "DATABASE_URL"),
        )
        self.db_path = self.config.sqlite_path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self):
        return connect(self.config)

    def _init_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            id_column = (
                "id BIGSERIAL PRIMARY KEY"
                if self.config.is_postgres
                else "id INTEGER PRIMARY KEY AUTOINCREMENT"
            )
            conn.execute(
                sql(self.config, f"""
                CREATE TABLE IF NOT EXISTS memory_archive (
                    {id_column},
                    session_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_summary TEXT NOT NULL,
                    source_ref TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{{}}'
                )
                """)
            )
            conn.execute(
                sql(self.config, """
                CREATE INDEX IF NOT EXISTS idx_memory_archive_session_created
                ON memory_archive(session_id, created_at DESC)
                """)
            )
            conn.commit()

    def append(self, turn: ArchivedRecentTurn) -> bool:
        payload = asdict(turn)
        payload["metadata"] = json.dumps(
            payload["metadata"], ensure_ascii=False, sort_keys=True
        )
        with self._lock, closing(self._connect()) as conn:
            insert_values = (
                payload["session_id"],
                payload["mode"],
                payload["user_text"],
                payload["assistant_summary"],
                payload["source_ref"],
                payload["created_at"],
                payload["archived_at"],
                payload["metadata"],
            )
            insert_sql = """
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
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
            """
            if self.config.is_postgres:
                insert_sql = insert_sql.replace("INSERT OR IGNORE", "INSERT").rstrip() + " ON CONFLICT (source_ref) DO NOTHING"
            cursor = conn.execute(
                sql(self.config, insert_sql),
                insert_values,
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_recent(
        self, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = """
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
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, limit))

        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql(self.config, query), params).fetchall()
        return [
            {
                **dict(row),
                "metadata": json.loads(row_get(row, "metadata", "{}") or "{}"),
            }
            for row in rows
        ]
