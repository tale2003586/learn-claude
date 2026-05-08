from requests import session

from bus.user_bus import InboundMessage, OutboundMessage, MessageBus
from modes import router
import pipeline
from session import SessionManager


class AgentLoop:
    def __init__(self, bus: MessageBus, sessions: SessionManager, pipeline: pipeline, router: router) -> None:
        self.bus = bus
        self.sessions = sessions
        self.pipeline = pipeline
        self.router = router

    async def run_once(self) -> None:
        inbound = await self.bus.consume_inbound()

        session = self.sessions.get_or_create(inbound.session_key)

        route = self.router.route(session, inbound.content)

        if route.switched:
            self.sessions.save(session)
            await self.bus.publish_outbound(OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=route.switch_message or "",
            ))
            return

        session.add_message(
            "user",
            inbound.content,
            media=inbound.media,
            metadata=inbound.metadata,
        )

        reply = self.pipeline.run(session, route.profile)

        self.sessions.save(session)

        await self.bus.publish_outbound(OutboundMessage(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            content=reply,
        ))

    def _session_id(self, message: InboundMessage) -> str:
        return f"{message.channel}:{message.chat_id}"
