import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.scheduler.workflow import (
    build_search_workflow,
    primary_search_step,
    validate_workflow,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(raw_value: Any, *, fallback: Any, expected_type: type) -> Any:
    if not raw_value:
        return fallback
    try:
        value = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, expected_type) else fallback


class ScheduleStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or Path.cwd() / ".scheduler" / "schedules.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    name             TEXT NOT NULL UNIQUE,
                    query            TEXT NOT NULL,
                    topic            TEXT NOT NULL DEFAULT 'news',
                    max_results      INTEGER NOT NULL DEFAULT 5,
                    time_range       TEXT,
                    hour             INTEGER NOT NULL,
                    minute           INTEGER NOT NULL DEFAULT 0,
                    timezone         TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    enabled          INTEGER NOT NULL DEFAULT 1,
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL,
                    last_run_at      TEXT,
                    last_status      TEXT,
                    last_report_path TEXT,
                    last_error       TEXT,
                    workflow_json    TEXT,
                    schedule_type    TEXT NOT NULL DEFAULT 'workflow',
                    task_prompt      TEXT,
                    approval_status  TEXT NOT NULL DEFAULT 'active',
                    requested_tools_json       TEXT NOT NULL DEFAULT '[]',
                    approved_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    limits_json                TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    name        TEXT NOT NULL,
                    started_at  TEXT NOT NULL,
                    finished_at TEXT,
                    status      TEXT NOT NULL,
                    report_path TEXT,
                    error       TEXT,
                    task_session_id      TEXT,
                    trace_path           TEXT,
                    approval_request_json TEXT
                )
                """
            )
            self._ensure_column(conn, "schedules", "workflow_json", "TEXT")
            self._ensure_column(
                conn,
                "schedules",
                "schedule_type",
                "TEXT NOT NULL DEFAULT 'workflow'",
            )
            self._ensure_column(conn, "schedules", "task_prompt", "TEXT")
            self._ensure_column(
                conn,
                "schedules",
                "approval_status",
                "TEXT NOT NULL DEFAULT 'active'",
            )
            self._ensure_column(
                conn,
                "schedules",
                "requested_tools_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "schedules",
                "approved_capabilities_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "schedules",
                "limits_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(conn, "schedule_runs", "task_session_id", "TEXT")
            self._ensure_column(conn, "schedule_runs", "trace_path", "TEXT")
            self._ensure_column(
                conn,
                "schedule_runs",
                "approval_request_json",
                "TEXT",
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule_started
                ON schedule_runs(schedule_id, started_at DESC)
                """
            )
            conn.commit()

    def create(
        self,
        *,
        name: str,
        query: str,
        hour: int,
        minute: int = 0,
        timezone_name: str = "Asia/Shanghai",
        topic: str = "news",
        max_results: int = 5,
        time_range: str | None = "day",
    ) -> dict[str, Any]:
        workflow = build_search_workflow(
            query=query,
            topic=topic,
            max_results=max_results,
            time_range=time_range,
        )
        return self.create_workflow(
            name=name,
            workflow=workflow,
            hour=hour,
            minute=minute,
            timezone_name=timezone_name,
        )

    def create_workflow(
        self,
        *,
        name: str,
        workflow: list[dict[str, Any]],
        hour: int,
        minute: int = 0,
        timezone_name: str = "Asia/Shanghai",
    ) -> dict[str, Any]:
        workflow = validate_workflow(workflow)
        search = primary_search_step(workflow)
        values = self._validated_values(
            name=name,
            query=search["query"],
            hour=hour,
            minute=minute,
            timezone_name=timezone_name,
            topic=search["topic"],
            max_results=search["max_results"],
            time_range=search.get("time_range"),
        )
        now = _now_iso()
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO schedules (
                    name,
                    query,
                    topic,
                    max_results,
                    time_range,
                    hour,
                    minute,
                    timezone,
                    enabled,
                    created_at,
                    updated_at,
                    workflow_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    values["name"],
                    values["query"],
                    values["topic"],
                    values["max_results"],
                    values["time_range"],
                    values["hour"],
                    values["minute"],
                    values["timezone"],
                    now,
                    now,
                    json.dumps(workflow, ensure_ascii=False),
                ),
            )
            conn.commit()
            schedule_id = cursor.lastrowid
        return self.get(schedule_id)

    def list_schedules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id ASC"
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql).fetchall()
        return [self._schedule_row(row) for row in rows]

    def get(self, schedule_id: int) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id = ?",
                (int(schedule_id),),
            ).fetchone()
        if row is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        return self._schedule_row(row)

    def delete(self, schedule_id: int) -> bool:
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM schedules WHERE id = ?",
                (int(schedule_id),),
            )
            conn.commit()
        return cursor.rowcount > 0

    def begin_run(
        self,
        schedule: dict[str, Any],
        *,
        task_session_id: str = "",
        trace_path: str = "",
        approval_request: dict[str, Any] | None = None,
    ) -> int:
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO schedule_runs (
                    schedule_id,
                    name,
                    started_at,
                    status,
                    task_session_id,
                    trace_path,
                    approval_request_json
                )
                VALUES (?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    schedule["id"],
                    schedule["name"],
                    _now_iso(),
                    task_session_id or None,
                    trace_path or None,
                    _optional_json(approval_request),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def complete_run(
        self,
        *,
        run_id: int,
        schedule_id: int,
        status: str,
        report_path: str = "",
        error: str = "",
        task_session_id: str = "",
        trace_path: str = "",
        approval_request: dict[str, Any] | None = None,
    ) -> None:
        finished_at = _now_iso()
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE schedule_runs
                SET
                    finished_at = ?,
                    status = ?,
                    report_path = ?,
                    error = ?,
                    task_session_id = COALESCE(?, task_session_id),
                    trace_path = COALESCE(?, trace_path),
                    approval_request_json = COALESCE(?, approval_request_json)
                WHERE id = ?
                """,
                (
                    finished_at,
                    status,
                    report_path or None,
                    error or None,
                    task_session_id or None,
                    trace_path or None,
                    _optional_json(approval_request),
                    int(run_id),
                ),
            )
            conn.execute(
                """
                UPDATE schedules
                SET
                    last_run_at = ?,
                    last_status = ?,
                    last_report_path = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    status,
                    report_path or None,
                    error or None,
                    finished_at,
                    int(schedule_id),
                ),
            )
            conn.commit()

    def list_runs(
        self,
        *,
        schedule_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedule_runs"
        params: list[Any] = []
        if schedule_id is not None:
            sql += " WHERE schedule_id = ?"
            params.append(int(schedule_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 50)))
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._run_row(row) for row in rows]

    def _validated_values(
        self,
        *,
        name: str,
        query: str,
        hour: int,
        minute: int,
        timezone_name: str,
        topic: str,
        max_results: int,
        time_range: str | None,
    ) -> dict[str, Any]:
        name = name.strip()
        query = query.strip()
        if not name:
            raise ValueError("Schedule name is required.")
        if not query:
            raise ValueError("Search query is required.")
        hour = int(hour)
        minute = int(minute)
        if hour < 0 or hour > 23:
            raise ValueError("hour must be between 0 and 23.")
        if minute < 0 or minute > 59:
            raise ValueError("minute must be between 0 and 59.")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        if topic not in {"general", "news", "finance"}:
            raise ValueError("topic must be general, news, or finance.")
        if time_range not in {None, "", "day", "week", "month", "year"}:
            raise ValueError("time_range must be day, week, month, or year.")
        return {
            "name": name,
            "query": query,
            "hour": hour,
            "minute": minute,
            "timezone": timezone_name,
            "topic": topic,
            "max_results": max(1, min(int(max_results), 8)),
            "time_range": time_range or None,
        }

    def _schedule_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        raw_workflow = data.pop("workflow_json", None)
        if raw_workflow:
            data["workflow"] = validate_workflow(json.loads(raw_workflow))
        else:
            data["workflow"] = build_search_workflow(
                query=data["query"],
                topic=data["topic"],
                max_results=data["max_results"],
                time_range=data["time_range"],
            )
        data["schedule_type"] = data.get("schedule_type") or "workflow"
        data["approval_status"] = data.get("approval_status") or "active"
        data["requested_tools"] = _json_value(
            data.pop("requested_tools_json", None),
            fallback=[],
            expected_type=list,
        )
        data["approved_capabilities"] = _json_value(
            data.pop("approved_capabilities_json", None),
            fallback=[],
            expected_type=list,
        )
        data["limits"] = _json_value(
            data.pop("limits_json", None),
            fallback={},
            expected_type=dict,
        )
        return data

    def _run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["approval_request"] = _json_value(
            data.pop("approval_request_json", None),
            fallback=None,
            expected_type=dict,
        )
        return data

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)
