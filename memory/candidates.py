from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

from memory.dedup import normalize_memory_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryCandidate:
    id: str
    content: str
    type: str = "candidate"
    confidence: float = 0.5
    evidence_count: int = 1
    source_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_triggered_at: str = field(default_factory=_now_iso)
    status: str = "candidate"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemoryCandidate":
        return cls(
            id=str(payload.get("id") or ""),
            content=str(payload.get("content") or ""),
            type=str(payload.get("type") or "candidate"),
            confidence=float(payload.get("confidence") or 0.5),
            evidence_count=int(payload.get("evidence_count") or 1),
            source_refs=[
                str(item)
                for item in (payload.get("source_refs") or [])
                if str(item).strip()
            ],
            created_at=str(payload.get("created_at") or _now_iso()),
            updated_at=str(payload.get("updated_at") or _now_iso()),
            last_triggered_at=str(payload.get("last_triggered_at") or _now_iso()),
            status=str(payload.get("status") or "candidate"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CandidateMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db_path = path.with_suffix(".db")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                '{\n  "version": 1,\n  "candidates": []\n}\n',
                encoding="utf-8",
            )
        self._init_schema()
        self._migrate_json_if_needed()

    def read(self) -> list[MemoryCandidate]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM candidates
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def write(self, candidates: list[MemoryCandidate]) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM candidates")
            for candidate in candidates:
                self._upsert_candidate_row(conn, candidate)
            conn.commit()
        self._write_json_mirror(candidates)

    def upsert(
        self,
        *,
        content: str,
        type: str = "candidate",
        confidence: float = 0.5,
        source_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[MemoryCandidate, bool]:
        text = content.strip()
        now = _now_iso()
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            match = self._find_exact_row(conn, text)
            if match is None:
                candidate = MemoryCandidate(
                    id=self._next_id(conn),
                    content=text,
                    type=type,
                    confidence=max(0.0, min(1.0, float(confidence))),
                    evidence_count=1,
                    source_refs=[source_ref] if source_ref else [],
                    created_at=now,
                    updated_at=now,
                    last_triggered_at=now,
                    metadata=dict(metadata or {}),
                )
                self._upsert_candidate_row(conn, candidate)
                conn.commit()
                created = True
            else:
                candidate = self._candidate_from_row(match)
                candidate.evidence_count += 1
                candidate.confidence = max(candidate.confidence, min(1.0, float(confidence)))
                candidate.confidence = min(1.0, candidate.confidence + 0.08)
                candidate.updated_at = now
                candidate.last_triggered_at = now
                if source_ref and source_ref not in candidate.source_refs:
                    candidate.source_refs.append(source_ref)
                candidate.metadata.update(metadata or {})
                self._upsert_candidate_row(conn, candidate)
                conn.commit()
                created = False
        self._write_json_mirror(self.read())
        return candidate, created

    def trigger_related(
        self,
        text: str,
        *,
        source_ref: str = "",
        min_overlap: int = 2,
    ) -> list[MemoryCandidate]:
        tokens = _tokens(text)
        normalized_text = normalize_memory_text(text)
        if not tokens and not normalized_text:
            return []
        candidates = self.read()
        triggered = []
        now = _now_iso()
        for candidate in candidates:
            if candidate.status != "candidate":
                continue
            overlap = tokens.intersection(_tokens(candidate.content))
            related_by_text = _text_related(normalized_text, candidate.content)
            if len(overlap) < min_overlap and not related_by_text:
                continue
            candidate.evidence_count += 1
            candidate.confidence = min(1.0, candidate.confidence + 0.06)
            candidate.updated_at = now
            candidate.last_triggered_at = now
            if source_ref and source_ref not in candidate.source_refs:
                candidate.source_refs.append(source_ref)
            triggered.append(candidate)
        if triggered:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                for candidate in triggered:
                    self._upsert_candidate_row(conn, candidate)
                conn.commit()
            self._write_json_mirror(self.read())
        return triggered

    def mark_promoted(self, ids: set[str]) -> None:
        now = _now_iso()
        with self._lock, sqlite3.connect(self.db_path) as conn:
            for candidate_id in ids:
                conn.execute(
                    """
                    UPDATE candidates
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("promoted", now, candidate_id),
                )
            conn.commit()
        self._write_json_mirror(self.read())

    def _find_exact(
        self,
        candidates: list[MemoryCandidate],
        content: str,
    ) -> MemoryCandidate | None:
        normalized = normalize_memory_text(content)
        for candidate in candidates:
            if normalize_memory_text(candidate.content) == normalized:
                return candidate
        return None

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    source_refs TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_triggered_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_status_updated
                ON candidates(status, updated_at DESC)
                """
            )
            conn.commit()

    def _migrate_json_if_needed(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        items = payload.get("candidates", []) if isinstance(payload, dict) else []
        if not items:
            return
        with self._lock, sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            if count:
                return
            for item in items:
                if isinstance(item, dict) and str(item.get("content") or "").strip():
                    self._upsert_candidate_row(conn, MemoryCandidate.from_dict(item))
            conn.commit()
        self._write_json_mirror(self.read())

    def _candidate_from_row(self, row) -> MemoryCandidate:
        return MemoryCandidate(
            id=str(row["id"]),
            content=str(row["content"]),
            type=str(row["type"]),
            confidence=float(row["confidence"]),
            evidence_count=int(row["evidence_count"]),
            source_refs=json.loads(row["source_refs"] or "[]"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_triggered_at=str(row["last_triggered_at"]),
            status=str(row["status"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def _find_exact_row(self, conn, content: str):
        return conn.execute(
            """
            SELECT *
            FROM candidates
            WHERE normalized_content = ?
            """,
            (normalize_memory_text(content),),
        ).fetchone()

    def _upsert_candidate_row(self, conn, candidate: MemoryCandidate) -> None:
        conn.execute(
            """
            INSERT INTO candidates (
                id,
                content,
                normalized_content,
                type,
                confidence,
                evidence_count,
                source_refs,
                created_at,
                updated_at,
                last_triggered_at,
                status,
                metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                normalized_content = excluded.normalized_content,
                type = excluded.type,
                confidence = excluded.confidence,
                evidence_count = excluded.evidence_count,
                source_refs = excluded.source_refs,
                updated_at = excluded.updated_at,
                last_triggered_at = excluded.last_triggered_at,
                status = excluded.status,
                metadata = excluded.metadata
            """,
            (
                candidate.id,
                candidate.content,
                normalize_memory_text(candidate.content),
                candidate.type,
                candidate.confidence,
                candidate.evidence_count,
                json.dumps(candidate.source_refs, ensure_ascii=False),
                candidate.created_at,
                candidate.updated_at,
                candidate.last_triggered_at,
                candidate.status,
                json.dumps(candidate.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _next_id(self, conn) -> str:
        rows = conn.execute("SELECT id FROM candidates").fetchall()
        existing = {str(row[0]) for row in rows}
        index = len(existing) + 1
        while True:
            candidate_id = f"mem_cand_{index:04d}"
            if candidate_id not in existing:
                return candidate_id
            index += 1

    def _write_json_mirror(self, candidates: list[MemoryCandidate]) -> None:
        payload = {
            "version": 1,
            "backend": "sqlite",
            "database": str(self.db_path),
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _tokens(text: str) -> set[str]:
    normalized = normalize_memory_text(text)
    raw_tokens = normalized.replace("_", " ").replace("-", " ").split()
    tokens = {
        token
        for token in raw_tokens
        if len(token) >= 2 and token not in {"用户", "这个", "一个", "需要"}
    }
    for keyword in _memory_keywords():
        if keyword in normalized:
            tokens.add(keyword)
    return tokens


def _text_related(normalized_text: str, candidate_content: str) -> bool:
    normalized_candidate = normalize_memory_text(candidate_content)
    if not normalized_text or not normalized_candidate:
        return False
    if normalized_text in normalized_candidate or normalized_candidate in normalized_text:
        return True
    shared_keywords = [
        keyword
        for keyword in _memory_keywords()
        if keyword in normalized_text and keyword in normalized_candidate
    ]
    return len(shared_keywords) >= 1


_DEFAULT_MEMORY_KEYWORDS = {
    "pytest",
    "测试",
    "代码风格",
    "项目",
    "偏好",
    "喜欢",
    "不喜欢",
    "真实",
    "搜索",
    "人物细节",
    "同人文",
    "prefer",
    "preference",
}


def _memory_keywords() -> set[str]:
    raw = os.getenv("MEMORY_CANDIDATE_KEYWORDS", "").strip()
    if not raw:
        return set(_DEFAULT_MEMORY_KEYWORDS)
    configured = {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }
    return configured | set(_DEFAULT_MEMORY_KEYWORDS)
