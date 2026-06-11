from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import DEFAULT_CODING_WORKSPACE, WORKDIR, WORKSPACE_ROOTS


WORKSPACE_METADATA_KEY = "workspace_root"


@dataclass(frozen=True)
class WorkspaceRef:
    root: Path
    display_name: str
    allowed_root: Path
    source: str
    requested: str

    def to_metadata(self) -> dict:
        return {
            "workspace_root": str(self.root),
            "workspace_display_name": self.display_name,
            "workspace_allowed_root": str(self.allowed_root),
            "workspace_source": self.source,
            "workspace_requested": self.requested,
        }


class WorkspaceResolver:
    def __init__(
        self,
        *,
        allowed_roots: Iterable[str | Path] | None = None,
        default_workspace: str | Path | None = None,
    ) -> None:
        roots = list(allowed_roots if allowed_roots is not None else WORKSPACE_ROOTS)
        if not roots:
            roots = [WORKDIR]
        self.allowed_roots = [_resolve_path(root) for root in roots]
        self.default_workspace = _resolve_path(
            default_workspace if default_workspace is not None else DEFAULT_CODING_WORKSPACE
        )

    def resolve(
        self,
        requested: str | Path | None = None,
        *,
        session=None,
    ) -> WorkspaceRef:
        source = "default"
        raw = ""
        if requested is not None and str(requested).strip():
            raw = str(requested).strip()
            source = "request"
        else:
            metadata = getattr(session, "metadata", {}) or {}
            session_workspace = metadata.get(WORKSPACE_METADATA_KEY)
            if session_workspace:
                raw = str(session_workspace).strip()
                source = "session"
        if not raw:
            raw = str(self.default_workspace)

        root = _resolve_path(raw)
        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")

        allowed_root = self._allowed_root_for(root)
        return WorkspaceRef(
            root=root,
            display_name=root.name or str(root),
            allowed_root=allowed_root,
            source=source,
            requested=raw,
        )

    def bind_session(self, session, workspace: WorkspaceRef) -> None:
        if session is None:
            return
        session.metadata.update(workspace.to_metadata())

    def _allowed_root_for(self, root: Path) -> Path:
        for allowed in self.allowed_roots:
            if root == allowed or root.is_relative_to(allowed):
                return allowed
        allowed_text = ", ".join(str(path) for path in self.allowed_roots)
        raise ValueError(
            f"Workspace is outside allowed roots: {root}. Allowed roots: {allowed_text}"
        )


def workspace_root_for_session(session=None) -> Path:
    metadata = getattr(session, "metadata", {}) or {}
    raw = metadata.get(WORKSPACE_METADATA_KEY)
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return DEFAULT_CODING_WORKSPACE


def safe_workspace_path(path: str, *, session=None) -> Path:
    root = workspace_root_for_session(session).resolve()
    relative = Path(str(path or "").strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Path escapes workspace: {path}")
    target = (root / relative).resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")
    return target


def _resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
