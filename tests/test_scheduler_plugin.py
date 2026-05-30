import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from plugins.scheduler.plugin import SchedulerPlugin
from plugins.scheduler.reports import ScheduledReportService
from plugins.scheduler.store import ScheduleStore
from plugins.scheduler.workflow import WorkflowExecutor, validate_workflow
from plugins.web_search.client import TavilySearchClient
from scheduler_worker import SchedulerWorker


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


if __name__ == "__main__":
    unittest.main()
