import json

from background_task import BG
from compact import auto_compact, estimate_tokens, mirco_compact
from config import THRESHOLD, client, MODEL
from bus.team_bus import BUS

class Pipeline:
    def __init__(self, tools) -> None:
        self.tools = tools

    def run(self, session, profile) -> str:
        agent_loop(session, self.tools, profile)
        return get_last_assistant_text(session.messages)




def get_last_assistant_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return message_text(message)
    return ""


def agent_loop(session, tools, profile) -> None:

    """The universal agent loop.
    
    Per agent-builder skill:
      LOOP:
        Model sees: conversation history + available tools
        Model decides: act or respond
        If act: tool executed, result added to context, loop continues
        If respond: answer returned, loop ends
    """

    messages = session.messages

    while True:
        mirco_compact(messages)

        if estimate_tokens(messages) > THRESHOLD:
            print("auto Compacting...")
            messages[:] = auto_compact(messages)
            session.mark_compacted()

        notifs = BG.drain_notifications()

        inbox = BUS.read_inbox("lead")
        if inbox:
            messages.append({"role": "user", "content": f"<inbox>\n{json.dumps(inbox, indent=2)}\n</inbox>"})

        if notifs and messages:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
            )
            messages.append({"role": "user", "content": f"<background-results>\n{notif_text}\n</background-results>"})
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": profile.system_prompt}, *messages],
            tools=tools.schemas_for_mode(profile.tool_mode),
            tool_choice="auto",
            max_tokens=8000,
        )

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        # If no tool calls, the model chose to respond -> loop ends
        if not message.tool_calls:
            return

        manual_compact = False
        for call in message.tool_calls:
            if call.function.name == "compact":
                manual_compact = True
                output = "Manual compact requested."
            else:
                output = execute_tool_call(call, tools)
            print_tool_output(call.function.name, output)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })

        if manual_compact:
            messages[:] = auto_compact(messages)
            session.mark_compacted()


def execute_tool_call(call, tools) -> str:
    try:
        args = json.loads(call.function.arguments)
    except Exception as e:
        return f"Error parsing arguments for {call.function.name}: {e}"

    return tools.execute(call.function.name, args)



def print_response(messages: list) -> None:
    """Print the last assistant response."""
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message_text(message)
            if content:
                print(content)
            return
        
def print_tool_output(name: str, output: str) -> None:
    """Print tool output with truncation for readability."""
    print(f"> {name}:")
    limit = 4000 if name in ("task") else 1200
    if len(output) <= limit:
        print(output)
    else:
        print(output[:limit])
        print(f"... ({len(output) - limit} more chars)")

def message_text(message: dict) -> str:
    """Extract text content from a message (handles various formats)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "".join(parts)
    return ""
