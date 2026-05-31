import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.context import ContextBuilder
from core.pipeline import DEFAULT_MAX_REASONING_STEPS, Pipeline, get_last_assistant_text
from memory.store import MemoryStore
from modes.automation import AUTOMATION_PROFILE
from plugins.scheduler.policy import ToolApprovalPolicyHook
from plugins.scheduler.store import ScheduleStore
from tasksessions.artifacts import TaskArtifactWriter
from tasksessions.conclusions import ConclusionExtraction, TaskConclusionExtractor
from tasksessions.memory_lifecycle import TaskMemoryLifecycle
from tasksessions.promotion import PromotionResult, TaskMemoryPromoter
from tasksessions.session import TaskSessionFactory
from tools.executor import ToolExecutor


class ScheduledAgentRunner:
    def __init__(
        self,
        *,
        store: ScheduleStore,
        sessions,
        base_pipeline: Pipeline,
        global_memory: MemoryStore,
        workspace: str | Path | None = None,
        conclusion_extractor=None,
        promoter=None,
        artifact_writer=None,
    ) -> None:
        self.store = store
        self.sessions = sessions
        self.base_pipeline = base_pipeline
        self.global_memory = global_memory
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.factory = TaskSessionFactory(
            sessions,
            root=self.workspace / ".task_sessions",
        )
        self.conclusion_extractor = conclusion_extractor or TaskConclusionExtractor(
            provider=base_pipeline.provider,
            model=base_pipeline.model,
        )
        self.promoter = promoter or TaskMemoryPromoter(global_memory)
        self.artifact_writer = artifact_writer or TaskArtifactWriter()
        self.reports_dir = self.workspace / "storage" / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run(self, schedule_id: int) -> dict[str, Any]:
        schedule = self.store.get(schedule_id)
        if schedule["schedule_type"] != "agent":
            raise ValueError("ScheduledAgentRunner only executes agent schedules.")
        if schedule["approval_status"] != "active":
            return {
                "status": "not_active",
                "schedule_id": schedule["id"],
                "approval_status": schedule["approval_status"],
            }

        record = self.factory.create(
            parent_session_id=f"scheduler:{schedule['id']}",
            task_type="scheduled_agent",
            user_request=schedule["task_prompt"],
        )
        trace_path = record.memory_root.parent / "TOOL_TRACE.json"
        run_id = self.store.begin_run(
            schedule,
            task_session_id=record.session.id,
            trace_path=_portable_path(trace_path, self.workspace),
        )
        self._prepare_session(record, schedule, run_id)
        task_memory = self._prepare_memory(record, schedule, run_id)

        status = "success"
        error = ""
        reply = ""
        approval_request = None
        try:
            pipeline = self._build_pipeline(task_memory)
            pipeline.run(record.session, AUTOMATION_PROFILE)
            approval_request = record.session.metadata.get("runtime_approval_request")
            if approval_request:
                status = "awaiting_runtime_approval"
            reply = get_last_assistant_text(record.session.messages).strip()
            if not reply and approval_request:
                reply = "Scheduled agent paused while waiting for runtime approval."
            record.session.metadata["status"] = status
            record.session.metadata["task_reply"] = reply
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            reply = f"Scheduled agent failed: {error}"
            record.session.metadata["status"] = status
            record.session.metadata["task_reply"] = reply
            record.session.metadata["error"] = error

        self.sessions.save(record.session)
        portable_trace = ""
        try:
            trace_path.write_text(
                json.dumps(
                    _tool_trace(record.session.messages),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ) + "\n",
                encoding="utf-8",
            )
            portable_trace = _portable_path(trace_path, self.workspace)
        except Exception as exc:
            status, error = _partial_failure(status, error, "trace", exc)

        artifacts = None
        try:
            extraction, promotion = self._extract_and_promote(
                record=record,
                schedule=schedule,
                reply=reply,
                enabled=status == "success",
            )
            artifacts = self.artifact_writer.write(
                record=record,
                user_request=schedule["task_prompt"],
                task_reply=reply,
                extraction=extraction,
                promotion=promotion,
            )
        except Exception as exc:
            status, error = _partial_failure(status, error, "artifact", exc)

        portable_report = ""
        try:
            report_path = self._write_report(
                schedule=schedule,
                run_id=run_id,
                status=status,
                reply=reply,
                trace_path=trace_path,
                approval_request=approval_request,
                error=error,
            )
            portable_report = _portable_path(report_path, self.workspace)
        except Exception as exc:
            status, error = _partial_failure(status, error, "report", exc)

        self.store.complete_run(
            run_id=run_id,
            schedule_id=schedule["id"],
            status=status,
            report_path=portable_report,
            error=error,
            task_session_id=record.session.id,
            trace_path=portable_trace,
            approval_request=approval_request,
        )
        if artifacts is not None:
            record.session.metadata["task_log_path"] = _portable_path(
                artifacts.task_log_path,
                self.workspace,
            )
            record.session.metadata["conclusions_path"] = _portable_path(
                artifacts.conclusions_path,
                self.workspace,
            )
        record.session.metadata["report_path"] = portable_report
        record.session.metadata["status"] = status
        if error:
            record.session.metadata["error"] = error
        self.sessions.save(record.session)
        return {
            "status": status,
            "schedule_id": schedule["id"],
            "run_id": run_id,
            "name": schedule["name"],
            "task_session_id": record.session.id,
            "report_path": portable_report,
            "trace_path": portable_trace,
            "approval_request": approval_request,
            "error": error or None,
        }

    def _prepare_session(self, record, schedule: dict[str, Any], run_id: int) -> None:
        record.session.metadata.update({
            "kind": "scheduled_agent",
            "schedule_id": schedule["id"],
            "schedule_run_id": run_id,
            "approved_capabilities": schedule["approved_capabilities"],
            "automation_limits": schedule["limits"],
            "automation_started_monotonic": time.monotonic(),
            "automation_tool_calls_used": 0,
        })
        record.session.add_message(
            "user",
            (
                f"<scheduled-task schedule_id=\"{schedule['id']}\" run_id=\"{run_id}\">\n"
                "This is an internal unattended task, not a live user message. "
                "Complete the task with only the approved tools exposed to this session. "
                "Return a report-ready final response.\n"
                "</scheduled-task>\n\n"
                f"Task:\n{schedule['task_prompt']}"
            ),
        )

    def _prepare_memory(self, record, schedule: dict[str, Any], run_id: int) -> MemoryStore:
        memory = MemoryStore(record.memory_root)
        memory.append("now", f"Scheduled task: {schedule['task_prompt']}")
        memory.append("now", f"Schedule ID: {schedule['id']}; run ID: {run_id}")
        memory.write_recent_context(
            f"- schedule_id: `{schedule['id']}`\n"
            f"- run_id: `{run_id}`\n"
            f"- task_prompt: {schedule['task_prompt']}"
        )
        return memory

    def _build_pipeline(self, task_memory: MemoryStore) -> Pipeline:
        return Pipeline(
            tools=self.base_pipeline.tools,
            provider=self.base_pipeline.provider,
            model=self.base_pipeline.model,
            tool_executor=ToolExecutor([
                ToolApprovalPolicyHook(),
                *self.base_pipeline.tool_executor.hooks,
            ]),
            context_builder=ContextBuilder(memory_store=task_memory),
            memory_lifecycle=TaskMemoryLifecycle(task_memory),
            max_tokens=self.base_pipeline.max_tokens,
            max_reasoning_steps=getattr(
                self.base_pipeline,
                "max_reasoning_steps",
                DEFAULT_MAX_REASONING_STEPS,
            ),
        )

    def _extract_and_promote(
        self,
        *,
        record,
        schedule: dict[str, Any],
        reply: str,
        enabled: bool,
    ) -> tuple[ConclusionExtraction, PromotionResult]:
        if not enabled:
            return ConclusionExtraction(
                summary="Scheduled agent did not complete successfully.",
            ), PromotionResult()
        extraction = self.conclusion_extractor.extract(
            user_request=schedule["task_prompt"],
            task_summary=reply,
            messages=record.session.messages,
        )
        promotion = self.promoter.promote(
            task_id=record.task_id,
            task_memory=MemoryStore(record.memory_root),
            extracted_conclusions=extraction.candidates,
        )
        return extraction, promotion

    def _write_report(
        self,
        *,
        schedule: dict[str, Any],
        run_id: int,
        status: str,
        reply: str,
        trace_path: Path,
        approval_request: dict[str, Any] | None,
        error: str,
    ) -> Path:
        generated_at = datetime.now(timezone.utc)
        filename = (
            f"{generated_at.strftime('%Y%m%d-%H%M%S')}"
            f"-schedule-{schedule['id']}-run-{run_id}-{_slug(schedule['name'])}.md"
        )
        path = self.reports_dir / filename
        sections = [
            f"# Scheduled Agent Report: {schedule['name']}",
            "",
            f"- generated_at: `{generated_at.isoformat()}`",
            f"- schedule_id: `{schedule['id']}`",
            f"- run_id: `{run_id}`",
            f"- status: `{status}`",
            f"- trace_path: `{_portable_path(trace_path, self.workspace)}`",
            "",
            "## Task",
            "",
            schedule["task_prompt"],
            "",
            "## Approved Capabilities",
            "",
            "```json",
            json.dumps(schedule["approved_capabilities"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Final Reply",
            "",
            reply or "(empty)",
        ]
        if approval_request:
            sections.extend([
                "",
                "## Runtime Approval Request",
                "",
                "```json",
                json.dumps(approval_request, ensure_ascii=False, indent=2),
                "```",
            ])
        if error:
            sections.extend(["", "## Error", "", error])
        path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
        return path


def _tool_trace(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "status": message.get("status", "unknown"),
            "tool_call_id": message.get("tool_call_id", ""),
            "arguments": message.get("final_arguments", {}),
            "output": str(message.get("content", ""))[:5000],
            "pre_hook_trace": message.get("pre_hook_trace", []),
            "post_hook_trace": message.get("post_hook_trace", []),
        }
        for message in messages
        if message.get("role") == "tool"
    ]


def _portable_path(path: Path, workspace: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return str(resolved)


def _slug(text: str) -> str:
    cleaned = [char if char.isalnum() else "-" for char in text.lower()]
    return "-".join(filter(None, "".join(cleaned).split("-")))[:48] or "agent-report"


def _partial_failure(
    status: str,
    current_error: str,
    stage: str,
    exc: Exception,
) -> tuple[str, str]:
    error = f"{stage} {type(exc).__name__}: {exc}"
    combined = f"{current_error}; {error}" if current_error else error
    if status == "success":
        return "partial_success", combined
    return status, combined
