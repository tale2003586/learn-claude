import json
from pathlib import Path
from datetime import datetime, timezone
from config import WORKDIR
from memory.dedup import is_duplicate_memory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or WORKDIR / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.root / "MEMORY.md"
        self.self_path = self.root / "SELF.md"
        self.now_path = self.root / "NOW.md"
        self.pending_path = self.root / "PENDING.md"
        self.history_path = self.root / "HISTORY.md"
        self.recent_context_path = self.root / "RECENT_CONTEXT.md"
        self.recent_context_data_path = self.root / "RECENT_CONTEXT.json"
        self._ensure_files()

    def _ensure_files(self) -> None:
        defaults = {
            self.memory_path: "# Memory\n\n",
            self.self_path: "# Self\n\n",
            self.now_path: "# Now\n\n",
            self.pending_path: "# Pending Memory\n\n",
            self.history_path: "# History\n\n",
            self.recent_context_path: "# Recent Context\n\n",
            self.recent_context_data_path: '{\n  "version": 1,\n  "turns": []\n}\n',
        }
        for path, content in defaults.items():
            if not path.exists():
                path.write_text(content)

    def read_all(self) -> str:
        parts = []
        for label, path in [
            ("SELF", self.self_path),
            ("MEMORY", self.memory_path),
            ("NOW", self.now_path),
            ("RECENT_CONTEXT", self.recent_context_path),
        ]:
            parts.append(f"<{label.lower()}>\n{path.read_text()}\n</{label.lower()}>")
        return "\n\n".join(parts)

    def append(self, section: str, content: str) -> str:
        path = self._path_for(section)
        existing = path.read_text() if path.exists() else ""
        if is_duplicate_memory(content, existing):
            return f"Duplicate memory skipped in {path.name}"
        with path.open("a") as f:
            f.write(f"\n- {content}\n")
        return f"Saved to {path.name}"

    def append_pending(self, content: str, *, tag: str = "candidate", source_ref: str = "") -> str:
        line = self._format_memory_line(tag=tag, content=content, source_ref=source_ref)
        existing_memory = self.memory_path.read_text()
        if is_duplicate_memory(content, existing_memory):
            return "Memory already exists in MEMORY.md."
        existing_pending = self.pending_path.read_text()
        if is_duplicate_memory(content, existing_pending):
            return "Pending memory already exists."
        with self.pending_path.open("a") as f:
            f.write(f"\n{line}\n")
        return "Saved to PENDING.md"

    def append_history(self, content: str, *, source_ref: str = "") -> str:
        text = content.strip()
        if not text:
            return "No history to save."
        entry = f"\n## {_now_iso()}\n\n{text}\n"
        if source_ref:
            entry += f"\nsource_ref: `{source_ref}`\n"
        with self.history_path.open("a") as f:
            f.write(entry)
        return "Saved to HISTORY.md"

    def write_recent_context(self, content: str) -> str:
        self.recent_context_path.write_text(
            "# Recent Context\n\n" + content.strip() + "\n"
        )
        self.recent_context_data_path.write_text(
            '{\n  "version": 1,\n  "turns": []\n}\n'
        )
        return "Updated RECENT_CONTEXT.md"

    def read_recent_turns(self) -> list[dict]:
        try:
            payload = json.loads(self.recent_context_data_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        turns = payload.get("turns", []) if isinstance(payload, dict) else []
        return [turn for turn in turns if isinstance(turn, dict)]

    def write_recent_turns(self, turns: list[dict]) -> str:
        payload = {
            "version": 1,
            "turns": turns,
        }
        self.recent_context_data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        self.recent_context_path.write_text(self._render_recent_turns(turns))
        return "Updated RECENT_CONTEXT.md and RECENT_CONTEXT.json"

    def read_pending(self) -> str:
        return self.pending_path.read_text()

    def read_history(self) -> str:
        return self.history_path.read_text()

    def read_recent_context(self) -> str:
        return self.recent_context_path.read_text()

    def recall(self, query: str | None = None) -> str:
        text = self.read_all()
        if not query:
            return text
        # 第一版先不做检索，直接返回全文。
        return text

    def _path_for(self, section: str) -> Path:
        key = section.lower()
        if key == "self":
            return self.self_path
        if key == "now":
            return self.now_path
        if key == "pending":
            return self.pending_path
        if key == "history":
            return self.history_path
        if key in {"recent", "recent_context"}:
            return self.recent_context_path
        return self.memory_path

    def _format_memory_line(self, *, tag: str, content: str, source_ref: str = "") -> str:
        source = f" (source: `{source_ref}`)" if source_ref else ""
        return f"- [{tag}] {content.strip()}{source}"

    def _render_recent_turns(self, turns: list[dict]) -> str:
        sections = ["# Recent Context"]
        for index, turn in enumerate(turns, start=1):
            sections.extend(
                [
                    f"## Turn {index}",
                    "",
                    f"- session: `{turn.get('session_id', '')}`",
                    f"- mode: `{turn.get('mode', '')}`",
                    f"- source_ref: `{turn.get('source_ref', '')}`",
                    f"- created_at: `{turn.get('created_at', '')}`",
                    "",
                    "### USER_EXCERPT",
                    "",
                    self._trim_for_recent(str(turn.get("user_text", "")).strip()),
                    "",
                    "### ASSISTANT_SUMMARY",
                    "",
                    str(turn.get("assistant_summary", "")).strip(),
                ]
            )
        return "\n\n".join(sections).rstrip() + "\n"

    def _trim_for_recent(self, text: str, limit: int = 1200) -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + f"... ({len(text) - limit} chars omitted)"
