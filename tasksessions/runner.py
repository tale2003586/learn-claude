from core.context import ContextBuilder
from core.pipeline import DEFAULT_MAX_REASONING_STEPS, Pipeline, get_last_assistant_text
from config import WORKDIR
from memory.store import MemoryStore
from sessions import SessionManager
from .artifacts import TaskArtifactPaths, TaskArtifactWriter
from .conclusions import TaskConclusionExtractor
from .memory_lifecycle import TaskMemoryLifecycle
from .promotion import TaskMemoryPromoter, PromotionResult
from .session import TaskSessionFactory, TaskSessionRecord
from user_scope import explicit_user_id_for_session, user_role_for_session


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
        self.conclusion_extractor = TaskConclusionExtractor(
            provider=base_pipeline.provider,
            model=base_pipeline.model,
        )
        self.artifact_writer = TaskArtifactWriter()

    def run_coding_task(self, *, parent_session, user_text: str, profile) -> str:
        global_memory = self._global_memory_for(parent_session)
        record = self.factory.create(
            parent_session_id=parent_session.id,
            task_type="coding",
            user_request=user_text,
            user_id=explicit_user_id_for_session(parent_session),
            user_role=user_role_for_session(parent_session),
        )
        task_memory = MemoryStore(record.memory_root)
        self._seed_task_memory(
            task_memory=task_memory,
            parent_session_id=parent_session.id,
            user_text=user_text,
        )
        record.session.add_message(
            "user",
            self._build_task_request(parent_session.id, user_text, global_memory),
        )

        task_pipeline = self._build_task_pipeline(task_memory)
        task_pipeline.run(record.session, profile)

        reply = get_last_assistant_text(record.session.messages)
        record.session.metadata["status"] = "completed"
        record.session.metadata["task_reply"] = reply

        extraction = self.conclusion_extractor.extract(
            user_request=user_text,
            task_summary=reply,
            messages=record.session.messages,
        )
        promotion = TaskMemoryPromoter(global_memory).promote(
            task_id=record.task_id,
            task_memory=task_memory,
            extracted_conclusions=extraction.candidates,
        )
        artifacts = None
        try:
            artifacts = self.artifact_writer.write(
                record=record,
                user_request=user_text,
                task_reply=reply,
                extraction=extraction,
                promotion=promotion,
            )
            record.session.metadata["task_log_path"] = _portable_path(artifacts.task_log_path)
            record.session.metadata["conclusions_path"] = _portable_path(artifacts.conclusions_path)
        except Exception as exc:
            record.session.metadata["artifact_error"] = f"{type(exc).__name__}: {exc}"

        if extraction.error:
            record.session.metadata["conclusion_extraction_error"] = extraction.error
        self.sessions.save(record.session)
        return self._format_parent_reply(record, reply, promotion, artifacts)

    def _build_task_pipeline(self, task_memory: MemoryStore) -> Pipeline:
        return Pipeline(
            tools=self.base_pipeline.tools,
            provider=self.base_pipeline.provider,
            model=self.base_pipeline.model,
            tool_executor=self.base_pipeline.tool_executor,
            context_builder=ContextBuilder(memory_store=task_memory),
            memory_lifecycle=TaskMemoryLifecycle(task_memory),
            max_tokens=self.base_pipeline.max_tokens,
            max_reasoning_steps=getattr(
                self.base_pipeline,
                "max_reasoning_steps",
                DEFAULT_MAX_REASONING_STEPS,
            ),
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

    def _build_task_request(
        self,
        parent_session_id: str,
        user_text: str,
        global_memory: MemoryStore,
    ) -> str:
        global_memory_text = global_memory.read_all()
        return (
            f"<task-session parent_session=\"{parent_session_id}\">\n"
            "You are running in an isolated coding task session. "
            "Use the task-local context for intermediate work. "
            "Only durable project conventions or important findings should be memorized. "
            "When you discover a reusable project conclusion, call memorize with "
            "section='pending' so it can be reviewed for global promotion.\n"
            "</task-session>\n\n"
            "<global-memory-snapshot>\n"
            f"{global_memory_text}\n"
            "</global-memory-snapshot>\n\n"
            f"User coding task:\n{user_text}"
        )

    def _global_memory_for(self, session) -> MemoryStore:
        if hasattr(self.global_memory, "for_session"):
            return self.global_memory.for_session(session)
        return self.global_memory

    def _format_parent_reply(
        self,
        record: TaskSessionRecord,
        reply: str,
        promotion: PromotionResult,
        artifacts: TaskArtifactPaths | None,
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
        if promotion.rejected:
            lines.extend([
                "",
                f"Rejected {len(promotion.rejected)} noisy task memory candidate(s).",
            ])
        if artifacts is not None:
            lines.extend([
                "",
                f"Task log: `{_portable_path(artifacts.task_log_path)}`",
                f"Conclusions: `{_portable_path(artifacts.conclusions_path)}`",
            ])
        return "\n".join(lines)


def _portable_path(path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(WORKDIR.resolve()).as_posix()
    except ValueError:
        return str(resolved)
