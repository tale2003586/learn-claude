import json
import time

from config import client, MODEL, WORKDIR
from config import KEEP_RECENT, PRESERVE_RESULT_TOOLS, TRANSCRIPT_DIR


def mirco_compact(messages:list) -> list:
    """Compact message history by summarizing older messages.
    
    - Keeps the last 6 messages in full detail.
    - Summarizes earlier messages into a single summary message.
    - Reduces token count while preserving context.
    """
    tool_results = []

    for msg_idx, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_results.append((msg_idx, msg))
    if len(tool_results) <= KEEP_RECENT:
        return messages  # No compaction needed
    
    tool_name_map = {}

    for msg in messages:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls", []) or []:
                call_id = call.get("id")
                function = call.get("function", {})
                if call_id:
                    tool_name_map[call_id] = function.get("name", "unknown_tool")

    to_clear = tool_results[:-KEEP_RECENT]
    for _, result in to_clear:
        content = result.get("content")
        if not isinstance(content, str) or len(content) <= 100:
            continue
        tool_id = result.get("tool_call_id")
        tool_name = tool_name_map.get(tool_id, "unknown_tool")
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        result["content"] = f"<{tool_name} result hidden for compactness>"
    return messages

def auto_compact(messages:list) -> list:
    """Automatically compact messages if token count exceeds threshold."""
    # This is a placeholder. In practice, you'd calculate token count and call mirco_compact as needed.
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    print(f"[transcript saved: {transcript_path}]")
    conversation_text = json.dumps(messages, default=str)[-80000:]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity. Include: "
            "1) What was accomplished, 2) Current state, 3) Key decisions made. "
            "Be concise but preserve critical details.\n\n" + conversation_text}],
        max_tokens=2000,
    )

    summary = response.choices[0].message.content or ""

    if not summary:
        summary = "No summary generated."

    return [
        {
            "role": "user",
            "content": f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}",
        }
    ]

def estimate_tokens(messages: list) -> int:
    """Rough token count: ~4 chars per token."""
    return len(str(messages)) // 4
