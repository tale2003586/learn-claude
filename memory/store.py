from pathlib import Path
from config import WORKDIR


class MemoryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or WORKDIR / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.root / "MEMORY.md"
        self.self_path = self.root / "SELF.md"
        self.now_path = self.root / "NOW.md"
        self._ensure_files()

    def _ensure_files(self) -> None:
        defaults = {
            self.memory_path: "# Memory\n\n",
            self.self_path: "# Self\n\n",
            self.now_path: "# Now\n\n",
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
        ]:
            parts.append(f"<{label.lower()}>\n{path.read_text()}\n</{label.lower()}>")
        return "\n\n".join(parts)

    def append(self, section: str, content: str) -> str:
        path = self._path_for(section)
        with path.open("a") as f:
            f.write(f"\n- {content}\n")
        return f"Saved to {path.name}"

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
        return self.memory_path
