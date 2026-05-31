from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import mimetypes
import os
import queue
import shutil
import sys
import threading
import traceback
import warnings
from datetime import datetime, timezone
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning, message="'cgi' is deprecated.*")
    import cgi


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web" / "static"
SESSIONS_DB = ROOT / ".sessions" / "sessions.db"
MEMORY_DIR = ROOT / "memory"
STORAGE_DIR = ROOT / "storage"
RECORDS_DIR = STORAGE_DIR / "records"
ANALYSIS_RECORD_PATH = RECORDS_DIR / "analysis.txt"
MEMORY_FILES = [
    "SELF.md",
    "MEMORY.md",
    "NOW.md",
    "PENDING.md",
    "RECENT_CONTEXT.md",
    "HISTORY.md",
]
DEFAULT_MAX_BODY_BYTES = 52_428_800
MAX_PREVIEW_BYTES = 1_000_000
AUTH_REALM = "taleclaw"
_CHAT_MARKDOWN = None

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")
MAX_BODY_BYTES = int(os.environ.get("WEB_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES)))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
RECORDS_DIR.mkdir(parents=True, exist_ok=True)


class AgentService:
    """Owns the async agent runtime behind the synchronous stdlib HTTP server."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._start_error: BaseException | None = None
        self._turn_lock: asyncio.Lock | None = None
        self._pending: dict[str, asyncio.Future[str]] = {}

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._runtime is not None:
                return
            if self._start_error is not None:
                raise RuntimeError(_friendly_runtime_error(self._start_error)) from self._start_error
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="agent-web-runtime",
                    daemon=True,
                )
                self._thread.start()

        if not self._ready.wait(timeout=15):
            raise RuntimeError("Agent runtime did not start within 15 seconds.")
        if self._start_error is not None:
            raise RuntimeError(_friendly_runtime_error(self._start_error)) from self._start_error

    def ask(self, *, session_id: str, content: str, timeout: int = 180) -> str:
        return self.ask_stream(
            session_id=session_id,
            content=content,
            timeout=timeout,
        )

    def ask_stream(
        self,
        *,
        session_id: str,
        content: str,
        on_text: Callable[[str], None] | None = None,
        timeout: int = 180,
    ) -> str:
        self.ensure_started()
        if self._loop is None:
            raise RuntimeError("Agent runtime loop is not available.")

        future = asyncio.run_coroutine_threadsafe(
            self._ask_async(
                session_id=session_id,
                content=content,
                on_text=on_text,
            ),
            self._loop,
        )
        return future.result(timeout=timeout)

    def delete_session(self, session_id: str, timeout: int = 10) -> bool:
        if self._thread is None and self._runtime is None:
            return _delete_stored_session(session_id)

        self.ensure_started()
        if self._loop is None:
            raise RuntimeError("Agent runtime loop is not available.")
        future = asyncio.run_coroutine_threadsafe(
            self._delete_session_async(session_id),
            self._loop,
        )
        return future.result(timeout=timeout)

    def stop(self) -> None:
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
        try:
            future.result(timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._start_async())
        except BaseException as exc:
            self._start_error = exc
        finally:
            self._ready.set()

        if self._start_error is None:
            loop.run_forever()
        loop.close()

    async def _start_async(self) -> None:
        from core.bootstrap import build_runtime

        self._runtime = build_runtime()
        self._turn_lock = asyncio.Lock()
        self._runtime.bus.subscribe_outbound("web", self._handle_outbound)
        self._runtime.start()

    async def _stop_async(self) -> None:
        if self._runtime is None:
            return
        await self._runtime.stop()
        sessions = getattr(getattr(self._runtime, "loop", None), "sessions", None)
        if sessions is not None:
            sessions.close()

    async def _handle_outbound(self, message) -> None:
        reply_future = self._pending.pop(message.chat_id, None)
        if reply_future is not None and not reply_future.done():
            reply_future.set_result(message.content)

    async def _ask_async(
        self,
        *,
        session_id: str,
        content: str,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        if self._runtime is None or self._turn_lock is None or self._loop is None:
            raise RuntimeError("Agent runtime is not started.")

        async with self._turn_lock:
            reply_future: asyncio.Future[str] = self._loop.create_future()
            self._pending[session_id] = reply_future
            try:
                await self._runtime.submit_user_message(
                    content=content,
                    channel="web",
                    chat_id=session_id,
                )
                await self._runtime.run_once(on_text=on_text)
                return await asyncio.wait_for(reply_future, timeout=10)
            finally:
                self._pending.pop(session_id, None)

    async def _delete_session_async(self, session_id: str) -> bool:
        if self._runtime is None or self._turn_lock is None:
            raise RuntimeError("Agent runtime is not started.")

        async with self._turn_lock:
            sessions = getattr(getattr(self._runtime, "loop", None), "sessions", None)
            if sessions is None:
                raise RuntimeError("Agent session manager is not available.")
            return sessions.delete(session_id)


def _friendly_runtime_error(exc: BaseException) -> str:
    if isinstance(exc, KeyError) and exc.args and exc.args[0] == "DEEPSEEK_API_KEY":
        return "缺少 DEEPSEEK_API_KEY。请在环境变量或 .env 中配置后再使用聊天功能。"
    if isinstance(exc, ModuleNotFoundError):
        return f"缺少 Python 模块：{exc.name}。现有 CLI 依赖需要先安装完整。"
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


def read_sessions() -> list[dict[str, Any]]:
    from sessions.session_store import SessionStore

    store = SessionStore(SESSIONS_DB)
    try:
        rows = store.list_sessions()
    finally:
        store.close()

    sessions: list[dict[str, Any]] = []
    for row in rows:
        if _is_internal_task_session(row):
            continue
        channel, _, chat_id = row["id"].partition(":")
        sessions.append({
            **row,
            "channel": channel if chat_id else "",
            "chat_id": chat_id if chat_id else row["id"],
            "can_chat": channel == "web" and bool(chat_id),
        })
    return sessions


def _delete_stored_session(session_id: str) -> bool:
    from sessions.session_store import SessionStore

    store = SessionStore(SESSIONS_DB)
    try:
        return store.delete_session(session_id)
    finally:
        store.close()


def _web_storage_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not value:
        raise ValueError("session_id is required")

    channel, separator, chat_id = value.partition(":")
    if separator:
        if channel != "web" or not chat_id:
            raise ValueError("Only Web sessions can be deleted.")
        return value
    return f"web:{value}"


def _is_internal_task_session(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") or {}
    return row.get("id", "").startswith("task:") or metadata.get("kind") == "task_session"


def read_session(session_id: str, *, raw: bool = False) -> dict[str, Any]:
    from sessions.session_store import SessionStore

    storage_id = session_id if raw or ":" in session_id else f"web:{session_id}"
    store = SessionStore(SESSIONS_DB)
    try:
        session = store.load_session(storage_id)
    finally:
        store.close()

    if session is None:
        channel, _, chat_id = storage_id.partition(":")
        return {
            "id": storage_id,
            "chat_id": chat_id if chat_id else session_id,
            "channel": channel if chat_id else "",
            "can_chat": channel == "web" and bool(chat_id),
            "messages": [],
            "current_mode": "hybrid",
        }
    channel, _, chat_id = session["id"].partition(":")
    session["channel"] = channel if chat_id else ""
    session["chat_id"] = chat_id if chat_id else session["id"]
    session["can_chat"] = channel == "web" and bool(chat_id)
    session["messages"] = [_web_message(message) for message in session["messages"]]
    return session


def _web_message(message: dict[str, Any]) -> dict[str, Any]:
    result = dict(message)
    content = result.get("content")
    if result.get("role") == "assistant" and isinstance(content, str):
        result["display_html"] = render_chat_markdown(content)
    return result


def render_chat_markdown(text: str) -> str:
    global _CHAT_MARKDOWN

    try:
        import mistune
    except ImportError:
        return f"<p>{html_escape(text).replace(chr(10), '<br>')}</p>"

    if _CHAT_MARKDOWN is None:
        class SafeChatRenderer(mistune.HTMLRenderer):
            def link(self, text: str, url: str, title: str | None = None) -> str:
                if urlparse(url).scheme not in {"http", "https", "mailto"}:
                    return text
                return super().link(text, url, title)

            def image(self, text: str, url: str, title: str | None = None) -> str:
                return html_escape(f"[image: {text or 'attachment'}]")

        _CHAT_MARKDOWN = mistune.create_markdown(
            renderer=SafeChatRenderer(escape=True),
        )
    return _CHAT_MARKDOWN(text)


def read_memory_files() -> list[dict[str, str]]:
    files = []
    for name in MEMORY_FILES:
        path = MEMORY_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            content = ""
        files.append({"name": name, "content": content})
    return files


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_storage_path(relative_path: str | None = "") -> Path:
    raw = unquote(str(relative_path or "")).strip().lstrip("/")
    candidate = (STORAGE_DIR / raw).resolve()
    if candidate != STORAGE_DIR and not candidate.is_relative_to(STORAGE_DIR):
        raise ValueError("Path escapes storage.")
    return candidate


def _storage_rel(path: Path) -> str:
    if path == STORAGE_DIR:
        return ""
    return path.relative_to(STORAGE_DIR).as_posix()


def _entry_for(path: Path) -> dict[str, Any]:
    stat = path.stat()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    is_dir = path.is_dir()
    return {
        "name": path.name,
        "path": _storage_rel(path),
        "is_dir": is_dir,
        "size": 0 if is_dir else stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "mime": "inode/directory" if is_dir else mime,
        "previewable": _is_previewable(path, mime),
    }


def _is_previewable(path: Path, mime: str) -> bool:
    if path.is_dir():
        return False
    if mime.startswith("text/") or mime in {
        "application/json",
        "application/xml",
        "application/javascript",
    }:
        return True
    return path.suffix.lower() in {
        ".txt", ".md", ".py", ".js", ".css", ".html", ".json", ".csv", ".log", ".yaml", ".yml",
    }


def list_storage(relative_path: str | None = "") -> dict[str, Any]:
    current = _safe_storage_path(relative_path)
    if not current.exists():
        raise FileNotFoundError("Path not found.")
    if not current.is_dir():
        raise NotADirectoryError("Path is not a directory.")
    entries = [_entry_for(path) for path in current.iterdir() if not path.name.startswith(".")]
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    parent = ""
    if current != STORAGE_DIR:
        parent = _storage_rel(current.parent)
    return {
        "path": _storage_rel(current),
        "parent": parent,
        "entries": entries,
        "record_path": _storage_rel(ANALYSIS_RECORD_PATH),
    }


def append_analysis_record(*, user_text: str, assistant_text: str) -> str:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    entry = (
        f"\n## {_now_iso()}\n\n"
        "USER:\n"
        f"{user_text.strip()}\n\n"
        "AI:\n"
        f"{assistant_text.strip()}\n"
    )
    with ANALYSIS_RECORD_PATH.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return _storage_rel(ANALYSIS_RECORD_PATH)


def auth_credentials() -> tuple[str, str] | None:
    password = os.environ.get("WEB_PASSWORD", "")
    if not password:
        return None
    return os.environ.get("WEB_USERNAME", "agent"), password


class RequestHandler(BaseHTTPRequestHandler):
    agent_service: AgentService

    server_version = "taleclaw/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._authorize():
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({
                "ok": True,
                "workspace": str(ROOT),
                "runtime": "lazy",
                "has_env_file": (ROOT / ".env").exists(),
                "has_deepseek_key": bool(os.environ.get("DEEPSEEK_API_KEY")),
            })
            return

        if parsed.path == "/api/runtime-health":
            try:
                self.agent_service.ensure_started()
                self._send_json({
                    "ok": True,
                    "runtime": "ready",
                    "has_deepseek_key": bool(os.environ.get("DEEPSEEK_API_KEY")),
                })
            except Exception as exc:
                traceback.print_exc()
                self._send_json(
                    {
                        "ok": False,
                        "runtime": "error",
                        "error": _friendly_runtime_error(exc),
                        "error_type": type(exc).__name__,
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return

        if parsed.path == "/api/sessions":
            self._send_json({"sessions": read_sessions()})
            return

        if parsed.path == "/api/session":
            params = parse_qs(parsed.query)
            session_id = params.get("session_id", ["default"])[0]
            raw = params.get("raw", ["0"])[0] == "1"
            self._send_json({"session": read_session(session_id, raw=raw)})
            return

        if parsed.path == "/api/memory":
            self._send_json({"files": read_memory_files()})
            return

        if parsed.path == "/api/files":
            params = parse_qs(parsed.query)
            path = params.get("path", [""])[0]
            try:
                self._send_json({"files": list_storage(path)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/files/preview":
            self._handle_file_preview(parsed)
            return

        if parsed.path == "/api/files/download":
            self._handle_file_download(parsed)
            return

        self._send_static(parsed.path)

    def do_POST(self) -> None:
        if not self._authorize():
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
            return

        if parsed.path == "/api/chat/stream":
            self._handle_chat_stream()
            return

        if parsed.path == "/api/analyze":
            self._handle_analyze()
            return

        if parsed.path == "/api/files/upload":
            self._handle_file_upload()
            return

        if parsed.path == "/api/files/mkdir":
            self._handle_mkdir()
            return

        if parsed.path == "/api/files/rename":
            self._handle_rename()
            return

        if parsed.path == "/api/files/delete":
            self._handle_delete()
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if not self._authorize():
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/session":
            self._handle_session_delete()
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_chat(self) -> None:
        try:
            payload = self._read_json_body()
            message = str(payload.get("message", "")).strip()
            session_id = str(payload.get("session_id", "default")).strip() or "default"
            if not message:
                self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                return

            reply = self.agent_service.ask(session_id=session_id, content=message)
            self._send_json({
                "reply": reply,
                "session_id": session_id,
                "session": read_session(session_id),
            })
        except Exception as exc:
            traceback.print_exc()
            self._send_json(
                {
                    "error": _friendly_runtime_error(exc),
                    "error_type": type(exc).__name__,
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def _handle_session_delete(self) -> None:
        try:
            payload = self._read_json_body()
            storage_id = _web_storage_id(payload.get("session_id", ""))
            if not self.agent_service.delete_session(storage_id):
                self._send_json({"error": "Session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({
                "deleted": True,
                "session_id": storage_id.removeprefix("web:"),
                "sessions": read_sessions(),
            })
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self._send_json(
                {
                    "error": _friendly_runtime_error(exc),
                    "error_type": type(exc).__name__,
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def _handle_chat_stream(self) -> None:
        try:
            payload = self._read_json_body()
            message = str(payload.get("message", "")).strip()
            session_id = str(payload.get("session_id", "default")).strip() or "default"
            if not message:
                self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                return
        except Exception as exc:
            self._send_json(
                {"error": str(exc), "error_type": type(exc).__name__},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def run_agent() -> None:
            try:
                reply = self.agent_service.ask_stream(
                    session_id=session_id,
                    content=message,
                    on_text=lambda text: events.put({"type": "delta", "text": text}),
                )
                events.put({
                    "type": "complete",
                    "reply": reply,
                    "session_id": session_id,
                    "session": read_session(session_id),
                })
            except Exception as exc:
                traceback.print_exc()
                events.put({
                    "type": "error",
                    "error": _friendly_runtime_error(exc),
                    "error_type": type(exc).__name__,
                })

        worker = threading.Thread(
            target=run_agent,
            name=f"agent-web-stream-{session_id}",
            daemon=True,
        )
        worker.start()

        self._send_stream_headers()
        while True:
            event = events.get()
            try:
                self._send_stream_event(event)
            except (BrokenPipeError, ConnectionResetError):
                return
            if event["type"] in {"complete", "error"}:
                return

    def _handle_analyze(self) -> None:
        try:
            payload = self._read_json_body()
            text = str(payload.get("text", "")).strip()
            session_id = str(payload.get("session_id", "analysis")).strip() or "analysis"
            if not text:
                self._send_json({"error": "text is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            prompt = (
                "请分析下面这段文字，给出清晰、有结构的回复。"
                "请保留关键事实、判断可能的问题，并给出可执行建议。\n\n"
                f"{text}"
            )
            reply = self.agent_service.ask(session_id=session_id, content=prompt)
            record_path = append_analysis_record(user_text=text, assistant_text=reply)
            self._send_json({
                "reply": reply,
                "record_path": record_path,
                "record_download_url": "/api/files/download?path=" + quote(record_path),
            })
        except Exception as exc:
            traceback.print_exc()
            self._send_json(
                {
                    "error": _friendly_runtime_error(exc),
                    "error_type": type(exc).__name__,
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def _handle_file_preview(self, parsed) -> None:
        try:
            params = parse_qs(parsed.query)
            path = _safe_storage_path(params.get("path", [""])[0])
            if not path.exists() or not path.is_file():
                self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if not _is_previewable(path, mime):
                self._send_json({"error": "Preview is not available"}, status=HTTPStatus.BAD_REQUEST)
                return
            if path.stat().st_size > MAX_PREVIEW_BYTES:
                self._send_json({"error": "File is too large to preview"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({
                "name": path.name,
                "path": _storage_rel(path),
                "content": path.read_text(encoding="utf-8", errors="replace"),
                "mime": mime,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_file_download(self, parsed) -> None:
        try:
            params = parse_qs(parsed.query)
            path = _safe_storage_path(params.get("path", [""])[0])
            if not path.exists() or not path.is_file():
                self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            safe_name = quote(path.name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{safe_name}")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_file_upload(self) -> None:
        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                self._send_json({"error": "Upload is too large"}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                },
            )
            target_dir = _safe_storage_path(form.getfirst("path", ""))
            target_dir.mkdir(parents=True, exist_ok=True)
            if not target_dir.is_dir():
                self._send_json({"error": "Target path is not a directory"}, status=HTTPStatus.BAD_REQUEST)
                return
            files = form["file"] if "file" in form else []
            if not isinstance(files, list):
                files = [files]
            saved = []
            for item in files:
                if not getattr(item, "filename", ""):
                    continue
                filename = Path(item.filename).name
                dest = (target_dir / filename).resolve()
                if dest != STORAGE_DIR and not dest.is_relative_to(STORAGE_DIR):
                    raise ValueError("Path escapes storage.")
                with dest.open("wb") as handle:
                    shutil.copyfileobj(item.file, handle)
                saved.append(_entry_for(dest))
            self._send_json({"saved": saved, "files": list_storage(_storage_rel(target_dir))})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_mkdir(self) -> None:
        try:
            payload = self._read_json_body()
            parent = _safe_storage_path(payload.get("path", ""))
            name = Path(str(payload.get("name", "")).strip()).name
            if not name:
                self._send_json({"error": "name is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            target = (parent / name).resolve()
            if target != STORAGE_DIR and not target.is_relative_to(STORAGE_DIR):
                raise ValueError("Path escapes storage.")
            target.mkdir(parents=True, exist_ok=True)
            self._send_json({"files": list_storage(_storage_rel(parent))})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_rename(self) -> None:
        try:
            payload = self._read_json_body()
            source = _safe_storage_path(payload.get("path", ""))
            name = Path(str(payload.get("name", "")).strip()).name
            if not name:
                self._send_json({"error": "name is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            if source == STORAGE_DIR:
                self._send_json({"error": "Cannot rename storage root"}, status=HTTPStatus.BAD_REQUEST)
                return
            target = (source.parent / name).resolve()
            if target != STORAGE_DIR and not target.is_relative_to(STORAGE_DIR):
                raise ValueError("Path escapes storage.")
            if target.exists():
                raise FileExistsError("Target already exists.")
            source.rename(target)
            self._send_json({"files": list_storage(_storage_rel(target.parent))})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_delete(self) -> None:
        try:
            payload = self._read_json_body()
            target = _safe_storage_path(payload.get("path", ""))
            if target == STORAGE_DIR:
                self._send_json({"error": "Cannot delete storage root"}, status=HTTPStatus.BAD_REQUEST)
                return
            parent = target.parent
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            self._send_json({"files": list_storage(_storage_rel(parent))})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_headers(self) -> None:
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _send_stream_event(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        self.wfile.write(line)
        self.wfile.flush()

    def _send_static(self, request_path: str) -> None:
        target = self._static_target(request_path)
        if target is None:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static_target(self, request_path: str) -> Path | None:
        path = unquote(request_path.split("?", 1)[0])
        if path in {"", "/"}:
            relative = "index.html"
        else:
            relative = path.lstrip("/")
            if relative.startswith("static/"):
                relative = relative.removeprefix("static/")

        candidate = (STATIC_DIR / relative).resolve()
        if candidate.is_relative_to(STATIC_DIR) and candidate.is_file():
            return candidate

        fallback = STATIC_DIR / "index.html"
        if fallback.exists() and "." not in Path(relative).name:
            return fallback
        return None

    def _authorize(self) -> bool:
        credentials = auth_credentials()
        if credentials is None:
            return True

        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "basic" and token:
            try:
                decoded = base64.b64decode(token).decode("utf-8")
            except Exception:
                decoded = ""
            username, _, password = decoded.partition(":")
            expected_username, expected_password = credentials
            if (
                hmac.compare_digest(username, expected_username)
                and hmac.compare_digest(password, expected_password)
            ):
                return True

        body = json.dumps({"error": "Authentication required"}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", f'Basic realm="{AUTH_REALM}", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))


def build_handler(agent_service: AgentService):
    class BoundRequestHandler(RequestHandler):
        pass

    BoundRequestHandler.agent_service = agent_service
    return BoundRequestHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local agent web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    agent_service = AgentService()
    server = ThreadingHTTPServer((args.host, args.port), build_handler(agent_service))
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print(f"taleclaw running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        agent_service.stop()
        server.server_close()


if __name__ == "__main__":
    main()
