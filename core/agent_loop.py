from typing import Callable

from bus.user_bus import OutboundMessage, MessageBus
from core.pipeline import Pipeline
from modes.router import ModeRouter
from sessions import SessionManager


class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,
        sessions: SessionManager,
        pipeline: Pipeline,
        router: ModeRouter,
        plugin_manager=None,
        task_session_runner=None,
    ) -> None:
        self.bus = bus
        self.sessions = sessions
        self.pipeline = pipeline
        self.router = router
        self.plugin_manager = plugin_manager
        self.task_session_runner = task_session_runner

    async def run_once(self, on_text: Callable[[str], None] | None = None) -> None:
        inbound = await self.bus.consume_inbound()

        session = self.sessions.get_or_create(inbound.session_key)
        self._apply_inbound_identity(session, inbound)

        if self.plugin_manager is not None:
            plugin_result = self.plugin_manager.before_turn(inbound, session)
            if plugin_result.abort:
                self.sessions.save(session)
                self._emit_text(on_text, plugin_result.reply)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    content=plugin_result.reply,
                ))
                return

        route = self.router.route(session, inbound.content)

        if route.switched:
            reply = route.switch_message or ""
            session.add_message(
                "user",
                inbound.content,
                media=inbound.media,
                metadata=inbound.metadata,
            )
            session.add_message(
                "assistant",
                reply,
                metadata={
                    "kind": "mode_switch",
                    "mode": session.current_mode,
                },
            )
            self.sessions.save(session)
            self._emit_text(on_text, reply)
            await self.bus.publish_outbound(OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=reply,
            ))
            return

        session.add_message(
            "user",
            inbound.content,
            media=inbound.media,
            metadata=inbound.metadata,
        )

        if (
            self.task_session_runner is not None
            and route.profile.tool_mode == "coding"
        ):
            reply = self.task_session_runner.run_coding_task(
                parent_session=session,
                user_text=inbound.content,
                profile=route.profile,
            )
            session.add_message("assistant", reply)
            self._emit_text(on_text, reply)
        else:
            reply = self.pipeline.run(session, route.profile, on_text=on_text)

        if self.plugin_manager is not None:
            self.plugin_manager.after_turn(inbound, session, reply)

        self.sessions.save(session)

        await self.bus.publish_outbound(OutboundMessage(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            content=reply,
        ))

    def _emit_text(
        self,
        on_text: Callable[[str], None] | None,
        content: str,
    ) -> None:
        if on_text is not None and content:
            on_text(content)

    def _apply_inbound_identity(self, session, inbound) -> None:
        metadata = inbound.metadata or {}
        for key in ("user_id", "user_role"):
            if key in metadata and key not in session.metadata:
                session.metadata[key] = metadata[key]
