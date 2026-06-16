from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.feishu.store import FeishuGatewayStore
from gateway.telegram.store import TelegramGatewayStore
from memory.archive_store import MemoryArchiveStore
from sessions.session_store import SessionStore
from web.auth_store import WebAuthStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate local SQLite runtime databases into PostgreSQL."
    )
    parser.add_argument("--root", default=".", help="Project root containing .sessions/.users/.gateway.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL DSN, e.g. postgresql://agent:password@127.0.0.1:5432/agent_console",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    dsn = str(args.database_url or "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL or --database-url is required.")
    if not dsn.lower().startswith(("postgres://", "postgresql://")):
        raise SystemExit("--database-url must be a PostgreSQL URL.")

    _init_postgres_schema(dsn)

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is required. Run: pip install -r requirements.txt") from exc

    with psycopg.connect(dsn) as pg:
        _migrate_sessions(root / ".sessions" / "sessions.db", pg)
        _migrate_web_auth(root / ".users" / "auth.db", pg)
        _migrate_telegram(root / ".gateway" / "telegram.db", pg)
        _migrate_feishu(root / ".gateway" / "feishu.db", pg)
        _reset_sequences(pg)
        pg.commit()
    return 0


def _init_postgres_schema(dsn: str) -> None:
    stores = [
        SessionStore(dsn),
        MemoryArchiveStore(dsn),
        WebAuthStore(dsn),
        TelegramGatewayStore(dsn),
        FeishuGatewayStore(dsn),
    ]
    for store in stores:
        close = getattr(store, "close", None)
        if close is not None:
            close()


def _sqlite_rows(path: Path, query: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(row) for row in rows]


def _migrate_sessions(path: Path, pg) -> None:
    sessions = _sqlite_rows(
        path,
        """
        SELECT id, current_mode, created_at, updated_at, last_compacted, metadata
        FROM sessions
        """,
    )
    for row in sessions:
        pg.execute(
            """
            INSERT INTO sessions (id, current_mode, created_at, updated_at, last_compacted, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                current_mode = excluded.current_mode,
                updated_at = excluded.updated_at,
                last_compacted = excluded.last_compacted,
                metadata = excluded.metadata
            """,
            (
                row["id"],
                row["current_mode"],
                row["created_at"],
                row["updated_at"],
                row.get("last_compacted"),
                row.get("metadata") or "{}",
            ),
        )
        pg.execute("DELETE FROM messages WHERE session_id = %s", (row["id"],))

    messages = _sqlite_rows(
        path,
        """
        SELECT session_id, seq, role, timestamp, message_json
        FROM messages
        ORDER BY session_id, seq
        """,
    )
    for row in messages:
        pg.execute(
            """
            INSERT INTO messages (session_id, seq, role, timestamp, message_json)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(session_id, seq) DO UPDATE SET
                role = excluded.role,
                timestamp = excluded.timestamp,
                message_json = excluded.message_json
            """,
            (
                row["session_id"],
                row["seq"],
                row["role"],
                row.get("timestamp"),
                row["message_json"],
            ),
        )

    archive = _sqlite_rows(
        path,
        """
        SELECT session_id, mode, user_text, assistant_summary, source_ref,
               created_at, archived_at, metadata
        FROM memory_archive
        """,
    )
    for row in archive:
        pg.execute(
            """
            INSERT INTO memory_archive (
                session_id, mode, user_text, assistant_summary, source_ref,
                created_at, archived_at, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(source_ref) DO UPDATE SET
                session_id = excluded.session_id,
                mode = excluded.mode,
                user_text = excluded.user_text,
                assistant_summary = excluded.assistant_summary,
                created_at = excluded.created_at,
                archived_at = excluded.archived_at,
                metadata = excluded.metadata
            """,
            (
                row["session_id"],
                row["mode"],
                row["user_text"],
                row["assistant_summary"],
                row["source_ref"],
                row["created_at"],
                row["archived_at"],
                row.get("metadata") or "{}",
            ),
        )


def _migrate_web_auth(path: Path, pg) -> None:
    for row in _sqlite_rows(path, "SELECT * FROM web_users"):
        pg.execute(
            """
            INSERT INTO web_users (
                user_id, password_hash, salt, role, source, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(user_id) DO UPDATE SET
                password_hash = excluded.password_hash,
                salt = excluded.salt,
                role = excluded.role,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                row["user_id"],
                row["password_hash"],
                row["salt"],
                row["role"],
                row["source"],
                row["created_at"],
                row["updated_at"],
            ),
        )
    for row in _sqlite_rows(path, "SELECT * FROM web_auth_sessions"):
        pg.execute(
            """
            INSERT INTO web_auth_sessions (token_hash, user_id, created_at, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(token_hash) DO UPDATE SET
                user_id = excluded.user_id,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (row["token_hash"], row["user_id"], row["created_at"], row["expires_at"]),
        )


def _migrate_telegram(path: Path, pg) -> None:
    for row in _sqlite_rows(path, "SELECT * FROM telegram_state"):
        pg.execute(
            """
            INSERT INTO telegram_state (key, value)
            VALUES (%s, %s)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (row["key"], row["value"]),
        )
    for row in _sqlite_rows(path, "SELECT * FROM telegram_conversations"):
        pg.execute(
            """
            INSERT INTO telegram_conversations (external_chat_id, conversation_id, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT(external_chat_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                updated_at = excluded.updated_at
            """,
            (row["external_chat_id"], row["conversation_id"], row["updated_at"]),
        )
    _migrate_outbox(path, pg, "telegram_outbox")


def _migrate_feishu(path: Path, pg) -> None:
    for row in _sqlite_rows(path, "SELECT * FROM feishu_events"):
        pg.execute(
            """
            INSERT INTO feishu_events (event_id, created_at)
            VALUES (%s, %s)
            ON CONFLICT(event_id) DO UPDATE SET created_at = excluded.created_at
            """,
            (row["event_id"], row["created_at"]),
        )
    for row in _sqlite_rows(path, "SELECT * FROM feishu_conversations"):
        pg.execute(
            """
            INSERT INTO feishu_conversations (external_chat_id, conversation_id, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT(external_chat_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                updated_at = excluded.updated_at
            """,
            (row["external_chat_id"], row["conversation_id"], row["updated_at"]),
        )
    _migrate_outbox(path, pg, "feishu_outbox")


def _migrate_outbox(path: Path, pg, table: str) -> None:
    for row in _sqlite_rows(path, f"SELECT * FROM {table}"):
        pg.execute(
            f"""
            INSERT INTO {table} (
                id, chat_id, text, message_type, document_path, caption, status,
                attempts, source, metadata, error, created_at, updated_at, sent_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                chat_id = excluded.chat_id,
                text = excluded.text,
                message_type = excluded.message_type,
                document_path = excluded.document_path,
                caption = excluded.caption,
                status = excluded.status,
                attempts = excluded.attempts,
                source = excluded.source,
                metadata = excluded.metadata,
                error = excluded.error,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                sent_at = excluded.sent_at
            """,
            (
                row["id"],
                row["chat_id"],
                row["text"],
                row.get("message_type") or "text",
                row.get("document_path"),
                row.get("caption"),
                row.get("status") or "pending",
                row.get("attempts") or 0,
                row.get("source"),
                row.get("metadata") or "{}",
                row.get("error"),
                row["created_at"],
                row["updated_at"],
                row.get("sent_at"),
            ),
        )


def _reset_sequences(pg) -> None:
    for table in ("memory_archive", "telegram_outbox", "feishu_outbox"):
        pg.execute(
            """
            SELECT setval(
                pg_get_serial_sequence(%s, 'id'),
                GREATEST(COALESCE((SELECT MAX(id) FROM """ + table + """), 1), 1),
                (SELECT MAX(id) IS NOT NULL FROM """ + table + """)
            )
            """,
            (table,),
        )


if __name__ == "__main__":
    raise SystemExit(main())
