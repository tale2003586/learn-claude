import json
import os
import hashlib
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from uuid import uuid4


from coding_runtime.background_task import BG
from config import WORKDIR
from bus import AgentMessage, MessageType, render_agent_message
from bus.team_bus import BUS
from memory.store import MemoryStore
from runtime.workspace import safe_workspace_path, workspace_root_for_session
from coding_runtime.protocols import PROTOCOLS
from skill_runtime import SKILL_LOADER
from coding_runtime.task import TASKS
from user_scope import (
    explicit_user_id_for_session,
    memory_root_for_session,
    storage_root_for_session,
)


SUBAGENT_RUNNER = None


def configure_subagent_runner(runner) -> None:
    global SUBAGENT_RUNNER
    SUBAGENT_RUNNER = runner


def _format_tool_error(exc: Exception) -> str:
    return f"Error: {type(exc).__name__}: {exc}"


def safe_path(p: str, *, session=None) -> Path:
    return safe_workspace_path(p, session=session)


def run_list_files(path: str = "", recursive: bool = False, *, _session=None) -> str:
    try:
        root = workspace_root_for_session(_session).resolve()
        target = safe_workspace_path(path or ".", session=_session)
        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not target.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {path}")
        entries = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        for child in sorted(iterator, key=lambda item: item.relative_to(root).as_posix()):
            rel_parts = child.relative_to(root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            if "__pycache__" in rel_parts:
                continue
            entries.append({
                "path": child.relative_to(root).as_posix(),
                "type": "dir" if child.is_dir() else "file",
            })
            if len(entries) >= 500:
                break
        return json.dumps({
            "path": "" if target == root else target.relative_to(root).as_posix(),
            "recursive": bool(recursive),
            "entries": entries,
            "truncated": len(entries) >= 500,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return _format_tool_error(e)


def run_read(path: str, limit: int = None, *, _session=None) -> str:
    try:
        text = safe_path(path, session=_session).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return _format_tool_error(e)
    
def run_write(path: str, content: str, *, _session=None) -> str:
    try:
        fp = safe_path(path, session=_session)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return _format_tool_error(e)
    
def run_edit(path: str, old_text: str, new_text: str, *, _session=None) -> str:
    try:
        fp = safe_path(path, session=_session)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return _format_tool_error(e)


MAX_STORAGE_READ_BYTES = 1_000_000
MAX_STORAGE_WRITE_BYTES = 10 * 1024 * 1024
MAX_STORAGE_LIST_ENTRIES = 500
MAX_STORAGE_READ_CHARS = 50_000
MAX_PUBLISHED_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_GIT_OUTPUT_CHARS = 50_000
DEFAULT_SANDBOX_TTL_HOURS = 168
STORAGE_TEXT_SUFFIXES = {
    ".csv",
    ".html",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _storage_root(session=None) -> Path:
    return storage_root_for_session(WORKDIR, session)


def _generated_storage_root(session=None) -> Path:
    storage_root = _storage_root(session)
    generated = (storage_root / "generated").resolve()
    if generated != storage_root and not generated.is_relative_to(storage_root):
        raise ValueError("Generated storage directory escapes storage.")
    return generated


def _sandbox_root() -> Path:
    workspace = WORKDIR.resolve()
    root = (workspace / ".task_sandbox").resolve()
    if root != workspace and not root.is_relative_to(workspace):
        raise ValueError("Sandbox directory escapes workspace.")
    return root


def _safe_storage_path(root: Path, raw_path: str, *, allow_root: bool = False) -> Path:
    if not isinstance(raw_path, str):
        raise ValueError("Storage path must be a string.")
    cleaned = raw_path.strip()
    relative = Path(cleaned)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Storage path escapes allowed directory: {raw_path}")
    if any(part.startswith(".") for part in relative.parts):
        raise ValueError("Hidden storage paths are not allowed.")

    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError(f"Storage path escapes allowed directory: {raw_path}")
    if target == root and not allow_root:
        raise ValueError("Storage file path is required.")
    return target


def _storage_relative(path: Path, *, session=None) -> str:
    return path.relative_to(_storage_root(session)).as_posix()


def _scope_relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _git_workspace(session=None) -> Path:
    root = workspace_root_for_session(session).resolve()
    if not (root / ".git").exists():
        raise ValueError(f"Workspace is not a git repository root: {root}")
    return root


def _run_git(args: list[str], *, _session=None) -> str:
    root = _git_workspace(_session)
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return f"Error: git {' '.join(args)} failed with code {result.returncode}\n{output}"
    return output[:MAX_GIT_OUTPUT_CHARS] if output else "(no output)"


def _git_pathspec(path: str, *, _session=None) -> str:
    raw = str(path or "").strip()
    if raw.startswith(":"):
        raise ValueError(f"Git pathspec magic is not allowed: {path}")
    target = safe_workspace_path(path, session=_session)
    root = workspace_root_for_session(_session).resolve()
    if target == root:
        raise ValueError("Git path must point to a file or subdirectory, not workspace root.")
    return target.relative_to(root).as_posix()


def _git_pathspecs(paths, *, _session=None) -> list[str]:
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list.")
    return [_git_pathspec(str(path), _session=_session) for path in paths]


def run_git_status(porcelain: bool = False, *, _session=None) -> str:
    try:
        args = ["status", "--short"] if porcelain else ["status", "--branch", "--short"]
        return _run_git(args, _session=_session)
    except Exception as e:
        return _format_tool_error(e)


def run_git_diff(
    path: str = "",
    staged: bool = False,
    stat: bool = False,
    *,
    _session=None,
) -> str:
    try:
        args = ["diff"]
        if staged:
            args.append("--staged")
        if stat:
            args.append("--stat")
        if path:
            args.extend(["--", _git_pathspec(path, _session=_session)])
        return _run_git(args, _session=_session)
    except Exception as e:
        return _format_tool_error(e)


def run_git_log(max_count: int = 10, *, _session=None) -> str:
    try:
        try:
            count = max(1, min(int(max_count or 10), 50))
        except (TypeError, ValueError):
            count = 10
        return _run_git(
            [
                "log",
                f"--max-count={count}",
                "--date=iso",
                "--pretty=format:%h %ad %an %s",
            ],
            _session=_session,
        )
    except Exception as e:
        return _format_tool_error(e)


def run_git_branch(all: bool = False, *, _session=None) -> str:
    try:
        args = ["branch"]
        if all:
            args.append("--all")
        return _run_git(args, _session=_session)
    except Exception as e:
        return _format_tool_error(e)


def run_git_add(paths, *, _session=None) -> str:
    try:
        pathspecs = _git_pathspecs(paths, _session=_session)
        return _run_git(["add", "--", *pathspecs], _session=_session)
    except Exception as e:
        return _format_tool_error(e)


def run_git_commit(message: str, *, _session=None) -> str:
    try:
        cleaned = str(message or "").strip()
        if not cleaned:
            raise ValueError("Commit message is required.")
        return _run_git(
            [
                "-c",
                "user.name=Agent Runtime",
                "-c",
                "user.email=agent@example.invalid",
                "commit",
                "-m",
                cleaned,
            ],
            _session=_session,
        )
    except Exception as e:
        return _format_tool_error(e)


def _sandbox_scope_root(session, *, create: bool = True) -> Path:
    if session is None or not str(getattr(session, "id", "")).strip():
        raise ValueError("Sandbox tools require an active session.")

    cleanup_expired_sandboxes()
    metadata = getattr(session, "metadata", {}) or {}
    sandbox_root = _sandbox_root()
    if metadata.get("kind") == "task_session":
        task_id = str(metadata.get("task_id", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", task_id):
            raise ValueError("Task session has an invalid task_id for sandbox scope.")
        raw_scope = sandbox_root / "tasks" / task_id
    else:
        digest = hashlib.sha256(str(session.id).encode("utf-8")).hexdigest()[:20]
        raw_scope = sandbox_root / "sessions" / digest

    scope = raw_scope.resolve()
    if scope != sandbox_root and not scope.is_relative_to(sandbox_root):
        raise ValueError("Session sandbox escapes .task_sandbox.")
    if create:
        scope.mkdir(parents=True, exist_ok=True)
        _touch_sandbox_scope(scope)
    return scope


def _touch_sandbox_scope(scope: Path) -> None:
    if scope.exists():
        os.utime(scope, None)


def cleanup_expired_sandboxes(
    *,
    max_age_seconds: float | None = None,
    now: float | None = None,
) -> int:
    root = _sandbox_root()
    if not root.exists():
        return 0
    if max_age_seconds is None:
        try:
            ttl_hours = float(os.environ.get("TASK_SANDBOX_TTL_HOURS", DEFAULT_SANDBOX_TTL_HOURS))
        except ValueError:
            ttl_hours = float(DEFAULT_SANDBOX_TTL_HOURS)
        max_age_seconds = max(0.0, ttl_hours * 3600)
    if max_age_seconds <= 0:
        return 0

    current_time = time.time() if now is None else now
    removed = 0
    for category in ("tasks", "sessions"):
        category_root = root / category
        if not category_root.exists() or category_root.is_symlink():
            continue
        for scope in category_root.iterdir():
            if scope.is_symlink() or not scope.is_dir():
                continue
            if current_time - scope.stat().st_mtime <= max_age_seconds:
                continue
            shutil.rmtree(scope)
            removed += 1
    return removed


def _read_text_file(target: Path, *, path_label: str, limit: int = None) -> str:
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path_label}")
    if not target.is_file():
        raise IsADirectoryError(f"Path is not a file: {path_label}")
    if target.stat().st_size > MAX_STORAGE_READ_BYTES:
        raise ValueError(
            f"File is too large to read: maximum is {MAX_STORAGE_READ_BYTES} bytes."
        )

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    if limit and limit < len(lines):
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    output = "\n".join(lines)
    if len(output) > MAX_STORAGE_READ_CHARS:
        output = output[:MAX_STORAGE_READ_CHARS] + "\n... (content truncated)"
    return output


def run_storage_list(path: str = "", *, _session=None) -> str:
    try:
        root = _storage_root(_session)
        root.mkdir(parents=True, exist_ok=True)
        target = _safe_storage_path(root, path, allow_root=True)
        if not target.exists():
            raise FileNotFoundError(f"Storage path not found: {path}")
        if not target.is_dir():
            raise NotADirectoryError(f"Storage path is not a directory: {path}")

        entries = []
        truncated = False
        children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        for child in children:
            if child.name.startswith("."):
                continue
            resolved = child.resolve()
            if resolved != root and not resolved.is_relative_to(root):
                continue
            if len(entries) >= MAX_STORAGE_LIST_ENTRIES:
                truncated = True
                break
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": _storage_relative(resolved, session=_session),
                "is_dir": child.is_dir(),
                "bytes": 0 if child.is_dir() else stat.st_size,
            })

        return json.dumps({
            "path": _storage_relative(target, session=_session) if target != root else "",
            "entries": entries,
            "truncated": truncated,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return _format_tool_error(e)


def run_storage_read(path: str, limit: int = None, *, _session=None) -> str:
    try:
        target = _safe_storage_path(_storage_root(_session), path)
        return _read_text_file(target, path_label=path, limit=limit)
    except Exception as e:
        return _format_tool_error(e)


def run_storage_write(path: str, content: str, *, _session=None) -> str:
    try:
        if not isinstance(content, str):
            raise ValueError("Storage artifact content must be text.")
        target = _safe_storage_path(_generated_storage_root(_session), path)
        if target.suffix.lower() not in STORAGE_TEXT_SUFFIXES:
            allowed = ", ".join(sorted(STORAGE_TEXT_SUFFIXES))
            raise ValueError(f"Unsupported storage artifact type. Allowed suffixes: {allowed}")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_STORAGE_WRITE_BYTES:
            raise ValueError(
                f"Storage artifact is too large: maximum is {MAX_STORAGE_WRITE_BYTES} bytes."
            )
        if target.exists():
            raise FileExistsError(
                f"Storage artifact already exists: {_storage_relative(target, session=_session)}. "
                "Choose a new filename."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()

        _append_storage_write_record(
            target=target,
            encoded=encoded,
            session_id=getattr(_session, "id", ""),
            session=_session,
        )
        return json.dumps({
            "status": "created",
            "path": _storage_relative(target, session=_session),
            "bytes": len(encoded),
        }, ensure_ascii=False)
    except Exception as e:
        return _format_tool_error(e)


def _append_storage_write_record(
    *,
    target: Path,
    encoded: bytes,
    session_id: str,
    session=None,
) -> None:
    _append_artifact_record(
        operation="storage_write",
        target=target,
        session_id=session_id,
        byte_count=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
        session=session,
    )


def _append_artifact_record(
    *,
    operation: str,
    target: Path,
    session_id: str,
    byte_count: int,
    sha256: str,
    source_path: str = "",
    session=None,
) -> None:
    records_dir = _storage_root(session) / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "session_id": session_id,
        "path": _storage_relative(target, session=session),
        "bytes": byte_count,
        "sha256": sha256,
    }
    if source_path:
        record["source_path"] = source_path
    with (records_dir / "storage_writes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_sandbox_list(path: str = "", *, _session=None) -> str:
    try:
        scope = _sandbox_scope_root(_session)
        target = _safe_storage_path(scope, path, allow_root=True)
        if not target.exists():
            raise FileNotFoundError(f"Sandbox path not found: {path}")
        if not target.is_dir():
            raise NotADirectoryError(f"Sandbox path is not a directory: {path}")

        entries = []
        truncated = False
        children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        for child in children:
            if child.name.startswith("."):
                continue
            resolved = child.resolve()
            if resolved != scope and not resolved.is_relative_to(scope):
                continue
            if len(entries) >= MAX_STORAGE_LIST_ENTRIES:
                truncated = True
                break
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": _scope_relative(scope, resolved),
                "is_dir": child.is_dir(),
                "bytes": 0 if child.is_dir() else stat.st_size,
            })
        _touch_sandbox_scope(scope)
        return json.dumps({
            "path": _scope_relative(scope, target) if target != scope else "",
            "entries": entries,
            "truncated": truncated,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return _format_tool_error(e)


def run_sandbox_read(path: str, limit: int = None, *, _session=None) -> str:
    try:
        scope = _sandbox_scope_root(_session)
        target = _safe_storage_path(scope, path)
        output = _read_text_file(target, path_label=path, limit=limit)
        _touch_sandbox_scope(scope)
        return output
    except Exception as e:
        return _format_tool_error(e)


def run_sandbox_write(
    path: str,
    content: str,
    *,
    overwrite: bool = False,
    _session=None,
) -> str:
    try:
        if not isinstance(content, str):
            raise ValueError("Sandbox file content must be text.")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_STORAGE_WRITE_BYTES:
            raise ValueError(
                f"Sandbox file is too large: maximum is {MAX_STORAGE_WRITE_BYTES} bytes."
            )

        scope = _sandbox_scope_root(_session)
        target = _safe_storage_path(scope, path)
        existed = target.exists()
        if existed and not overwrite:
            raise FileExistsError(
                f"Sandbox file already exists: {path}. Set overwrite=true to revise it."
            )
        if existed and not target.is_file():
            raise IsADirectoryError(f"Sandbox path is not a file: {path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        _touch_sandbox_scope(scope)
        return json.dumps({
            "status": "updated" if existed else "created",
            "path": _scope_relative(scope, target),
            "bytes": len(encoded),
        }, ensure_ascii=False)
    except Exception as e:
        return _format_tool_error(e)


def run_publish_artifact(
    source_path: str,
    destination_path: str | None = None,
    *,
    _session=None,
) -> str:
    try:
        scope = _sandbox_scope_root(_session)
        source = _safe_storage_path(scope, source_path)
        if not source.exists():
            raise FileNotFoundError(f"Sandbox file not found: {source_path}")
        if not source.is_file():
            raise IsADirectoryError(f"Sandbox path is not a file: {source_path}")
        byte_count = source.stat().st_size
        if byte_count > MAX_PUBLISHED_ARTIFACT_BYTES:
            raise ValueError(
                f"Artifact is too large to publish: maximum is {MAX_PUBLISHED_ARTIFACT_BYTES} bytes."
            )

        published_path = destination_path if destination_path is not None else source_path
        target = _safe_storage_path(_generated_storage_root(_session), published_path)
        if target.exists():
            raise FileExistsError(
                f"Storage artifact already exists: {_storage_relative(target, session=_session)}. "
                "Choose a new destination filename."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
                while chunk := source_handle.read(1024 * 1024):
                    digest.update(chunk)
                    target_handle.write(chunk)
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()

        _append_artifact_record(
            operation="publish_artifact",
            target=target,
            session_id=getattr(_session, "id", ""),
            source_path=source_path,
            byte_count=byte_count,
            sha256=digest.hexdigest(),
            session=_session,
        )
        _touch_sandbox_scope(scope)
        return json.dumps({
            "status": "published",
            "source_path": source_path,
            "path": _storage_relative(target, session=_session),
            "bytes": byte_count,
        }, ensure_ascii=False)
    except Exception as e:
        return _format_tool_error(e)


def run_bash(command: str, *, _session=None) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        workspace = workspace_root_for_session(_session)
        r = subprocess.run(command, shell=True, cwd=workspace,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return _format_tool_error(e)
    

BASE_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"], _session=kw.get("_session")),
    "load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
    "list_files": lambda **kw: run_list_files(
        kw.get("path", ""),
        kw.get("recursive", False),
        _session=kw.get("_session"),
    ),
    "read_file": lambda **kw: run_read(
        kw["path"],
        kw.get("limit"),
        _session=kw.get("_session"),
    ),
    "write_file": lambda **kw: run_write(
        kw["path"],
        kw["content"],
        _session=kw.get("_session"),
    ),
    "edit_file": lambda **kw: run_edit(
        kw["path"],
        kw["old_text"],
        kw["new_text"],
        _session=kw.get("_session"),
    ),
    "git_status": lambda **kw: run_git_status(
        kw.get("porcelain", False),
        _session=kw.get("_session"),
    ),
    "git_diff": lambda **kw: run_git_diff(
        kw.get("path", ""),
        kw.get("staged", False),
        kw.get("stat", False),
        _session=kw.get("_session"),
    ),
    "git_log": lambda **kw: run_git_log(
        kw.get("max_count", 10),
        _session=kw.get("_session"),
    ),
    "git_branch": lambda **kw: run_git_branch(
        kw.get("all", False),
        _session=kw.get("_session"),
    ),
    "git_add": lambda **kw: run_git_add(
        kw["paths"],
        _session=kw.get("_session"),
    ),
    "git_commit": lambda **kw: run_git_commit(
        kw["message"],
        _session=kw.get("_session"),
    ),
}

TASK_HANDLERS = {
    "task_create": lambda **kw: TASKS.create(
        kw["subject"],
        kw.get("description", "")
    ),
    "task_update": lambda **kw: TASKS.update(
        kw["task_id"],
        kw.get("status"),
        kw.get("addBlockedBy"),
        kw.get("removeBlockedBy"),
    ),
    "task_list": lambda **kw: TASKS.list_all(),
    "task_get": lambda **kw: TASKS.get(kw["task_id"]),
}

BACKGROUND_HANDLERS = {
    "background_run": lambda **kw: BG.run(kw["command"]),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
}

def make_protocol_handlers(sender: str):
    return {
        "idle": lambda **kw: "Entering idle phase. Will poll for new tasks.",
        "shutdown_response": lambda **kw: PROTOCOLS.handle_shutdown_response(
            sender,
            kw["request_id"],
            kw["approve"],
            kw.get("details", ""),
        ),
        "plan_approval_request": lambda **kw: PROTOCOLS.handle_plan_request(
            sender,
            kw["plan"],
        ),
    }


def _message_type(value: str | None) -> MessageType:
    try:
        return MessageType(str(value or "message"))
    except ValueError:
        return MessageType.MESSAGE


def _send_structured_message(
    *,
    sender: str,
    to: str,
    content: str,
    msg_type: str = "message",
    payload: dict | None = None,
) -> str:
    message = AgentMessage(
        sender=sender,
        recipient=to,
        type=_message_type(msg_type),
        payload={"content": content, **(payload or {})},
    )
    return BUS.send(
        message.sender,
        message.recipient,
        message.to_json(),
        message.type.value,
        {
            "id": message.id,
            "recipient": message.recipient,
            "correlation_id": message.correlation_id,
            "payload": message.payload,
            "ttl_seconds": message.ttl_seconds,
        },
    )


def _read_structured_inbox(name: str) -> str:
    messages = BUS.read_inbox(name)
    rendered = []
    for message in messages:
        try:
            PROTOCOLS.notify_message(message)
            rendered.append(render_agent_message(message))
        except Exception:
            rendered.append(json.dumps(message, ensure_ascii=False))
    return "\n\n".join(rendered) if rendered else "Inbox is empty."


def _spawn_teammate_with_protocol(team, name: str, role: str, prompt: str) -> str:
    message = AgentMessage(
        sender="lead",
        recipient=name,
        type=MessageType.TASK_ASSIGN,
        payload={
            "description": f"Initial task for teammate {name}",
            "prompt": prompt,
            "role": role,
        },
    )
    return team.spawn(name, role, message.to_json())


def _broadcast_structured(team, content: str) -> str:
    count = 0
    for name in team.member_names():
        message = AgentMessage(
            sender="lead",
            recipient=name,
            type=MessageType.BROADCAST,
            payload={"content": content},
        )
        BUS.send(
            message.sender,
            message.recipient,
            message.to_json(),
            message.type.value,
            {
                "id": message.id,
                "recipient": message.recipient,
                "payload": message.payload,
                "ttl_seconds": message.ttl_seconds,
            },
        )
        count += 1
    return f"Broadcast to {count} teammates"


def _run_subagent_task(
    *,
    prompt: str,
    description: str = "",
    agent_type: str = "explore",
    _session=None,
) -> str:
    if SUBAGENT_RUNNER is None:
        return "Error: The short-lived subagent task runner is not configured."
    result = SUBAGENT_RUNNER.run(
        prompt=prompt,
        description=description,
        agent_type=agent_type,
        parent_session=_session,
    )
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def _run_parallel_subagent_tasks(
    *,
    tasks,
    max_workers: int | None = None,
    _session=None,
) -> str:
    if SUBAGENT_RUNNER is None:
        return "Error: The short-lived subagent task runner is not configured."
    from agents.subagent.parallel import run_parallel_tasks

    results = run_parallel_tasks(
        runner=SUBAGENT_RUNNER,
        tasks=tasks or [],
        parent_session=_session,
        max_workers=max_workers,
    )
    return json.dumps({"results": results}, ensure_ascii=False, indent=2)


def make_lead_handlers(team):
    return {
        **BASE_HANDLERS,
        **TASK_HANDLERS,
        **BACKGROUND_HANDLERS,
        **MEMORY_HANDLERS,
        **STORAGE_HANDLERS,
        **SANDBOX_HANDLERS,
        **make_protocol_handlers("lead"),

        "compact": lambda **kw: "Manual compression requested.",
        "task": _run_subagent_task,
        "parallel_tasks": _run_parallel_subagent_tasks,
        "claim_task": lambda **kw: TASKS.claim_task(
            kw["task_id"],
            "lead",
        ),

        "spawn_teammate": lambda **kw: _spawn_teammate_with_protocol(
            team,
            kw["name"],
            kw["role"],
            kw["prompt"],
        ),
        "list_teammates": lambda **kw: team.list_all(),
        "broadcast": lambda **kw: _broadcast_structured(team, kw["content"]),
        "send_message": lambda **kw: _send_structured_message(
            sender="lead",
            to=kw["to"],
            content=kw["content"],
            msg_type=kw.get("msg_type", "message"),
        ),
        "read_inbox": lambda **kw: _read_structured_inbox("lead"),
        "shutdown_request": lambda **kw: PROTOCOLS.handle_shutdown_request(
            kw["teammate"],
        ),
        "shutdown_status": lambda **kw: PROTOCOLS._check_shutdown_status(
            kw["request_id"],
        ),
        "plan_approval": lambda **kw: PROTOCOLS.handle_plan_review(
            kw["request_id"],
            kw["approve"],
            kw.get("feedback", ""),
        ),
    }



def make_teammate_handlers(name: str):
    return {
        **BASE_HANDLERS,
        **TASK_HANDLERS,
        **BACKGROUND_HANDLERS,
        **make_protocol_handlers(name),
        "claim_task": lambda **kw: TASKS.claim_task(
            kw["task_id"],
            name,
        ),

        "send_message": lambda **kw: _send_structured_message(
            sender=name,
            to=kw["to"],
            content=kw["content"],
            msg_type=kw.get("msg_type", "message"),
        ),
        "read_inbox": lambda **kw: _read_structured_inbox(name),
    }

TEAMMATE_HANDLER = make_teammate_handlers("")


MEMORY = MemoryStore()
TASK_MEMORY_ROOT = (WORKDIR / ".task_sessions").resolve()


def memory_store_for_session(session=None) -> MemoryStore:
    metadata = getattr(session, "metadata", {}) or {}
    if metadata.get("kind") != "task_session":
        if explicit_user_id_for_session(session) is None:
            return MEMORY
        return MemoryStore(memory_root_for_session(WORKDIR, session))

    task_id = str(metadata.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("Task session is missing task_id metadata.")

    configured_root = metadata.get("memory_root")
    if configured_root:
        root = Path(str(configured_root))
        if not root.is_absolute():
            root = WORKDIR / root
        root = root.resolve()
    else:
        root = (TASK_MEMORY_ROOT / task_id / "memory").resolve()
    if not root.is_relative_to(TASK_MEMORY_ROOT):
        raise ValueError("Task memory root escapes .task_sessions.")
    return MemoryStore(root)


def run_memorize(*, content: str, section: str = "memory", _session=None) -> str:
    return memory_store_for_session(_session).append(section, content)


def run_recall_memory(*, query: str | None = None, _session=None) -> str:
    return memory_store_for_session(_session).recall(query)

MEMORY_HANDLERS = {
    "memorize": run_memorize,
    "recall_memory": run_recall_memory,
}

STORAGE_HANDLERS = {
    "storage_list_files": lambda **kw: run_storage_list(
        kw.get("path", ""),
        _session=kw.get("_session"),
    ),
    "storage_read_file": lambda **kw: run_storage_read(
        kw["path"],
        kw.get("limit"),
        _session=kw.get("_session"),
    ),
    "storage_write_file": lambda **kw: run_storage_write(
        kw["path"],
        kw["content"],
        _session=kw.get("_session"),
    ),
}

SANDBOX_HANDLERS = {
    "sandbox_list_files": lambda **kw: run_sandbox_list(
        kw.get("path", ""),
        _session=kw.get("_session"),
    ),
    "sandbox_read_file": lambda **kw: run_sandbox_read(
        kw["path"],
        kw.get("limit"),
        _session=kw.get("_session"),
    ),
    "sandbox_write_file": lambda **kw: run_sandbox_write(
        kw["path"],
        kw["content"],
        overwrite=kw.get("overwrite", False),
        _session=kw.get("_session"),
    ),
    "publish_artifact": lambda **kw: run_publish_artifact(
        kw["source_path"],
        kw.get("destination_path"),
        _session=kw.get("_session"),
    ),
}
