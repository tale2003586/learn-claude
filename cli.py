"""CLI - The harness that runs the agent loop.

Per agent-builder skill:
  "The model IS the agent. Code just runs the loop."
  
  LOOP:
    Model sees: context + available capabilities
    Model decides: act or respond
    If act: execute capability, add result, continue
    If respond: return to user

This is the harness. The magic is in the model, not this code.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import readline
    for setting in (
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ):
        readline.parse_and_bind(setting)
except ImportError:
    pass

from mytry.agent import CHILD_TOOLS, PARENT_TOOLS
from mytry.config import MODEL, SYSTEM, client
from mytry.subagent import run_subagent
from mytry.tools import TOOL_HANDLERS

load_dotenv(override=True)

os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"


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


def print_tool_output(name: str, output: str) -> None:
    """Print tool output with truncation for readability."""
    print(f"> {name}:")
    limit = 4000 if name in ("task", "todo") else 1200
    if len(output) <= limit:
        print(output)
    else:
        print(output[:limit])
        print(f"... ({len(output) - limit} more chars)")


def execute_tool_call(call) -> str:
    """Execute a single tool call and return its output."""
    try:
        args = json.loads(call.function.arguments)
    except Exception as e:
        return f"Error parsing arguments for {call.function.name}: {e}"

    if call.function.name == "task":
        desc = args.get("description", "subtask")
        prompt = args.get("prompt", "")
        agent_type = args.get("agent_type", "code")
        print(f"> task ({desc}) [{agent_type}]: {prompt[:80]}")
        return run_subagent(prompt, client, MODEL, CHILD_TOOLS, agent_type)

    handler = TOOL_HANDLERS.get(call.function.name)
    if not handler:
        return f"Unknown tool: {call.function.name}"

    try:
        return handler(**args)
    except Exception as e:
        return f"Error: {e}"


def agent_loop(messages: list) -> None:
    """The universal agent loop.
    
    Per agent-builder skill:
      LOOP:
        Model sees: conversation history + available tools
        Model decides: act or respond
        If act: tool executed, result added to context, loop continues
        If respond: answer returned, loop ends
    """
    rounds_since_todo = 0

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}, *messages],
            tools=PARENT_TOOLS,
            tool_choice="auto",
            max_tokens=8000,
        )

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        # If no tool calls, the model chose to respond -> loop ends
        if not message.tool_calls:
            return

        used_todo = False
        for call in message.tool_calls:
            output = execute_tool_call(call)
            print_tool_output(call.function.name, output)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })

            if call.function.name == "todo":
                used_todo = True

        # Remind model to update todos if it's been a while
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 5:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })


def print_response(messages: list) -> None:
    """Print the last assistant response."""
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message_text(message)
            if content:
                print(content)
            return


def main() -> None:
    """Main entry point - the interactive harness."""
    print(f"Agent Harness - {Path.cwd()}")
    print("Type 'q' to quit.\n")

    history = []
    while True:
        try:
            query = input("\033[36m>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not query.strip():
            continue
        if query.strip().lower() in ("q", "quit", "exit"):
            break

        history.append({"role": "user", "content": query})
        agent_loop(history)
        print_response(history)
        print("---")


if __name__ == "__main__":
    main()
