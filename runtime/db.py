from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


POSTGRES_SCHEMES = ("postgres://", "postgresql://")


@dataclass(frozen=True)
class DatabaseConfig:
    kind: str
    sqlite_path: Path | None = None
    dsn: str = ""

    @property
    def is_postgres(self) -> bool:
        return self.kind == "postgres"


def resolve_database_config(
    explicit_path: str | Path | None,
    *,
    default_sqlite_path: str | Path,
    env_names: Iterable[str],
) -> DatabaseConfig:
    if explicit_path is not None:
        value = str(explicit_path)
        if _is_postgres_url(value):
            return DatabaseConfig(kind="postgres", dsn=value)
        return DatabaseConfig(kind="sqlite", sqlite_path=Path(explicit_path))

    for name in env_names:
        value = str(os.getenv(name) or "").strip()
        if not value:
            continue
        if _is_postgres_url(value):
            return DatabaseConfig(kind="postgres", dsn=value)
        return DatabaseConfig(kind="sqlite", sqlite_path=Path(value))

    return DatabaseConfig(kind="sqlite", sqlite_path=Path(default_sqlite_path))


def connect(config: DatabaseConfig):
    if config.is_postgres:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgreSQL DATABASE_URL. "
                "Install requirements.txt or use a sqlite database path."
            ) from exc
        return psycopg.connect(config.dsn, row_factory=dict_row)

    if config.sqlite_path is None:
        raise ValueError("sqlite_path is required for sqlite database config.")
    config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.sqlite_path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
    except sqlite3.DatabaseError:
        pass
    return conn


def sql(config: DatabaseConfig, statement: str) -> str:
    if config.is_postgres:
        return statement.replace("?", "%s")
    return statement


def execute_many(conn: Any, config: DatabaseConfig, statement: str, rows: Iterable[Iterable[Any]]) -> None:
    rendered = sql(config, statement)
    if config.is_postgres:
        with conn.cursor() as cursor:
            cursor.executemany(rendered, rows)
        return
    conn.executemany(rendered, rows)


def row_get(row: Any, key: str | int, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def is_integrity_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) or exc.__class__.__name__ == "UniqueViolation"


def _is_postgres_url(value: str) -> bool:
    return value.strip().lower().startswith(POSTGRES_SCHEMES)
