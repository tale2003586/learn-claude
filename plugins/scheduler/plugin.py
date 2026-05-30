import json

from plugins.base import Plugin, ToolRegistration
from plugins.scheduler.planning import (
    ScheduledTaskPlanner,
    ToolCapabilityAuditor,
    merge_approved_capabilities,
)
from plugins.scheduler.reports import ScheduledReportService
from plugins.scheduler.store import ScheduleStore
from tools.schema import function_tool


class SchedulerPlugin(Plugin):
    name = "scheduler"

    def setup(self, context) -> None:
        super().setup(context)
        self.store = ScheduleStore(context.workspace / ".scheduler" / "schedules.db")
        self.reports = ScheduledReportService(
            store=self.store,
            workspace=context.workspace,
        )
        self.planner = ScheduledTaskPlanner()
        self.auditor = (
            ToolCapabilityAuditor(context.tool_registry)
            if getattr(context, "tool_registry", None) is not None
            else None
        )
        self.agent_runner = None

    def bind_agent_runner(self, agent_runner) -> None:
        self.agent_runner = agent_runner

    def tools(self) -> list[ToolRegistration]:
        return [
            self._tool(
                "schedule_create",
                "Create a daily web search schedule. The worker writes Markdown reports to storage/reports.",
                {
                    "name": {"type": "string", "description": "Unique schedule name."},
                    "query": {"type": "string", "description": "Web search query."},
                    "hour": {
                        "type": "integer",
                        "description": "Daily execution hour, from 0 to 23.",
                    },
                    "minute": {
                        "type": "integer",
                        "description": "Execution minute, from 0 to 59. Defaults to 0.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone. Defaults to Asia/Shanghai.",
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news", "finance"],
                        "description": "Search category. Defaults to news.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Result count. Defaults to 5, maximum 8.",
                    },
                    "time_range": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description": "Recency filter. Defaults to day.",
                    },
                },
                ["name", "query", "hour"],
                self.schedule_create,
            ),
            self._tool(
                "schedule_create_workflow",
                (
                    "Create a daily controlled research workflow. Use this when the user "
                    "requests analysis or a multi-step digest. Allowed step types are "
                    "web_search, llm_analyze, and write_report. The final step must be write_report."
                ),
                {
                    "name": {"type": "string", "description": "Unique schedule name."},
                    "hour": {
                        "type": "integer",
                        "description": "Daily execution hour, from 0 to 23.",
                    },
                    "minute": {
                        "type": "integer",
                        "description": "Execution minute, from 0 to 59. Defaults to 0.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone. Defaults to Asia/Shanghai.",
                    },
                    "steps": {
                        "type": "array",
                        "description": (
                            "Ordered workflow steps. Search first, optional LLM analysis, "
                            "then write_report as the final step."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["web_search", "llm_analyze", "write_report"],
                                },
                                "query": {
                                    "type": "string",
                                    "description": "Required for web_search.",
                                },
                                "topic": {
                                    "type": "string",
                                    "enum": ["general", "news", "finance"],
                                },
                                "max_results": {"type": "integer"},
                                "time_range": {
                                    "type": "string",
                                    "enum": ["day", "week", "month", "year"],
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Required for llm_analyze.",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "Optional report title for write_report.",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                },
                ["name", "hour", "steps"],
                self.schedule_create_workflow,
            ),
            self._tool(
                "schedule_list",
                "List configured workflow and approved-agent schedules with their last execution status.",
                {},
                [],
                self.schedule_list,
            ),
            self._tool(
                "schedule_create_agent_draft",
                (
                    "Create a daily autonomous agent schedule draft. The planner proposes "
                    "the minimum tools, then a deterministic auditor auto-approves low-risk "
                    "tools and returns any capabilities that need explicit user approval."
                ),
                {
                    "name": {"type": "string", "description": "Unique schedule name."},
                    "task_prompt": {
                        "type": "string",
                        "description": "The autonomous task to complete at the scheduled time.",
                    },
                    "hour": {
                        "type": "integer",
                        "description": "Daily execution hour, from 0 to 23.",
                    },
                    "minute": {
                        "type": "integer",
                        "description": "Execution minute, from 0 to 59. Defaults to 0.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone. Defaults to Asia/Shanghai.",
                    },
                },
                ["name", "task_prompt", "hour"],
                self.schedule_create_agent_draft,
            ),
            self._tool(
                "schedule_approve_agent",
                (
                    "Approve requested capabilities for an autonomous agent schedule. "
                    "High-risk shell commands require exact commands; file tools require "
                    "workspace-relative path prefixes."
                ),
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Agent schedule ID.",
                    },
                    "capabilities": {
                        "type": "array",
                        "description": "Capabilities explicitly approved by the user.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string"},
                                "scope": {
                                    "type": "object",
                                    "description": (
                                        "Scope object. Use commands for bash/background_run "
                                        "or paths for read_file/write_file/edit_file."
                                    ),
                                },
                            },
                            "required": ["tool", "scope"],
                        },
                    },
                },
                ["schedule_id", "capabilities"],
                self.schedule_approve_agent,
            ),
            self._tool(
                "schedule_reject_agent",
                "Reject and disable an autonomous agent schedule draft.",
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Agent schedule ID.",
                    },
                },
                ["schedule_id"],
                self.schedule_reject_agent,
            ),
            self._tool(
                "schedule_approve_runtime",
                (
                    "Approve a capability requested by a paused autonomous-agent run. "
                    "The approval applies to future runs; rerun the schedule explicitly "
                    "if it should execute immediately."
                ),
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Agent schedule ID.",
                    },
                    "run_id": {
                        "type": "integer",
                        "description": "Paused schedule run ID.",
                    },
                    "capability": {
                        "type": "object",
                        "description": "Approved tool and its explicit scope.",
                        "properties": {
                            "tool": {"type": "string"},
                            "scope": {"type": "object"},
                        },
                        "required": ["tool", "scope"],
                    },
                },
                ["schedule_id", "run_id"],
                self.schedule_approve_runtime,
            ),
            self._tool(
                "schedule_pending_approvals",
                "List autonomous agent schedule drafts that are waiting for approval or blocked.",
                {},
                [],
                self.schedule_pending_approvals,
            ),
            self._tool(
                "schedule_delete",
                "Delete a daily web search schedule by ID.",
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Schedule ID.",
                    },
                },
                ["schedule_id"],
                self.schedule_delete,
            ),
            self._tool(
                "schedule_run_now",
                "Run a configured web search schedule immediately and save its Markdown report.",
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Schedule ID.",
                    },
                },
                ["schedule_id"],
                self.schedule_run_now,
            ),
            self._tool(
                "schedule_results",
                "Read recent scheduled search report metadata, optionally including Markdown content.",
                {
                    "schedule_id": {
                        "type": "integer",
                        "description": "Optional schedule ID filter.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of runs. Defaults to 5.",
                    },
                    "include_content": {
                        "type": "boolean",
                        "description": "Include saved Markdown report content. Defaults to false.",
                    },
                },
                [],
                self.schedule_results,
            ),
        ]

    def schedule_create(
        self,
        *,
        name: str,
        query: str,
        hour: int,
        minute: int = 0,
        timezone: str = "Asia/Shanghai",
        topic: str = "news",
        max_results: int = 5,
        time_range: str | None = "day",
    ) -> str:
        schedule = self.store.create(
            name=name,
            query=query,
            hour=hour,
            minute=minute,
            timezone_name=timezone,
            topic=topic,
            max_results=max_results,
            time_range=time_range,
        )
        return json.dumps(schedule, ensure_ascii=False, indent=2)

    def schedule_list(self) -> str:
        return json.dumps(self.store.list_schedules(), ensure_ascii=False, indent=2)

    def schedule_create_workflow(
        self,
        *,
        name: str,
        hour: int,
        steps: list[dict],
        minute: int = 0,
        timezone: str = "Asia/Shanghai",
    ) -> str:
        schedule = self.store.create_workflow(
            name=name,
            workflow=steps,
            hour=hour,
            minute=minute,
            timezone_name=timezone,
        )
        return json.dumps(schedule, ensure_ascii=False, indent=2)

    def schedule_delete(self, *, schedule_id: int) -> str:
        deleted = self.store.delete(schedule_id)
        return json.dumps(
            {"schedule_id": int(schedule_id), "deleted": deleted},
            ensure_ascii=False,
            indent=2,
        )

    def schedule_create_agent_draft(
        self,
        *,
        name: str,
        task_prompt: str,
        hour: int,
        minute: int = 0,
        timezone: str = "Asia/Shanghai",
    ) -> str:
        draft = self.planner.create_draft(
            task_prompt=task_prompt,
            auditor=self._require_auditor(),
        )
        schedule = self.store.create_agent_draft(
            name=name,
            task_prompt=draft.plan.task_prompt,
            hour=hour,
            minute=minute,
            timezone_name=timezone,
            plan=draft.plan.to_dict(),
            approval_status=draft.audit.approval_status,
            requested_tools=draft.plan.requested_tools,
            approved_capabilities=draft.audit.approved_capabilities,
            limits=draft.plan.limits,
        )
        return json.dumps(
            {
                "schedule": schedule,
                "audit": draft.audit.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def schedule_approve_agent(
        self,
        *,
        schedule_id: int,
        capabilities: list[dict],
    ) -> str:
        schedule = self.store.get(schedule_id)
        audit = self._require_auditor().audit(schedule["requested_tools"])
        approved, approval_status = merge_approved_capabilities(audit, capabilities)
        updated = self.store.update_agent_approval(
            schedule_id,
            approved_capabilities=approved,
            approval_status=approval_status,
        )
        return json.dumps(updated, ensure_ascii=False, indent=2)

    def schedule_reject_agent(self, *, schedule_id: int) -> str:
        return json.dumps(
            self.store.reject_agent(schedule_id),
            ensure_ascii=False,
            indent=2,
        )

    def schedule_pending_approvals(self) -> str:
        return json.dumps(
            {
                "schedule_drafts": self.store.list_pending_agents(),
                "runtime_requests": self.store.list_runtime_approval_runs(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def schedule_approve_runtime(
        self,
        *,
        schedule_id: int,
        run_id: int,
        capability: dict | None = None,
    ) -> str:
        schedule = self.store.get(schedule_id)
        run = self.store.get_run(run_id)
        request = run.get("approval_request")
        if run["schedule_id"] != int(schedule_id):
            raise ValueError("Run does not belong to the requested schedule.")
        if not request:
            raise ValueError("Run does not contain a runtime approval request.")
        tool = str(request.get("tool", "")).strip()
        audit = self._require_auditor().audit([tool])
        submitted = []
        if audit.requires_approval:
            if capability is None:
                raise ValueError(f"Runtime approval requires an explicit capability: {tool}")
            submitted = [capability]
        newly_approved, approval_status = merge_approved_capabilities(audit, submitted)
        if approval_status != "active":
            raise ValueError("Runtime capability approval is incomplete.")
        merged = {
            item["tool"]: item
            for item in schedule["approved_capabilities"]
            if isinstance(item, dict) and item.get("tool")
        }
        merged.update({item["tool"]: item for item in newly_approved})
        requested_tools = list(schedule["requested_tools"])
        if tool not in requested_tools:
            requested_tools.append(tool)
        updated = self.store.update_agent_approval(
            schedule_id,
            approved_capabilities=[merged[name] for name in sorted(merged)],
            approval_status="active",
            requested_tools=requested_tools,
        )
        return json.dumps(
            {
                "schedule": updated,
                "approved_runtime_request": request,
                "rerun_required": True,
            },
            ensure_ascii=False,
            indent=2,
        )

    def schedule_run_now(self, *, schedule_id: int) -> str:
        schedule = self.store.get(schedule_id)
        if schedule["schedule_type"] == "agent":
            if self.agent_runner is None:
                return json.dumps(
                    {
                        "status": "error",
                        "error": "Scheduled agent runner is not bound.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            return json.dumps(
                self.agent_runner.run(schedule_id),
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps(
            self.reports.run(schedule_id),
            ensure_ascii=False,
            indent=2,
        )

    def schedule_results(
        self,
        *,
        schedule_id: int | None = None,
        limit: int = 5,
        include_content: bool = False,
    ) -> str:
        return json.dumps(
            self.reports.recent_results(
                schedule_id=schedule_id,
                limit=limit,
                include_content=include_content,
            ),
            ensure_ascii=False,
            indent=2,
        )

    def _tool(
        self,
        name: str,
        description: str,
        properties: dict,
        required: list[str],
        handler,
    ) -> ToolRegistration:
        return ToolRegistration(
            schema=function_tool(name, description, properties, required),
            handler=handler,
            risk="low",
            enabled_modes={"bot", "coding"},
            always_on=True,
            source="plugin:scheduler",
        )

    def _require_auditor(self) -> ToolCapabilityAuditor:
        if self.auditor is None:
            raise RuntimeError("Scheduler tool auditor is not available.")
        return self.auditor
