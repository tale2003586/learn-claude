import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.context import ContextBuilder
from core.pipeline import Pipeline
from core.provider import LLMResponse, ToolCall
from memory.store import MemoryStore
from plugins.scheduler.agent_runner import ScheduledAgentRunner
from plugins.scheduler.planning import (
    ScheduledTaskDraft,
    ScheduledTaskPlan,
    ToolCapabilityAuditor,
)
from plugins.scheduler.plugin import SchedulerPlugin
from plugins.scheduler.policy import ToolApprovalPolicyHook
from plugins.scheduler.reports import ScheduledReportService
from plugins.scheduler.store import ScheduleStore
from plugins.scheduler.workflow import WorkflowExecutor, validate_workflow
from plugins.web_search.client import TavilySearchClient
from plugins.web_search.plugin import WebSearchPlugin
from gateway.telegram.store import TelegramGatewayStore
from scheduler_worker import SchedulerWorker, TelegramScheduleNotifier
from sessions import SessionManager
from tasksessions.conclusions import ConclusionExtraction
from tasksessions.promotion import PromotionResult
from tools.executor import ToolExecutionRequest, ToolExecutor
from tools.schema import function_tool
from tools.tool_registry import ToolRegistry


class FakeSearchClient:
    def __init__(self) -> None:
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "title": "AI research update",
                "url": "https://example.com/ai",
                "snippet": "A useful update.",
                "score": 0.95,
            }
        ]


class FakeAnalysisClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return "## Trend\n\nAI Agent tooling is maturing."


def _register_tool(
    registry: ToolRegistry,
    name: str,
    *,
    risk: str = "low",
    handler=None,
    enabled_modes: set[str] | None = None,
) -> None:
    registry.register(
        function_tool(name, f"{name} description", {}, []),
        handler or (lambda **kwargs: "ok"),
        risk=risk,
        enabled_modes=enabled_modes,
        source=f"test:{name}",
    )


class TavilySearchClientTests(unittest.TestCase):
    def test_search_builds_compact_tavily_request(self) -> None:
        calls = []

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "results": [{
                        "title": "Title",
                        "url": "https://example.com",
                        "content": "x" * 1300,
                        "score": 0.8,
                    }]
                }

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return Response()

        results = TavilySearchClient(api_key="secret", post=post).search(
            query=" latest AI ",
            topic="news",
            max_results=99,
            time_range="day",
        )

        self.assertEqual("https://api.tavily.com/search", calls[0][0][0])
        self.assertEqual("Bearer secret", calls[0][1]["headers"]["Authorization"])
        self.assertEqual("latest AI", calls[0][1]["json"]["query"])
        self.assertEqual(8, calls[0][1]["json"]["max_results"])
        self.assertEqual(1200, len(results[0]["snippet"]))


class ScheduleStoreTests(unittest.TestCase):
    def test_create_list_and_delete_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(Path(tmp) / "schedules.db")

            created = store.create(
                name="daily-ai",
                query="latest AI news",
                hour=8,
            )

            self.assertEqual(8, created["hour"])
            self.assertEqual("Asia/Shanghai", created["timezone"])
            self.assertEqual([created], store.list_schedules())
            self.assertTrue(store.delete(created["id"]))
            self.assertEqual([], store.list_schedules())

    def test_create_rejects_invalid_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(Path(tmp) / "schedules.db")

            with self.assertRaisesRegex(ValueError, "hour"):
                store.create(
                    name="bad",
                    query="latest AI news",
                    hour=24,
                )

    def test_existing_database_is_migrated_to_default_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "schedules.db"
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE schedules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        query TEXT NOT NULL,
                        topic TEXT NOT NULL DEFAULT 'news',
                        max_results INTEGER NOT NULL DEFAULT 5,
                        time_range TEXT,
                        hour INTEGER NOT NULL,
                        minute INTEGER NOT NULL DEFAULT 0,
                        timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_run_at TEXT,
                        last_status TEXT,
                        last_report_path TEXT,
                        last_error TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO schedules (
                        name, query, topic, max_results, time_range, hour, minute,
                        timezone, created_at, updated_at
                    ) VALUES (
                        'legacy', 'AI news', 'news', 5, 'day', 8, 0,
                        'Asia/Shanghai', 'created', 'updated'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE schedule_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        schedule_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        report_path TEXT,
                        error TEXT
                    )
                    """
                )

            store = ScheduleStore(db_path)
            schedule = store.get(1)

            self.assertEqual("web_search", schedule["workflow"][0]["type"])
            self.assertEqual("write_report", schedule["workflow"][-1]["type"])
            self.assertEqual("workflow", schedule["schedule_type"])
            self.assertEqual("active", schedule["approval_status"])
            self.assertEqual([], schedule["requested_tools"])
            self.assertEqual([], schedule["approved_capabilities"])
            self.assertEqual({}, schedule["limits"])
            self.assertEqual({}, schedule["plan"])

            with sqlite3.connect(db_path) as conn:
                schedule_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(schedules)")
                }
                run_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(schedule_runs)")
                }

            self.assertTrue({
                "schedule_type",
                "task_prompt",
                "approval_status",
                "requested_tools_json",
                "approved_capabilities_json",
                "limits_json",
                "plan_json",
            }.issubset(schedule_columns))
            self.assertTrue({
                "task_session_id",
                "trace_path",
                "approval_request_json",
            }.issubset(run_columns))

    def test_run_metadata_round_trips_for_future_agent_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(Path(tmp) / "schedules.db")
            schedule = store.create(
                name="daily-ai",
                query="latest AI news",
                hour=8,
            )

            run_id = store.begin_run(
                schedule,
                task_session_id="task:scheduled-1-run-1",
            )
            store.complete_run(
                run_id=run_id,
                schedule_id=schedule["id"],
                status="awaiting_runtime_approval",
                trace_path=".task_sessions/scheduled-1-run-1/TRACE.json",
                approval_request={
                    "tool": "bash",
                    "arguments": {"command": "python scripts/report.py"},
                },
            )

            run = store.list_runs(schedule_id=schedule["id"])[0]

            self.assertEqual("task:scheduled-1-run-1", run["task_session_id"])
            self.assertEqual(
                ".task_sessions/scheduled-1-run-1/TRACE.json",
                run["trace_path"],
            )
            self.assertEqual("bash", run["approval_request"]["tool"])

    def test_agent_draft_can_be_approved_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(Path(tmp) / "schedules.db")
            schedule = store.create_agent_draft(
                name="daily-agent",
                task_prompt="Generate a report.",
                hour=8,
                plan={"summary": "Daily report"},
                approval_status="awaiting_approval",
                requested_tools=["web_search", "read_file"],
                approved_capabilities=[{
                    "tool": "web_search",
                    "risk": "low",
                    "scope": {},
                }],
                limits={"max_tool_calls": 8},
            )

            self.assertEqual("agent", schedule["schedule_type"])
            self.assertEqual([], schedule["workflow"])
            self.assertEqual("Daily report", schedule["plan"]["summary"])
            self.assertEqual([schedule], store.list_pending_agents())

            approved = store.update_agent_approval(
                schedule["id"],
                approved_capabilities=[
                    {
                        "tool": "read_file",
                        "risk": "normal",
                        "scope": {"paths": ["docs"]},
                    },
                    {
                        "tool": "web_search",
                        "risk": "low",
                        "scope": {},
                    },
                ],
                approval_status="active",
            )
            self.assertEqual("active", approved["approval_status"])
            self.assertEqual([], store.list_pending_agents())

            rejected = store.reject_agent(schedule["id"])
            self.assertEqual("rejected", rejected["approval_status"])
            self.assertFalse(rejected["enabled"])


class ToolApprovalPolicyHookTests(unittest.TestCase):
    def test_hook_pauses_unapproved_or_out_of_scope_tool_call(self) -> None:
        metadata = {
            "kind": "scheduled_agent",
            "automation_limits": {"max_tool_calls": 4, "timeout_seconds": 300},
            "approved_capabilities": [{
                "tool": "bash",
                "risk": "high",
                "scope": {"commands": ["python scripts/report.py"]},
            }],
        }
        hook = ToolApprovalPolicyHook()

        allowed = hook.before(ToolExecutionRequest(
            call_id="1",
            tool_name="bash",
            arguments={"command": "python scripts/report.py"},
            metadata=metadata,
        ))
        denied = hook.before(ToolExecutionRequest(
            call_id="2",
            tool_name="bash",
            arguments={"command": "rm -rf storage"},
            metadata=metadata,
        ))

        self.assertIsNone(allowed.deny_reason)
        self.assertIn("paused for approval", denied.deny_reason)
        self.assertEqual("bash", metadata["runtime_approval_request"]["tool"])

    def test_hook_blocks_forbidden_tool_even_if_metadata_is_tampered(self) -> None:
        hook = ToolApprovalPolicyHook()
        outcome = hook.before(ToolExecutionRequest(
            call_id="1",
            tool_name="schedule_create",
            arguments={},
            metadata={
                "kind": "scheduled_agent",
                "approved_capabilities": [{
                    "tool": "schedule_create",
                    "risk": "low",
                    "scope": {},
                }],
            },
        ))

        self.assertIn("forbidden", outcome.deny_reason)


class ScheduledAgentRunnerTests(unittest.TestCase):
    def _base_pipeline(self, *, workspace: Path, provider, registry: ToolRegistry) -> Pipeline:
        return Pipeline(
            tools=registry,
            provider=provider,
            model="test-model",
            tool_executor=ToolExecutor([]),
            context_builder=ContextBuilder(memory_store=MemoryStore(workspace / "memory")),
        )

    def _runner(self, *, workspace: Path, store, sessions, provider, registry):
        class Extractor:
            def extract(self, **kwargs):
                return ConclusionExtraction(summary="Completed report.")

        class Promoter:
            def promote(self, **kwargs):
                return PromotionResult()

        return ScheduledAgentRunner(
            store=store,
            sessions=sessions,
            base_pipeline=self._base_pipeline(
                workspace=workspace,
                provider=provider,
                registry=registry,
            ),
            global_memory=MemoryStore(workspace / "global_memory"),
            workspace=workspace,
            conclusion_extractor=Extractor(),
            promoter=Promoter(),
        )

    def test_runner_executes_approved_tool_in_isolated_session(self) -> None:
        class Provider:
            def __init__(self):
                self.calls = 0

            def chat(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        content=None,
                        tool_calls=[ToolCall(
                            id="call-1",
                            name="web_search",
                            arguments={"query": "latest AI"},
                        )],
                        raw_message={
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"latest AI"}',
                                },
                            }],
                        },
                    )
                return LLMResponse(
                    content="## Daily report\n\nUseful findings.",
                    raw_message={
                        "role": "assistant",
                        "content": "## Daily report\n\nUseful findings.",
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = ScheduleStore(workspace / ".scheduler" / "schedules.db")
            schedule = store.create_agent_draft(
                name="daily-agent",
                task_prompt="Research AI news.",
                hour=8,
                plan={},
                approval_status="active",
                requested_tools=["web_search"],
                approved_capabilities=[{
                    "tool": "web_search",
                    "risk": "low",
                    "scope": {},
                }],
                limits={},
            )
            registry = ToolRegistry()
            _register_tool(
                registry,
                "web_search",
                handler=lambda **kwargs: '[{"url":"https://example.com"}]',
                enabled_modes={"coding"},
            )
            sessions = SessionManager(workspace / ".sessions" / "sessions.db")
            result = self._runner(
                workspace=workspace,
                store=store,
                sessions=sessions,
                provider=Provider(),
                registry=registry,
            ).run(schedule["id"])

            self.assertEqual("success", result["status"])
            self.assertTrue(result["task_session_id"].startswith("task:scheduled_agent-"))
            self.assertTrue((workspace / result["report_path"]).is_file())
            trace = json.loads((workspace / result["trace_path"]).read_text())
            self.assertEqual("success", trace[0]["status"])
            sessions.close()

    def test_runner_pauses_when_model_attempts_unapproved_tool(self) -> None:
        class Provider:
            def chat(self, **kwargs):
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(
                        id="call-1",
                        name="bash",
                        arguments={"command": "python scripts/report.py"},
                    )],
                    raw_message={
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": '{"command":"python scripts/report.py"}',
                            },
                        }],
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = ScheduleStore(workspace / ".scheduler" / "schedules.db")
            schedule = store.create_agent_draft(
                name="daily-agent",
                task_prompt="Research AI news.",
                hour=8,
                plan={},
                approval_status="active",
                requested_tools=["web_search"],
                approved_capabilities=[{
                    "tool": "web_search",
                    "risk": "low",
                    "scope": {},
                }],
                limits={},
            )
            registry = ToolRegistry()
            _register_tool(registry, "bash", risk="high", enabled_modes={"coding"})
            sessions = SessionManager(workspace / ".sessions" / "sessions.db")
            result = self._runner(
                workspace=workspace,
                store=store,
                sessions=sessions,
                provider=Provider(),
                registry=registry,
            ).run(schedule["id"])

            self.assertEqual("awaiting_runtime_approval", result["status"])
            self.assertEqual("bash", result["approval_request"]["tool"])
            self.assertEqual(
                "awaiting_runtime_approval",
                store.list_runs(schedule_id=schedule["id"])[0]["status"],
            )
            self.assertEqual(
                "bash",
                store.list_runtime_approval_runs()[0]["approval_request"]["tool"],
            )
            sessions.close()


class ScheduledReportServiceTests(unittest.TestCase):
    def test_run_writes_markdown_and_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = ScheduleStore(workspace / ".scheduler" / "schedules.db")
            schedule = store.create(
                name="daily-ai",
                query="latest AI news",
                hour=8,
            )
            search = FakeSearchClient()
            service = ScheduledReportService(
                store=store,
                search_client=search,
                workspace=workspace,
            )

            result = service.run(schedule["id"])
            runs = service.recent_results(
                schedule_id=schedule["id"],
                include_content=True,
            )

            self.assertEqual("success", result["status"])
            self.assertEqual("latest AI news", search.calls[0]["query"])
            self.assertTrue((workspace / result["report_path"]).exists())
            self.assertIn("AI research update", runs[0]["content"])
            self.assertEqual("success", runs[0]["status"])

    def test_workflow_analysis_is_written_to_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = ScheduleStore(workspace / ".scheduler" / "schedules.db")
            schedule = store.create_workflow(
                name="daily-analysis",
                hour=8,
                workflow=[
                    {"type": "web_search", "query": "latest AI news"},
                    {"type": "llm_analyze", "prompt": "Analyze trends."},
                    {"type": "write_report", "title": "Daily AI Digest"},
                ],
            )
            analysis = FakeAnalysisClient()
            service = ScheduledReportService(
                store=store,
                workflow_executor=WorkflowExecutor(
                    search_client=FakeSearchClient(),
                    analysis_client=analysis,
                ),
                workspace=workspace,
            )

            result = service.run(schedule["id"])
            content = (workspace / result["report_path"]).read_text(encoding="utf-8")

            self.assertEqual("success", result["status"])
            self.assertIn("# Daily AI Digest", content)
            self.assertIn("AI Agent tooling is maturing.", content)
            self.assertEqual("Analyze trends.", analysis.calls[0]["prompt"])

    def test_analysis_failure_preserves_sources_as_partial_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = ScheduleStore(workspace / ".scheduler" / "schedules.db")
            schedule = store.create_workflow(
                name="daily-analysis",
                hour=8,
                workflow=[
                    {"type": "web_search", "query": "latest AI news"},
                    {"type": "llm_analyze", "prompt": "Analyze trends."},
                    {"type": "write_report"},
                ],
            )
            service = ScheduledReportService(
                store=store,
                workflow_executor=WorkflowExecutor(
                    search_client=FakeSearchClient(),
                    analysis_client=FakeAnalysisClient(error=RuntimeError("offline")),
                ),
                workspace=workspace,
            )

            result = service.run(schedule["id"])
            content = (workspace / result["report_path"]).read_text(encoding="utf-8")

            self.assertEqual("partial_success", result["status"])
            self.assertIn("Analysis failed", content)
            self.assertIn("https://example.com/ai", content)


class WorkflowValidationTests(unittest.TestCase):
    def test_workflow_rejects_shell_and_invalid_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_workflow([
                {"type": "bash", "command": "rm -rf /"},
                {"type": "write_report"},
            ])
        with self.assertRaisesRegex(ValueError, "earlier web_search"):
            validate_workflow([
                {"type": "llm_analyze", "prompt": "Analyze."},
                {"type": "write_report"},
            ])


class SchedulerPluginTests(unittest.TestCase):
    def test_plugin_exposes_scheduler_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = SchedulerPlugin()
            plugin.setup(SimpleNamespace(workspace=Path(tmp)))

            names = {
                registration.schema["function"]["name"]
                for registration in plugin.tools()
            }
            created = json.loads(plugin.schedule_create(
                name="daily-ai",
                query="latest AI news",
                hour=8,
            ))

            self.assertEqual({
                "schedule_create",
                "schedule_create_workflow",
                "schedule_create_agent_draft",
                "schedule_approve_agent",
                "schedule_approve_runtime",
                "schedule_reject_agent",
                "schedule_pending_approvals",
                "schedule_list",
                "schedule_delete",
                "schedule_run_now",
                "schedule_results",
            }, names)
            self.assertEqual("daily-ai", created["name"])
            self.assertEqual(1, len(json.loads(plugin.schedule_list())))

    def test_plugin_creates_analysis_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = SchedulerPlugin()
            plugin.setup(SimpleNamespace(workspace=Path(tmp)))

            created = json.loads(plugin.schedule_create_workflow(
                name="daily-analysis",
                hour=8,
                steps=[
                    {"type": "web_search", "query": "latest AI news"},
                    {"type": "llm_analyze", "prompt": "Analyze trends."},
                    {"type": "write_report"},
                ],
            ))

            self.assertEqual("llm_analyze", created["workflow"][1]["type"])

    def test_run_now_enqueues_telegram_report_file(self) -> None:
        class Reports:
            def __init__(self, workspace):
                self.workspace = workspace

            def run(self, schedule_id):
                report = self.workspace / "storage" / "reports" / "now.md"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("# Run now\n\nAI news.", encoding="utf-8")
                return {
                    "status": "success",
                    "schedule_id": schedule_id,
                    "run_id": 11,
                    "report_path": "storage/reports/now.md",
                }

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plugin = SchedulerPlugin()
            plugin.setup(SimpleNamespace(workspace=workspace))
            plugin.reports = Reports(workspace)
            schedule = plugin.store.create(
                name="daily-ai",
                query="latest AI news",
                hour=8,
            )

            with patch.dict(
                "os.environ",
                {"TELEGRAM_NOTIFY_CHAT_IDS": "123"},
                clear=True,
            ):
                result = json.loads(plugin.schedule_run_now(schedule_id=schedule["id"]))

            store = TelegramGatewayStore(workspace / ".gateway" / "telegram.db")
            try:
                pending = store.list_pending_messages()
            finally:
                store.close()

            self.assertEqual("success", result["status"])
            self.assertEqual(2, len(pending))
            self.assertEqual("text", pending[0]["message_type"])
            self.assertEqual("document", pending[1]["message_type"])
            self.assertEqual("storage/reports/now.md", pending[1]["document_path"])

    def test_plugin_creates_and_approves_agent_draft(self) -> None:
        class Planner:
            def create_draft(self, *, task_prompt, auditor):
                plan = ScheduledTaskPlan(
                    task_prompt=task_prompt,
                    summary="Daily AI report",
                    requested_tools=["web_search", "read_file"],
                    limits={"max_reasoning_steps": 12, "max_tool_calls": 8, "timeout_seconds": 300},
                )
                return ScheduledTaskDraft(
                    plan=plan,
                    audit=auditor.audit(plan.requested_tools),
                )

        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry()
            _register_tool(registry, "web_search", risk="low")
            _register_tool(registry, "read_file", risk="normal")
            plugin = SchedulerPlugin()
            plugin.setup(SimpleNamespace(
                workspace=Path(tmp),
                tool_registry=registry,
            ))
            plugin.planner = Planner()

            draft = json.loads(plugin.schedule_create_agent_draft(
                name="daily-agent",
                task_prompt="Generate a daily AI report.",
                hour=8,
            ))
            approved = json.loads(plugin.schedule_approve_agent(
                schedule_id=draft["schedule"]["id"],
                capabilities=[{
                    "tool": "read_file",
                    "scope": {"paths": ["docs"]},
                }],
            ))

            self.assertEqual("awaiting_approval", draft["audit"]["approval_status"])
            self.assertEqual("active", approved["approval_status"])
            self.assertEqual({
                "schedule_drafts": [],
                "runtime_requests": [],
            }, json.loads(plugin.schedule_pending_approvals()))

    def test_plugin_approves_runtime_request_for_future_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry()
            _register_tool(registry, "web_search", risk="low")
            _register_tool(registry, "bash", risk="high")
            plugin = SchedulerPlugin()
            plugin.setup(SimpleNamespace(
                workspace=Path(tmp),
                tool_registry=registry,
            ))
            schedule = plugin.store.create_agent_draft(
                name="daily-agent",
                task_prompt="Generate a daily AI report.",
                hour=8,
                plan={},
                approval_status="active",
                requested_tools=["web_search"],
                approved_capabilities=[{
                    "tool": "web_search",
                    "risk": "low",
                    "scope": {},
                }],
                limits={},
            )
            run_id = plugin.store.begin_run(schedule)
            plugin.store.complete_run(
                run_id=run_id,
                schedule_id=schedule["id"],
                status="awaiting_runtime_approval",
                approval_request={
                    "tool": "bash",
                    "arguments": {"command": "python scripts/report.py"},
                },
            )

            result = json.loads(plugin.schedule_approve_runtime(
                schedule_id=schedule["id"],
                run_id=run_id,
                capability={
                    "tool": "bash",
                    "scope": {"commands": ["python scripts/report.py"]},
                },
            ))

            self.assertTrue(result["rerun_required"])
            self.assertEqual(["bash", "web_search"], [
                item["tool"]
                for item in result["schedule"]["approved_capabilities"]
            ])


class SchedulerWorkerTests(unittest.TestCase):
    def test_reconcile_adds_and_removes_cron_jobs(self) -> None:
        class Store:
            schedules = [{
                "id": 1,
                "name": "daily-ai",
                "hour": 8,
                "minute": 30,
                "timezone": "Asia/Shanghai",
            }]

            def list_schedules(self, *, enabled_only=False):
                return list(self.schedules)

        class FakeScheduler:
            def __init__(self):
                self.jobs = {}

            def add_job(self, func, *, id, **kwargs):
                self.jobs[id] = {"func": func, **kwargs}

            def get_job(self, job_id):
                return self.jobs.get(job_id)

            def remove_job(self, job_id):
                self.jobs.pop(job_id)

        store = Store()
        scheduler = FakeScheduler()
        worker = SchedulerWorker(
            store=store,
            reports=SimpleNamespace(),
            scheduler=scheduler,
            cron_trigger=lambda **kwargs: kwargs,
        )

        worker.reconcile()
        self.assertEqual(
            {"hour": 8, "minute": 30, "timezone": "Asia/Shanghai"},
            scheduler.jobs["scheduled-search:1"]["trigger"],
        )

        store.schedules = []
        worker.reconcile()
        self.assertEqual({}, scheduler.jobs)

    def test_worker_routes_agent_schedule_and_skips_unapproved_jobs(self) -> None:
        class Store:
            schedules = [
                {
                    "id": 1,
                    "name": "workflow",
                    "hour": 8,
                    "minute": 0,
                    "timezone": "Asia/Shanghai",
                    "schedule_type": "workflow",
                    "approval_status": "active",
                },
                {
                    "id": 2,
                    "name": "approved-agent",
                    "hour": 9,
                    "minute": 0,
                    "timezone": "Asia/Shanghai",
                    "schedule_type": "agent",
                    "approval_status": "active",
                },
                {
                    "id": 3,
                    "name": "pending-agent",
                    "hour": 10,
                    "minute": 0,
                    "timezone": "Asia/Shanghai",
                    "schedule_type": "agent",
                    "approval_status": "awaiting_approval",
                },
            ]

            def list_schedules(self, *, enabled_only=False):
                return list(self.schedules)

            def get(self, schedule_id):
                return next(item for item in self.schedules if item["id"] == schedule_id)

        class FakeScheduler:
            def __init__(self):
                self.jobs = {}

            def add_job(self, func, *, id, **kwargs):
                self.jobs[id] = {"func": func, **kwargs}

            def get_job(self, job_id):
                return self.jobs.get(job_id)

            def remove_job(self, job_id):
                self.jobs.pop(job_id)

        class Runner:
            def __init__(self):
                self.calls = []

            def run(self, schedule_id):
                self.calls.append(schedule_id)
                return {"status": "success"}

        store = Store()
        scheduler = FakeScheduler()
        reports = Runner()
        agent_runner = Runner()
        worker = SchedulerWorker(
            store=store,
            reports=reports,
            agent_runner=agent_runner,
            scheduler=scheduler,
            cron_trigger=lambda **kwargs: kwargs,
        )

        worker.reconcile()
        worker.run_schedule(1)
        worker.run_schedule(2)

        self.assertEqual(
            {"scheduled-search:1", "scheduled-search:2"},
            set(scheduler.jobs),
        )
        self.assertEqual([1], reports.calls)
        self.assertEqual([2], agent_runner.calls)

    def test_telegram_notifier_enqueues_schedule_report_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "storage" / "reports" / "daily.md"
            report.parent.mkdir(parents=True)
            report.write_text("# Daily\n\nAI news.", encoding="utf-8")
            store = TelegramGatewayStore(root / ".gateway" / "telegram.db")
            notifier = TelegramScheduleNotifier(store=store, workspace=root)
            try:
                with patch.dict(
                    "os.environ",
                    {"TELEGRAM_NOTIFY_CHAT_IDS": "123"},
                    clear=True,
                ):
                    notifier.notify(
                        {"id": 1, "name": "daily-ai"},
                        {
                            "status": "success",
                            "run_id": 7,
                            "report_path": "storage/reports/daily.md",
                        },
                    )

                pending = store.list_pending_messages()
                self.assertEqual(2, len(pending))
                self.assertEqual("123", pending[0]["chat_id"])
                self.assertEqual("text", pending[0]["message_type"])
                self.assertIn("定时任务完成：daily-ai", pending[0]["text"])
                self.assertIn("AI news.", pending[0]["text"])
                self.assertEqual("123", pending[1]["chat_id"])
                self.assertEqual("document", pending[1]["message_type"])
                self.assertEqual(
                    "storage/reports/daily.md",
                    pending[1]["document_path"],
                )
                self.assertIn("daily-ai", pending[1]["caption"])
            finally:
                store.close()

    def test_telegram_notifier_is_noop_without_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notifier = TelegramScheduleNotifier(workspace=root)

            with patch.dict("os.environ", {}, clear=True):
                notifier.notify({"id": 1, "name": "daily-ai"}, {"status": "success"})

            self.assertFalse((root / ".gateway").exists())


class WebSearchVisibilityTests(unittest.TestCase):
    def test_web_search_is_visible_in_bot_mode_without_unlock(self) -> None:
        registry = ToolRegistry()
        plugin = WebSearchPlugin()
        for registration in plugin.tools():
            registry.register(
                registration.schema,
                registration.handler,
                risk=registration.risk,
                enabled_modes=registration.enabled_modes,
                always_on=registration.always_on,
                source=registration.source,
            )
        session = SimpleNamespace(metadata={})

        self.assertIn("web_search", registry.visible_names_for_turn(session, "bot"))


if __name__ == "__main__":
    unittest.main()
