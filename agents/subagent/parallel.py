from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
import time
from typing import Any


MAX_PARALLEL_SUBTASKS = 8


def run_parallel_tasks(
    *,
    runner,
    tasks: list[dict[str, Any]],
    parent_session=None,
    max_workers: int | None = None,
    timeout_seconds: float = 300,
) -> list[dict[str, Any]]:
    bounded_tasks = list(tasks or [])[:MAX_PARALLEL_SUBTASKS]
    if not bounded_tasks:
        return []

    workers = max(1, min(max_workers or len(bounded_tasks), MAX_PARALLEL_SUBTASKS))
    results: list[dict[str, Any] | None] = [None] * len(bounded_tasks)
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {}
    try:
        for index, task in enumerate(bounded_tasks):
            futures[executor.submit(
                runner.run,
                prompt=str(task.get("prompt") or ""),
                agent_type=str(task.get("agent_type") or "explore"),
                description=str(task.get("description") or ""),
                parent_session=parent_session,
            )] = index

        started = time.monotonic()
        try:
            for future in as_completed(futures, timeout=max(0.001, timeout_seconds)):
                index = futures[future]
                results[index] = _result_from_future(future, bounded_tasks[index])
        except TimeoutError:
            pass

        timed_out = time.monotonic() - started >= timeout_seconds
        for future, index in futures.items():
            if results[index] is not None:
                continue
            if not future.done():
                future.cancel()
                results[index] = _error_result(
                    bounded_tasks[index],
                    f"TimeoutError: subagent task exceeded {timeout_seconds:g}s",
                )
                continue
            results[index] = _result_from_future(future, bounded_tasks[index])
        if timed_out:
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True, cancel_futures=True)
    except Exception:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    return [item for item in results if item is not None]


def _result_from_future(future, task: dict[str, Any]) -> dict[str, Any]:
    try:
        result = future.result()
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return _error_result(task, f"TypeError: unexpected result {type(result).__name__}")
    except Exception as exc:
        return _error_result(task, f"{type(exc).__name__}: {exc}")


def _error_result(task: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "agent_type": str(task.get("agent_type") or "explore"),
        "success": False,
        "summary": "",
        "files_touched": [],
        "tool_count": 0,
        "error": error,
    }
