import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugins.scheduler.store import ScheduleStore
from plugins.scheduler.workflow import WorkflowExecution, WorkflowExecutor
from plugins.web_search.client import TavilySearchClient


class ScheduledReportService:
    def __init__(
        self,
        *,
        store: ScheduleStore | None = None,
        search_client: TavilySearchClient | None = None,
        workflow_executor: WorkflowExecutor | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.store = store or ScheduleStore(self.workspace / ".scheduler" / "schedules.db")
        self.search_client = search_client or TavilySearchClient()
        self.workflow_executor = workflow_executor or WorkflowExecutor(
            search_client=self.search_client,
        )
        self.reports_dir = self.workspace / "storage" / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run(self, schedule_id: int) -> dict[str, Any]:
        schedule = self.store.get(schedule_id)
        run_id = self.store.begin_run(schedule)
        try:
            execution = self.workflow_executor.execute(schedule["workflow"])
            report_path = self._write_report(schedule, execution, run_id=run_id)
            portable_path = report_path.relative_to(self.workspace).as_posix()
            self.store.complete_run(
                run_id=run_id,
                schedule_id=schedule["id"],
                status=execution.status,
                report_path=portable_path,
                error=execution.analysis_error,
            )
            return {
                "status": execution.status,
                "schedule_id": schedule["id"],
                "name": schedule["name"],
                "result_count": len(execution.results),
                "report_path": portable_path,
                "analysis_error": execution.analysis_error,
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.store.complete_run(
                run_id=run_id,
                schedule_id=schedule["id"],
                status="error",
                error=error,
            )
            return {
                "status": "error",
                "schedule_id": schedule["id"],
                "name": schedule["name"],
                "error": error,
            }

    def recent_results(
        self,
        *,
        schedule_id: int | None = None,
        limit: int = 5,
        include_content: bool = False,
    ) -> list[dict[str, Any]]:
        runs = self.store.list_runs(schedule_id=schedule_id, limit=limit)
        if not include_content:
            return runs
        for run in runs:
            report_path = run.get("report_path")
            if not report_path:
                continue
            path = (self.workspace / report_path).resolve()
            if path.is_relative_to(self.reports_dir) and path.is_file():
                run["content"] = path.read_text(encoding="utf-8")[:50000]
        return runs

    def _write_report(
        self,
        schedule: dict[str, Any],
        execution: WorkflowExecution,
        *,
        run_id: int,
    ) -> Path:
        generated_at = datetime.now(timezone.utc)
        slug = _slug(schedule["name"])
        filename = (
            f"{generated_at.strftime('%Y%m%d-%H%M%S')}"
            f"-schedule-{schedule['id']}-run-{run_id}-{slug}.md"
        )
        path = self.reports_dir / filename
        sections = [
            f"# {execution.report_title or f'Scheduled Search Report: {schedule['name']}'}",
            "",
            f"- generated_at: `{generated_at.isoformat()}`",
            f"- schedule_id: `{schedule['id']}`",
            f"- run_id: `{run_id}`",
            f"- query: {schedule['query']}",
            f"- topic: `{schedule['topic']}`",
            f"- time_range: `{schedule['time_range'] or 'none'}`",
            "",
            "## Workflow",
            "",
            "```json",
            json.dumps(execution.workflow, ensure_ascii=False, indent=2),
            "```",
        ]
        if execution.analysis or execution.analysis_error:
            sections.extend(["", "## AI Analysis", ""])
            if execution.analysis:
                sections.append(execution.analysis)
            if execution.analysis_error:
                sections.append(f"> Analysis failed: `{execution.analysis_error}`")
        sections.extend(["", f"## Sources ({len(execution.results)})"])
        for index, item in enumerate(execution.results, start=1):
            sections.extend(
                [
                    "",
                    f"### {index}. {item.get('title') or '(untitled)'}",
                    "",
                    f"- url: {item.get('url', '')}",
                    f"- score: `{item.get('score', '')}`",
                    "",
                    str(item.get("snippet", "")).strip(),
                ]
            )
        path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
        return path


def _slug(text: str) -> str:
    cleaned = []
    for char in text.lower():
        cleaned.append(char if char.isalnum() else "-")
    return "-".join(filter(None, "".join(cleaned).split("-")))[:48] or "report"
