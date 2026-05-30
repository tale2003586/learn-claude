import json

from plugins.base import Plugin, ToolRegistration
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
                "List configured daily web search schedules and their last execution status.",
                {},
                [],
                self.schedule_list,
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

    def schedule_run_now(self, *, schedule_id: int) -> str:
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
