# context.py

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from runtime.context_budget import BudgetedText, ContextBudgeter
from runtime.context_sections import ContextBuildReport, ContextSection, message_chars


DEFAULT_INSTRUCTION_LIMIT = 12000


@dataclass
class ContextBundle:
    messages: list[dict]
    report: ContextBuildReport | None = None


class ContextBuilder:

    def __init__(
        self,
        memory_store=None,
        *,
        instruction_root: str | Path | None = None,
        instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
        budgeter: ContextBudgeter | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.instruction_root = Path(instruction_root or Path.cwd()).resolve()
        self.instruction_limit = max(1000, int(instruction_limit))
        self.budgeter = budgeter or ContextBudgeter.from_env()

    def build(
        self,
        *,
        session,
        profile,
        inbox: list | None = None,
        background_results: list | None = None,

    ) -> ContextBundle:
        profile_prompt = str(getattr(profile, "system_prompt", "") or "")
        instruction_block, instruction_sections, instruction_reductions = (
            self._build_instruction_block(profile)
        )
        runtime_guidance = self._runtime_guidance()
        system_prompt = self._build_system_prompt(
            profile_prompt=profile_prompt,
            instruction_block=instruction_block,
            runtime_guidance=runtime_guidance,
        )
        raw_memory_block = self._build_memory_block(session)
        budgeted_memory = self.budgeter.apply("memory", raw_memory_block)
        raw_task_runtime_events = self._build_task_runtime_events_block(
            inbox=inbox or [],
            background_results=background_results or [],
        )
        budgeted_task_runtime_events = self.budgeter.apply(
            "task_runtime_events",
            raw_task_runtime_events,
        )

        context_frame = self._build_context_frame(
            memory_block=budgeted_memory.rendered_text,
            task_runtime_events_block=budgeted_task_runtime_events.rendered_text,
        )

        session_messages = list(session.messages)
        history_messages, current_request = self._split_current_request(session_messages)
        messages = [
            {"role": "system", "content": system_prompt},
            *session_messages,
        ]

        if context_frame:
            messages.append({
                "role": "user",
                "content": context_frame,
            })

        report = self._build_report(
            messages=messages,
            profile_prompt=profile_prompt,
            instruction_sections=instruction_sections,
            runtime_guidance=runtime_guidance,
            system_prompt=system_prompt,
            session_messages=session_messages,
            history_messages=history_messages,
            current_request=current_request,
            memory_block=budgeted_memory.rendered_text,
            raw_memory_block=raw_memory_block,
            budgeted_memory=budgeted_memory,
            inbox=inbox or [],
            background_results=background_results or [],
            task_runtime_events=budgeted_task_runtime_events.rendered_text,
            raw_task_runtime_events=raw_task_runtime_events,
            budgeted_task_runtime_events=budgeted_task_runtime_events,
            context_frame=context_frame,
            reductions=[
                *instruction_reductions,
                *self._reduction_list(budgeted_memory, budgeted_task_runtime_events),
            ],
        )
        return ContextBundle(messages=messages, report=report)

    def _build_context_frame(
        self,
        *,
        memory_block: str,
        task_runtime_events_block: str,
    ) -> str:
        sections = []

        if memory_block:
            sections.append(memory_block)

        if task_runtime_events_block:
            sections.append(task_runtime_events_block)

        return "\n\n".join(sections)
    
    def _build_system_prompt(
        self,
        *,
        profile_prompt: str,
        instruction_block: str,
        runtime_guidance: str,
    ) -> str:
        sections = [
            profile_prompt,
            instruction_block,
            runtime_guidance,
        ]
        return "\n\n".join(section for section in sections if section.strip())

    def _runtime_guidance(self) -> str:
        return "\n".join([
            "Use recall_memory when the user asks about prior preferences or project conventions.",
            "Use memorize when the user states a durable preference or important fact.",
            "Some tools are deferred. Use tool_search to find or unlock tools that are not currently visible.",
        ])

    def _build_instruction_block(
        self,
        profile,
    ) -> tuple[str, list[ContextSection], list[dict[str, Any]]]:
        mode = str(getattr(profile, "tool_mode", "bot") or "bot")
        files = self._instruction_files(mode)

        grouped: dict[str, list[dict[str, Any]]] = {
            "mode_instructions": [],
            "project_instructions": [],
        }
        for section_name, path in files:
            text, raw_text, truncated = self._read_instruction_file(path)
            if text:
                source = _relative_or_name(path, self.instruction_root)
                grouped[section_name].append({
                    "source": source,
                    "text": text,
                    "raw_text": raw_text,
                    "truncated": truncated,
                })

        blocks = []
        report_sections = []
        reductions = []
        for section_name, items in grouped.items():
            if not items:
                continue
            rendered_text = "\n\n".join(item["text"] for item in items)
            raw_text = "\n\n".join(item["raw_text"] for item in items)
            budgeted = self.budgeter.apply(
                section_name,
                rendered_text,
                raw_text=raw_text,
            )
            if budgeted.reduction is not None:
                reductions.append(budgeted.reduction)
            sources = [item["source"] for item in items]
            blocks.append(
                f"<instructions section=\"{section_name}\" sources=\"{','.join(sources)}\">\n"
                f"{budgeted.rendered_text}\n"
                "</instructions>"
            )
            report_sections.append(
                ContextSection.from_text(
                    section_name,
                    budgeted.rendered_text,
                    raw_text=raw_text,
                    budget_chars=budgeted.budget_chars
                    if self.budgeter.enabled
                    else self.instruction_limit * len(items),
                    truncated=any(item["truncated"] for item in items)
                    or budgeted.truncated,
                    metadata={
                        "mode": mode,
                        "sources": sources,
                        **budgeted.metadata,
                        "files": [
                            {
                                "source": item["source"],
                                "raw_chars": len(item["raw_text"]),
                                "rendered_chars": len(item["text"]),
                                "truncated": bool(item["truncated"]),
                            }
                            for item in items
                        ],
                    },
                )
            )
        return "\n\n".join(blocks), report_sections, reductions

    def _instruction_files(self, mode: str) -> list[tuple[str, Path]]:
        if mode == "coding":
            return [
                ("mode_instructions", self.instruction_root / ".agent" / "coding.md"),
                ("project_instructions", self.instruction_root / "AGENTS.md"),
            ]
        return [
            ("mode_instructions", self.instruction_root / ".agent" / "assistant.md"),
        ]

    def _read_instruction_file(self, path: Path) -> tuple[str, str, bool]:
        if not path.is_file():
            return "", "", False
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return "", "", False
        if len(text) <= self.instruction_limit:
            return text, text, False
        rendered = text[: self.instruction_limit].rstrip() + "\n\n...[truncated]"
        return rendered, text, True
    
    def _build_memory_block(self, session) -> str:
        if self.memory_store is None:
            return ""

        store = self.memory_store
        if hasattr(store, "for_session"):
            store = store.for_session(session)
        text = store.read_all().strip()
        if not text:
            return ""

        return "<memory>\n" + text + "\n</memory>"

    def _build_report(
        self,
        *,
        messages: list[dict],
        profile_prompt: str,
        instruction_sections: list[ContextSection],
        runtime_guidance: str,
        system_prompt: str,
        session_messages: list[dict],
        history_messages: list[dict],
        current_request: str,
        memory_block: str,
        raw_memory_block: str,
        budgeted_memory: BudgetedText,
        inbox: list,
        background_results: list,
        task_runtime_events: str,
        raw_task_runtime_events: str,
        budgeted_task_runtime_events: BudgetedText,
        context_frame: str,
        reductions: list[dict[str, Any]],
    ) -> ContextBuildReport:
        sections = [
            ContextSection.from_text("system_profile", profile_prompt),
            *instruction_sections,
            ContextSection.from_text("runtime_guidance", runtime_guidance),
            ContextSection.from_text(
                "system_prompt",
                system_prompt,
                metadata={
                    "composed": True,
                },
            ),
            ContextSection(
                name="conversation_history",
                raw_chars=message_chars(history_messages),
                rendered_chars=message_chars(history_messages),
                metadata={
                    "message_count": len(history_messages),
                    "transport": "chat_messages",
                },
            ),
            ContextSection.from_text(
                "current_request",
                current_request,
                metadata={
                    "transport": "chat_message",
                    "preserve": True,
                },
            ),
            ContextSection.from_text(
                "memory",
                memory_block,
                raw_text=raw_memory_block,
                budget_chars=budgeted_memory.budget_chars,
                truncated=budgeted_memory.truncated,
                metadata={
                    "transport": "context_frame",
                    **budgeted_memory.metadata,
                },
            ),
            ContextSection.from_text(
                "task_runtime_events",
                task_runtime_events,
                raw_text=raw_task_runtime_events,
                budget_chars=budgeted_task_runtime_events.budget_chars,
                truncated=budgeted_task_runtime_events.truncated,
                metadata={
                    "inbox_count": len(inbox),
                    "background_result_count": len(background_results),
                    "transport": "context_frame",
                    **budgeted_task_runtime_events.metadata,
                },
            ),
            ContextSection.from_text(
                "inbox",
                json.dumps(inbox, ensure_ascii=False, default=str) if inbox else "",
                metadata={"item_count": len(inbox)},
            ),
            ContextSection.from_text(
                "background_results",
                json.dumps(background_results, ensure_ascii=False, default=str)
                if background_results
                else "",
                metadata={"item_count": len(background_results)},
            ),
            ContextSection.from_text("context_frame", context_frame),
        ]
        return ContextBuildReport(
            total_chars=message_chars(messages),
            budget_chars=self.budgeter.total_budget_chars if self.budgeter.enabled else None,
            over_budget=(
                self.budgeter.enabled
                and self.budgeter.total_budget_chars is not None
                and message_chars(messages) > self.budgeter.total_budget_chars
            ),
            sections=sections,
            reductions=reductions,
            metadata={
                "message_count": len(messages),
                "section_budget_enabled": self.budgeter.enabled,
            },
        )

    def _split_current_request(
        self,
        session_messages: list[dict],
    ) -> tuple[list[dict], str]:
        history_messages = list(session_messages)
        for index in range(len(history_messages) - 1, -1, -1):
            message = history_messages[index]
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "") != "user":
                continue
            current = _message_text(message)
            del history_messages[index]
            return history_messages, current
        return history_messages, ""

    def _build_task_runtime_events_block(
        self,
        *,
        inbox: list,
        background_results: list,
    ) -> str:
        parts = []
        if inbox:
            parts.append(
                "<inbox>\n"
                + json.dumps(inbox, indent=2, ensure_ascii=False, default=str)
                + "\n</inbox>"
            )
        if background_results:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}"
                for n in background_results
            )
            parts.append(
                "<background-results>\n"
                + notif_text
                + "\n</background-results>"
            )
        return "\n\n".join(parts)

    def _reduction_list(self, *items: BudgetedText) -> list[dict[str, Any]]:
        return [item.reduction for item in items if item.reduction is not None]


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")
