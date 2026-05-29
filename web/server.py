from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import mimetypes
import os
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web" / "static"
SESSIONS_DB = ROOT / ".sessions" / "sessions.db"
MEMORY_DIR = ROOT / "memory"
MEMORY_FILES = [
    "SELF.md",
    "MEMORY.md",
    "NOW.md",
    "PENDING.md",
    "RECENT_CONTEXT.md",
    "HISTORY.md",
]
MAX_BODY_BYTES = 1_000_000
AUTH_REALM = "Agent Web"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
        self.ensure_started()
        if self._loop is None:
            raise RuntimeError("Agent runtime loop is not available.")

        future = asyncio.run_coroutine_threadsafe(
            self._ask_async(session_id=session_id, content=content),
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

    async def _ask_async(self, *, session_id: str, content: str) -> str:
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
                await self._runtime.run_once()
                return await asyncio.wait_for(reply_future, timeout=10)
            finally:
                self._pending.pop(session_id, None)


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
        channel, _, chat_id = row["id"].partition(":")
        sessions.append({
            **row,
            "channel": channel if chat_id else "",
            "chat_id": chat_id if chat_id else row["id"],
            "can_chat": channel == "web" and bool(chat_id),
        })
    return sessions


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
    return session


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


def auth_credentials() -> tuple[str, str] | None:
    password = os.environ.get("WEB_PASSWORD", "")
    if not password:
        return None
    return os.environ.get("WEB_USERNAME", "agent"), password


class RequestHandler(BaseHTTPRequestHandler):
    agent_service: AgentService

    server_version = "AgentWeb/0.1"

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

        self._send_static(parsed.path)

    def do_POST(self) -> None:
        if not self._authorize():
            return

        parsed = urlparse(self.path)
        if parsed.path != "/api/chat":
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

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
            self._send_json(
                {"error": _friendly_runtime_error(exc)},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

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
    print(f"Agent web UI running at {url}")
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
