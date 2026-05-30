import json
import os
from dataclasses import dataclass, field
from typing import Any

from plugins.web_search.client import TavilySearchClient


SUPPORTED_STEP_TYPES = {
    "web_search",
    "llm_analyze",
    "write_report",
}


@dataclass
class WorkflowExecution:
    workflow: list[dict[str, Any]]
    results: list[dict[str, Any]] = field(default_factory=list)
    analysis: str = ""
    analysis_error: str = ""
    report_title: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial_success" if self.analysis_error else "success"


class LLMAnalysisClient:
    def __init__(self, *, provider=None, model: str | None = None) -> None:
        self.provider = provider
        self.model = model

    def analyze(
        self,
        *,
        prompt: str,
        results: list[dict[str, Any]],
    ) -> str:
        provider, model = self._provider_and_model()
        source_text = json.dumps(results, ensure_ascii=False, indent=2)[:30000]
        response = provider.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write concise scheduled research digests. "
                        "Use only the supplied web search results. Preserve source URLs. "
                        "Clearly distinguish facts from your analysis. Return Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Analysis instructions:\n{prompt}\n\n"
                        f"Web search results:\n{source_text}"
                    ),
                },
            ],
            tools=[],
            tool_choice="none",
            max_tokens=max(400, int(os.environ.get("SCHEDULER_ANALYSIS_MAX_TOKENS", "1800"))),
        )
        text = (response.content or "").strip()
        if not text:
            raise RuntimeError("LLM returned an empty analysis.")
        return text

    def _provider_and_model(self):
        if self.provider is not None and self.model:
            return self.provider, self.model
        from config import MODEL, client
        from core.provider import OpenAICompatibleProvider

        return (
            self.provider or OpenAICompatibleProvider(client),
            self.model or os.environ.get("SCHEDULER_ANALYSIS_MODEL") or MODEL,
        )


class WorkflowExecutor:
    def __init__(
        self,
        *,
        search_client: TavilySearchClient | None = None,
        analysis_client: LLMAnalysisClient | None = None,
    ) -> None:
        self.search_client = search_client or TavilySearchClient()
        self.analysis_client = analysis_client or LLMAnalysisClient()

    def execute(self, workflow: list[dict[str, Any]]) -> WorkflowExecution:
        normalized = validate_workflow(workflow)
        execution = WorkflowExecution(workflow=normalized)

        for step in normalized:
            step_type = step["type"]
            if step_type == "web_search":
                results = self.search_client.search(
                    query=step["query"],
                    topic=step["topic"],
                    max_results=step["max_results"],
                    time_range=step.get("time_range"),
                )
                execution.results.extend(results)
                execution.trace.append({
                    "type": step_type,
                    "status": "success",
                    "result_count": len(results),
                })
                continue

            if step_type == "llm_analyze":
                try:
                    execution.analysis = self.analysis_client.analyze(
                        prompt=step["prompt"],
                        results=execution.results,
                    )
                    execution.trace.append({
                        "type": step_type,
                        "status": "success",
                    })
                except Exception as exc:
                    execution.analysis_error = f"{type(exc).__name__}: {exc}"
                    execution.trace.append({
                        "type": step_type,
                        "status": "error",
                        "error": execution.analysis_error,
                    })
                continue

            if step_type == "write_report":
                execution.report_title = step.get("title", "")
                execution.trace.append({
                    "type": step_type,
                    "status": "success",
                })

        return execution


def build_search_workflow(
    *,
    query: str,
    topic: str = "news",
    max_results: int = 5,
    time_range: str | None = "day",
) -> list[dict[str, Any]]:
    return validate_workflow([
        {
            "type": "web_search",
            "query": query,
            "topic": topic,
            "max_results": max_results,
            "time_range": time_range,
        },
        {"type": "write_report"},
    ])


def validate_workflow(workflow: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(workflow, list):
        raise ValueError("workflow must be a list of steps.")
    if not 2 <= len(workflow) <= 6:
        raise ValueError("workflow must contain between 2 and 6 steps.")

    normalized = []
    search_seen = False
    analysis_seen = False
    report_seen = False

    for index, raw_step in enumerate(workflow):
        if not isinstance(raw_step, dict):
            raise ValueError(f"workflow step {index + 1} must be an object.")
        step_type = str(raw_step.get("type", "")).strip()
        if step_type not in SUPPORTED_STEP_TYPES:
            raise ValueError(f"Unsupported workflow step: {step_type or '(empty)'}")
        if report_seen:
            raise ValueError("write_report must be the final workflow step.")

        if step_type == "web_search":
            if analysis_seen:
                raise ValueError("web_search steps must run before llm_analyze.")
            search_seen = True
            normalized.append(_normalize_search_step(raw_step))
            continue

        if step_type == "llm_analyze":
            if not search_seen:
                raise ValueError("llm_analyze requires an earlier web_search step.")
            if analysis_seen:
                raise ValueError("workflow may contain at most one llm_analyze step.")
            analysis_seen = True
            normalized.append(_normalize_analysis_step(raw_step))
            continue

        if not search_seen:
            raise ValueError("write_report requires an earlier web_search step.")
        report_seen = True
        normalized.append(_normalize_report_step(raw_step))

    if not report_seen:
        raise ValueError("workflow must end with write_report.")
    return normalized


def primary_search_step(workflow: list[dict[str, Any]]) -> dict[str, Any]:
    for step in validate_workflow(workflow):
        if step["type"] == "web_search":
            return step
    raise ValueError("workflow requires a web_search step.")


def _normalize_search_step(step: dict[str, Any]) -> dict[str, Any]:
    query = str(step.get("query", "")).strip()
    if not query:
        raise ValueError("web_search query is required.")
    if len(query) > 600:
        raise ValueError("web_search query is too long.")
    topic = str(step.get("topic", "news")).strip() or "news"
    if topic not in {"general", "news", "finance"}:
        raise ValueError("web_search topic must be general, news, or finance.")
    time_range = step.get("time_range", "day")
    if time_range not in {None, "", "day", "week", "month", "year"}:
        raise ValueError("web_search time_range must be day, week, month, or year.")
    return {
        "type": "web_search",
        "query": query,
        "topic": topic,
        "max_results": max(1, min(int(step.get("max_results", 5)), 8)),
        "time_range": time_range or None,
    }


def _normalize_analysis_step(step: dict[str, Any]) -> dict[str, Any]:
    prompt = str(step.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("llm_analyze prompt is required.")
    if len(prompt) > 4000:
        raise ValueError("llm_analyze prompt is too long.")
    return {
        "type": "llm_analyze",
        "prompt": prompt,
    }


def _normalize_report_step(step: dict[str, Any]) -> dict[str, Any]:
    title = str(step.get("title", "")).strip()
    if len(title) > 200:
        raise ValueError("write_report title is too long.")
    return {
        "type": "write_report",
        "title": title,
    }
