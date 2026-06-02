from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class TelegramGatewayStore:
    """Persists long-polling progress and per-chat active conversations."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or Path.cwd() / ".gateway" / "telegram.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_conversations (
                    external_chat_id TEXT PRIMARY KEY,
                    conversation_id  TEXT NOT NULL,
                    updated_at       TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def get_offset(self) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM telegram_state WHERE key = 'offset'"
            ).fetchone()
        return int(row[0]) if row is not None else None

    def set_offset(self, offset: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO telegram_state (key, value)
                VALUES ('offset', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(int(offset)),),
            )
            self._conn.commit()

    def get_conversation_id(self, external_chat_id: int | str) -> str:
        chat_id = str(external_chat_id)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT conversation_id
                FROM telegram_conversations
                WHERE external_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            if row is not None:
                return str(row[0])
            conversation_id = "default"
            self._save_conversation(chat_id, conversation_id)
        return conversation_id

    def start_conversation(self, external_chat_id: int | str) -> str:
        chat_id = str(external_chat_id)
        conversation_id = secrets.token_hex(6)
        with self._lock:
            self._save_conversation(chat_id, conversation_id)
        return conversation_id

    def runtime_chat_id(
        self,
        external_chat_id: int | str,
        *,
        user_id: str | None = None,
    ) -> str:
        return build_runtime_chat_id(
            external_chat_id,
            self.get_conversation_id(external_chat_id),
            user_id=user_id,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _save_conversation(self, chat_id: str, conversation_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO telegram_conversations (
                external_chat_id, conversation_id, updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(external_chat_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, conversation_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()


def build_runtime_chat_id(
    external_chat_id: int | str,
    conversation_id: str,
    *,
    user_id: str | None = None,
) -> str:
    suffix = f"_{user_id}" if user_id else ""
    return f"tg_{int(external_chat_id)}_{conversation_id}{suffix}"


def external_chat_id_from_runtime(value: str) -> int:
    parts = str(value or "").split("_", 2)
    if len(parts) != 3 or parts[0] != "tg":
        raise ValueError("Invalid Telegram runtime chat ID.")
    return int(parts[1])
