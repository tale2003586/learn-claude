import asyncio
from dataclasses import dataclass, field
from typing import Callable

from bus.events import InboundMessage
from bus.user_bus import MessageBus
from core.agent_loop import AgentLoop


@dataclass
class AppRuntime:
    bus: MessageBus
    loop: AgentLoop
    _dispatch_task: asyncio.Task | None = field(default=None, init=False)

    def start(self) -> None:
        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(
                self.bus.dispatch_outbound(),
                name="outbound_dispatch",
            )

    async def stop(self) -> None:
        self.bus.stop()
        if self._dispatch_task is not None:
            await self._dispatch_task

    async def submit_user_message(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "local",
        sender: str = "user",
    ) -> None:
        await self.bus.publish_inbound(InboundMessage(
            channel=channel,
            chat_id=chat_id,
            sender=sender,
            content=content,
        ))

    async def run_once(self, on_text: Callable[[str], None] | None = None) -> None:
        await self.loop.run_once(on_text=on_text)
