import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] | None = None


class OpenAICompatibleProvider:
    def __init__(self, client) -> None:
        self.client = client

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
        )

        message = response.choices[0].message
        tool_calls: list[ToolCall] = []

        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments)
            except Exception:
                arguments = {}

            tool_calls.append(ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=arguments,
            ))

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            raw_message=message.model_dump(exclude_none=True),
        )
