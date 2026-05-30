import json
from dataclasses import dataclass, field
from typing import Any, Callable


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
        request = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = tool_choice
        response = self.client.chat.completions.create(
            **request,
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

    def stream_chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int,
        on_text: Callable[[str], None],
        tool_choice: str = "auto",
    ) -> LLMResponse:
        request = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = tool_choice

        content_parts: list[str] = []
        streamed_tool_calls: dict[int, dict[str, str]] = {}
        for chunk in self.client.chat.completions.create(**request):
            choices = getattr(chunk, "choices", [])
            if not choices:
                continue
            delta = choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                on_text(text)
            for call in getattr(delta, "tool_calls", None) or []:
                item = streamed_tool_calls.setdefault(
                    call.index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if getattr(call, "id", None):
                    item["id"] += call.id
                function = getattr(call, "function", None)
                if function is not None:
                    item["name"] += getattr(function, "name", "") or ""
                    item["arguments"] += getattr(function, "arguments", "") or ""

        content = "".join(content_parts)
        tool_calls = []
        raw_tool_calls = []
        for index in sorted(streamed_tool_calls):
            item = streamed_tool_calls[index]
            try:
                arguments = json.loads(item["arguments"])
            except Exception:
                arguments = {}
            tool_calls.append(ToolCall(
                id=item["id"],
                name=item["name"],
                arguments=arguments,
            ))
            raw_tool_calls.append({
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            })

        raw_message: dict[str, Any] = {
            "role": "assistant",
            "content": content or None,
        }
        if raw_tool_calls:
            raw_message["tool_calls"] = raw_tool_calls
        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            raw_message=raw_message,
        )
