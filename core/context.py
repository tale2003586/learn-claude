# context.py

from dataclasses import dataclass
import json


@dataclass
class ContextBundle:
    messages: list[dict]


class ContextBuilder:

    def __init__(self,memory_store = None,) -> None:
        self.memory_store = memory_store

    def build(
        self,
        *,
        session,
        profile,
        inbox: list | None = None,
        background_results: list | None = None,

    ) -> ContextBundle:
        system_prompt = self._build_system_prompt(profile=profile)
        memory_block = self._build_memory_block(session)

        context_frame = self._build_context_frame(
            memory_block=memory_block,
            inbox=inbox or [],
            background_results=background_results or [],
        )

        messages = [
            {"role": "system", "content": system_prompt},
            *session.messages,
        ]

        if context_frame:
            messages.append({
                "role": "user",
                "content": context_frame,
            })

        return ContextBundle(messages=messages)

    def _build_context_frame(
        self,
        *,
        memory_block: str,
        inbox: list,
        background_results: list,
    ) -> str:
        sections = []

        if memory_block:
            sections.append(memory_block)

        if inbox:
            sections.append(
                "<inbox>\n"
                + json.dumps(inbox, indent=2, ensure_ascii=False)
                + "\n</inbox>"
            )

        if background_results:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}"
                for n in background_results
            )
            sections.append(
                "<background-results>\n"
                + notif_text
                + "\n</background-results>"
            )

        return "\n\n".join(sections)
    
    def _build_system_prompt(self, *, profile) -> str:
        sections = [
            profile.system_prompt,
            "Use recall_memory when the user asks about prior preferences or project conventions.",
            "Use memorize when the user states a durable preference or important fact.",
            "Some tools are deferred. Use tool_search to find or unlock tools that are not currently visible.",
        ]
        return "\n\n".join(section for section in sections if section.strip())
    
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
