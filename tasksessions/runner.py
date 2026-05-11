from core.context import ContextBuilder
from core.pipeline import Pipeline, get_last_assistant_text
from memory.lifecycle import MemoryLifecycle
from memory.store import MemoryStore
from sessions import SessionManager
from .promotion import TaskMemoryPromoter, PromotionResult
from .session import TaskSessionFactory, TaskSessionRecord


class TaskSessionRunner:
    def __init__(
        self,
        *,
        sessions: SessionManager,
        base_pipeline: Pipeline,
        global_memory: MemoryStore,
    ) -> None:
        self.sessions = sessions
        self.base_pipeline = base_pipeline
        self.global_memory = global_memory
        self.factory = TaskSessionFactory(sessions)
        self.promoter = TaskMemoryPromoter(global_memory)

    def run_coding_task(self, *, parent_session, user_text: str, profile) -> str:
        record = self.factory.create(
            parent_session_id=parent_session.id,
            task_type="coding",
            user_request=user_text,
        )
        task_memory = MemoryStore(record.memory_root)
        self._seed_task_memory(
            task_memory=task_memory,
            parent_session_id=parent_session.id,
            user_text=user_text,
        )
        record.session.add_message(
            "user",
            self._build_task_request(parent_session.id, user_text),
        )

        task_pipeline = self._build_task_pipeline(task_memory)
        task_pipeline.run(record.session, profile)

        reply = get_last_assistant_text(record.session.messages)
        record.session.metadata["status"] = "completed"
        record.session.metadata["task_reply"] = reply
        self.sessions.save(record.session)

        promotion = self.promoter.promote(
            task_id=record.task_id,
            task_memory=task_memory,
            task_summary=reply,
        )
        return self._format_parent_reply(record, reply, promotion)

    def _build_task_pipeline(self, task_memory: MemoryStore) -> Pipeline:
        return Pipeline(
            tools=self.base_pipeline.tools,
            provider=self.base_pipeline.provider,
            model=self.base_pipeline.model,
            tool_executor=self.base_pipeline.tool_executor,
            context_builder=ContextBuilder(memory_store=task_memory),
            memory_lifecycle=MemoryLifecycle(task_memory),
            max_tokens=self.base_pipeline.max_tokens,
        )

    def _seed_task_memory(
        self,
        *,
        task_memory: MemoryStore,
        parent_session_id: str,
        user_text: str,
    ) -> None:
        task_memory.append("now", f"Parent session: {parent_session_id}")
        task_memory.append("now", f"Task request: {user_text}")
        task_memory.append(
            "memory",
            "This task session may read global context but should only write durable "
            "findings to task-local memory. Useful findings are promoted after completion.",
        )
        task_memory.write_recent_context(
            f"- parent_session: `{parent_session_id}`\n"
            f"- task_request: {user_text}"
        )

    def _build_task_request(self, parent_session_id: str, user_text: str) -> str:
        global_memory = self.global_memory.read_all()
        return (
            f"<task-session parent_session=\"{parent_session_id}\">\n"
            "You are running in an isolated coding task session. "
            "Use the task-local context for intermediate work. "
            "Only durable project conventions or important findings should be memorized.\n"
            "</task-session>\n\n"
            "<global-memory-snapshot>\n"
            f"{global_memory}\n"
            "</global-memory-snapshot>\n\n"
            f"User coding task:\n{user_text}"
        )

    def _format_parent_reply(
        self,
        record: TaskSessionRecord,
        reply: str,
        promotion: PromotionResult,
    ) -> str:
        lines = [
            f"[TaskSession `{record.task_id}` completed]",
            "",
            reply.strip() or "(no assistant reply)",
        ]
        if promotion.promoted:
            lines.extend([
                "",
                f"Promoted {len(promotion.promoted)} task memory item(s) to global PENDING.md.",
            ])
        if promotion.skipped:
            lines.extend([
                "",
                f"Skipped {len(promotion.skipped)} duplicate task memory item(s).",
            ])
        return "\n".join(lines)
