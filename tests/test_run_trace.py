import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bus.events import InboundMessage
from bus.user_bus import MessageBus
from plugins import PluginManager
from plugins.run_report import RunReportPlugin
from runtime.agent_loop import AgentLoop
from runtime.pipeline import Pipeline
from models.provider import LLMResponse, ToolCall
from runtime.trace.run_state import RunState
from runtime.trace.trace_store import TraceStore
from runtime.trace.workspace import capture_workspace_snapshot, diff_workspace_snapshots
from runtime.workspace import WorkspaceResolver
from memory.store import MemoryStore
from runtime.routing.router import ModeRouter
from sessions.session import Session, SessionManager
from agents.coding.conclusions import ConclusionExtraction
from agents.coding.promotion import PromotionResult
from agents.coding.runner import TaskSessionRunner
from agents.coding.session import TaskSessionFactory
from tools.executor import ToolExecutor
from tools.handlers import run_read, run_write
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


class ContextBuilder:
    def build(self, **kwargs):
        return SimpleNamespace(messages=kwargs["session"].messages)


class ScriptedProvider:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake response queued.")
        return self.responses.pop(0)


class RecordingSessions:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.saved = []

    def get_or_create(self, session_id: str) -> Session:
        if session_id != self.session.id:
            raise AssertionError(f"unexpected session id: {session_id}")
        return self.session

    def save(self, session: Session) -> None:
        self.saved.append(session)


def _tool_response(index: int, name="echo", arguments=None) -> LLMResponse:
    arguments = arguments or {"text": "hello"}
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=f"call-{index}", name=name, arguments=arguments)],
        raw_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call-{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        },
    )


def _final_response(content="done") -> LLMResponse:
    return LLMResponse(
        content=content,
        raw_message={"role": "assistant", "content": content},
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        function_tool("echo", "Echo text.", {"text": {"type": "string"}}, ["text"]),
        lambda **kwargs: f"echo: {kwargs['text']}",
        enabled_modes={"bot", "coding"},
        always_on=True,
    )
    return registry


def _pipeline(provider) -> Pipeline:
    return Pipeline(
        tools=_registry(),
        provider=provider,
        model="test-model",
        tool_executor=ToolExecutor([]),
        context_builder=ContextBuilder(),
    )


def _workspace_pipeline(provider) -> Pipeline:
    registry = ToolRegistry()
    registry.register(
        function_tool(
            "write_file",
            "Write content to a file.",
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ),
        lambda **kwargs: run_write(
            kwargs["path"],
            kwargs["content"],
            _session=kwargs.get("_session"),
        ),
        enabled_modes={"coding"},
        always_on=True,
    )
    registry.register(
        function_tool(
            "read_file",
            "Read a file.",
            {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            ["path"],
        ),
        lambda **kwargs: run_read(
            kwargs["path"],
            kwargs.get("limit"),
            _session=kwargs.get("_session"),
        ),
        enabled_modes={"coding"},
    )
    return Pipeline(
        tools=registry,
        provider=provider,
        model="test-model",
        tool_executor=ToolExecutor([]),
        context_builder=ContextBuilder(),
    )


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RunTraceTests(unittest.TestCase):
    def test_agent_loop_writes_run_state_trace_and_report_for_tool_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_store = TraceStore(Path(tmp) / ".runs")
            session = Session(id="web:default", current_mode="bot")
            sessions = RecordingSessions(session)
            provider = ScriptedProvider([
                _tool_response(1, arguments={"text": "hello"}),
                _final_response("done"),
            ])
            bus = MessageBus()
            loop = AgentLoop(
                bus,
                sessions,
                _pipeline(provider),
                ModeRouter(),
                plugin_manager=PluginManager(
                    [RunReportPlugin()],
                    workspace=Path(tmp),
                    tool_registry=_registry(),
                ),
                trace_store=trace_store,
            )

            async def run() -> str:
                await bus.publish_inbound(InboundMessage(
                    channel="web",
                    chat_id="default",
                    sender="user",
                    content="please echo",
                    metadata={"user_id": "local", "user_role": "admin"},
                ))
                await loop.run_once()
                outbound = await bus._outbound.get()
                return outbound.content

            reply = asyncio.run(run())

            self.assertEqual("done", reply)
            run_id = session.metadata["last_run_id"]
            run_dir = trace_store.run_dir(run_id)
            state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            markdown_report = (run_dir / "report.md").read_text(encoding="utf-8")
            event_names = [event["event"] for event in _events(run_dir / "trace.jsonl")]

            self.assertEqual("completed", state["status"])
            self.assertEqual(2, state["reasoning_steps"])
            self.assertEqual(1, state["tool_calls"])
            self.assertEqual("echo", state["last_tool"])
            self.assertEqual("done", state["final_answer"])
            self.assertEqual("pipeline_bot", state["execution_path"])
            self.assertEqual("local", state["user_id"])
            self.assertEqual("pipeline_bot", report["report"]["execution_path"])
            self.assertIn("run.started", event_names)
            self.assertEqual(1, event_names.count("run.started"))
            self.assertNotIn("run_started", event_names)
            self.assertIn("model_requested", event_names)
            self.assertIn("model_returned", event_names)
            self.assertIn("tool_executed", event_names)
            self.assertIn("reasoning.step.started", event_names)
            self.assertIn("model.call.started", event_names)
            self.assertIn("model.call.completed", event_names)
            self.assertIn("tool.call.started", event_names)
            self.assertIn("tool.call.completed", event_names)
            self.assertIn("run.completed", event_names)
            self.assertIn("run_finished", event_names)
            self.assertEqual(2, metrics["model_calls"])
            self.assertEqual(1, metrics["tool_calls"])
            self.assertEqual(["echo"], metrics["tools"])
            self.assertIn("# Run Report", markdown_report)
            self.assertIn("please echo", markdown_report)
            self.assertIn("Model Activity", markdown_report)
            self.assertIn("Tool Activity", markdown_report)
            self.assertIn("echo", markdown_report)
            self.assertIn("done", markdown_report)

    def test_coding_task_session_is_bound_to_parent_run(self) -> None:
        class Extractor:
            def extract(self, **kwargs):
                return ConclusionExtraction(summary="No durable conclusions.")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = SessionManager(root / ".sessions" / "sessions.db")
            provider = ScriptedProvider([_final_response("coding done")])
            base_pipeline = _pipeline(provider)
            runner = TaskSessionRunner(
                sessions=sessions,
                base_pipeline=base_pipeline,
                global_memory=MemoryStore(root / "memory"),
                workspace_root=root,
            )
            runner.factory = TaskSessionFactory(sessions, root=root / ".task_sessions")
            runner.conclusion_extractor = Extractor()
            trace_store = TraceStore(root / ".runs")
            parent = Session(
                id="web:default",
                current_mode="coding",
                metadata={"user_id": "local", "user_role": "admin"},
            )
            run_state = RunState.create(
                session_id=parent.id,
                channel="web",
                chat_id="default",
                mode="coding",
                execution_path="task_session",
            )
            trace_store.start_run(run_state)
            try:
                reply = runner.run_coding_task(
                    parent_session=parent,
                    user_text="fix code",
                    profile=SimpleNamespace(
                        name="coding",
                        tool_mode="coding",
                        system_prompt="coding",
                    ),
                    run_state=run_state,
                    trace_store=trace_store,
                )
            finally:
                sessions.close()

            run_state.finish_success(reply)
            trace_store.write_run_state(run_state)
            trace_store.write_report(run_state, {"reply": reply})
            run_dir = trace_store.run_dir(run_state)
            state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            event_names = [event["event"] for event in _events(run_dir / "trace.jsonl")]
            workspace_diff = json.loads(
                (run_dir / "workspace_diff.json").read_text(encoding="utf-8")
            )
            task_session_id = state["metadata"]["task_session"]["task_session_id"]
            task_session = SessionManager(root / ".sessions" / "sessions.db")
            try:
                stored = task_session.get_or_create(task_session_id)
            finally:
                task_session.close()

            self.assertIn("coding done", reply)
            self.assertEqual(run_state.run_id, stored.metadata["parent_run_id"])
            self.assertEqual("completed", state["metadata"]["task_session"]["status"])
            self.assertIn("task_session_started", event_names)
            self.assertIn("task_session_completed", event_names)
            self.assertIn("workspace.snapshot.captured", event_names)
            self.assertIn("workspace.diff.written", event_names)
            self.assertIn("model_requested", event_names)
            self.assertIn("summary", workspace_diff)

    def test_trace_records_context_sanitizer_drops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_store = TraceStore(Path(tmp) / ".runs")
            session = Session(id="web:default", current_mode="bot")
            session.messages.append({"role": "assistant", "content": None})
            sessions = RecordingSessions(session)
            provider = ScriptedProvider([_final_response("clean")])
            bus = MessageBus()
            loop = AgentLoop(
                bus,
                sessions,
                _pipeline(provider),
                ModeRouter(),
                trace_store=trace_store,
            )

            async def run() -> None:
                await bus.publish_inbound(InboundMessage(
                    channel="web",
                    chat_id="default",
                    sender="user",
                    content="hello",
                    metadata={"user_id": "local", "user_role": "admin"},
                ))
                await loop.run_once()
                await bus._outbound.get()

            asyncio.run(run())

            run_dir = trace_store.run_dir(session.metadata["last_run_id"])
            metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            events = _events(run_dir / "trace.jsonl")
            event_names = [event["event"] for event in events]
            sent_messages = provider.calls[0]["messages"]

            self.assertIn("context.sanitized", event_names)
            self.assertEqual(1, metrics["sanitized_messages"])
            self.assertNotIn({"role": "assistant", "content": None}, sent_messages)

    def test_markdown_report_records_failed_run_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_store = TraceStore(Path(tmp) / ".runs")
            run_state = RunState.create(
                session_id="web:default",
                channel="web",
                chat_id="default",
                mode="bot",
                execution_path="pipeline_bot",
            )
            trace_store.start_run(run_state)
            trace_store.append_event(run_state, "inbound_received", {
                "content_preview": "break please",
                "metadata": {"user_id": "local"},
            })
            trace_store.append_event(run_state, "model.call.failed", {
                "model": "test-model",
                "provider": "TestProvider",
                "duration_ms": 12.3,
                "error_type": "RuntimeError",
                "error_message": "model exploded",
                "route_attempts": [{
                    "profile": "primary",
                    "provider": "test",
                    "model": "test-model",
                    "status": "failed",
                }],
            }, step=1)
            run_state.fail(RuntimeError("model exploded"))
            trace_store.write_run_state(run_state)
            trace_store.write_report(run_state, {"execution_path": "pipeline_bot"})
            plugin_manager = PluginManager(
                [RunReportPlugin()],
                workspace=Path(tmp),
                tool_registry=_registry(),
            )
            plugin_manager.after_run(
                run_state=run_state,
                session=Session(id="web:default"),
                run_dir=trace_store.run_dir(run_state),
                report={"execution_path": "pipeline_bot"},
            )

            markdown_report = (
                trace_store.run_dir(run_state) / "report.md"
            ).read_text(encoding="utf-8")

            self.assertIn("# Run Report", markdown_report)
            self.assertIn("break please", markdown_report)
            self.assertIn("Run Error", markdown_report)
            self.assertIn("model exploded", markdown_report)
            self.assertIn("Route attempts", markdown_report)

    def test_trace_store_does_not_write_markdown_report_without_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_store = TraceStore(Path(tmp) / ".runs")
            run_state = RunState.create(session_id="web:default")
            trace_store.start_run(run_state)
            run_state.finish_success("done")
            trace_store.write_run_state(run_state)
            trace_store.write_report(run_state, {"reply": "done"})

            self.assertFalse((trace_store.run_dir(run_state) / "report.md").exists())

    def test_workspace_diff_detects_created_modified_and_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = root / "created.txt"
            modified = root / "modified.txt"
            deleted = root / "deleted.txt"
            ignored_dir = root / ".runs"
            ignored_dir.mkdir()
            (ignored_dir / "trace.jsonl").write_text("ignore me", encoding="utf-8")
            modified.write_text("before", encoding="utf-8")
            deleted.write_text("remove", encoding="utf-8")

            before = capture_workspace_snapshot(root)
            created.write_text("new", encoding="utf-8")
            modified.write_text("after", encoding="utf-8")
            deleted.unlink()
            after = capture_workspace_snapshot(root)

            diff = diff_workspace_snapshots(before, after)

            self.assertEqual(["created.txt"], diff["created"])
            self.assertEqual(["deleted.txt"], diff["deleted"])
            self.assertEqual(["modified.txt"], [
                item["path"] for item in diff["modified"]
            ])
            self.assertNotIn(".runs/trace.jsonl", diff["created"])

    def test_coding_task_uses_requested_workspace_for_tools_and_diff(self) -> None:
        class Extractor:
            def extract(self, **kwargs):
                return ConclusionExtraction(summary="No durable conclusions.")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "project"
            workspace.mkdir()
            sessions = SessionManager(root / ".sessions" / "sessions.db")
            provider = ScriptedProvider([
                _tool_response(
                    1,
                    name="write_file",
                    arguments={
                        "path": "src/answer.txt",
                        "content": "created in real workspace",
                    },
                ),
                _final_response("created file"),
            ])
            runner = TaskSessionRunner(
                sessions=sessions,
                base_pipeline=_workspace_pipeline(provider),
                global_memory=MemoryStore(root / "memory"),
                workspace_root=workspace,
            )
            runner.factory = TaskSessionFactory(sessions, root=root / ".task_sessions")
            runner.conclusion_extractor = Extractor()
            trace_store = TraceStore(root / ".runs")
            parent = Session(
                id="web:default",
                current_mode="coding",
                metadata={"user_id": "local", "user_role": "admin"},
            )
            run_state = RunState.create(
                session_id=parent.id,
                channel="web",
                chat_id="default",
                mode="coding",
                execution_path="task_session",
            )
            trace_store.start_run(run_state)
            try:
                runner.run_coding_task(
                    parent_session=parent,
                    user_text="create answer file",
                    profile=SimpleNamespace(
                        name="coding",
                        tool_mode="coding",
                        system_prompt="coding",
                    ),
                    workspace_root=str(workspace),
                    run_state=run_state,
                    trace_store=trace_store,
                )
            finally:
                sessions.close()
            run_state.finish_success("created file")
            trace_store.write_run_state(run_state)
            trace_store.write_report(run_state)

            run_dir = trace_store.run_dir(run_state)
            diff = json.loads((run_dir / "workspace_diff.json").read_text(encoding="utf-8"))
            events = _events(run_dir / "trace.jsonl")

            self.assertEqual("created in real workspace", (workspace / "src/answer.txt").read_text())
            self.assertIn("src/answer.txt", diff["created"])
            self.assertIn("workspace.resolved", [event["event"] for event in events])
            self.assertEqual(str(workspace.resolve()), parent.metadata["workspace_root"])

    def test_workspace_file_tools_reject_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "project"
            outside = root / "outside.txt"
            workspace.mkdir()
            session = Session(
                id="task:test",
                metadata={"workspace_root": str(workspace)},
            )

            result = run_write("../outside.txt", "nope", _session=session)

            self.assertIn("Path escapes workspace", result)
            self.assertFalse(outside.exists())

    def test_workspace_resolver_falls_back_to_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "default-project"
            workspace.mkdir()
            resolver = WorkspaceResolver(
                allowed_roots=[workspace],
                default_workspace=workspace,
            )

            resolved = resolver.resolve()

            self.assertEqual(workspace.resolve(), resolved.root)
            self.assertEqual("default", resolved.source)


if __name__ == "__main__":
    unittest.main()
