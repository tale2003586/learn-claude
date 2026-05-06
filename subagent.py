"""Subagent pattern - Context isolation for focused subtasks.

Per agent-builder skill (subagent-pattern.py):
  "Spawn child agents with ISOLATED context to prevent context pollution
   where exploration details fill up the main conversation."

Key concepts:
1. AGENT TYPE REGISTRY - Each type has tool whitelist + specialized prompt
2. ISOLATED HISTORY - Subagent starts fresh, no parent context
3. FILTERED TOOLS - Based on agent type permissions
4. RETURNS SUMMARY ONLY - Parent sees just the final result
"""

import json
from openai import OpenAI

from mytry.tools import TOOL_HANDLERS


# =============================================================================
# AGENT TYPE REGISTRY (from subagent-pattern.py)
# =============================================================================

AGENT_TYPES = {
    "explore": {
        "description": "Read-only agent for exploring code, finding files, searching",
        "tools": {"bash", "read_file"},  # No write access!
        "prompt": (
            "You are an exploration agent. Search and analyze, but NEVER modify files. "
            "Return a concise summary of what you found."
        ),
    },
    "code": {
        "description": "Full agent for implementing features and fixing bugs",
        "tools": None,  # None = all tools (except Task to prevent recursion)
        "prompt": (
            "You are a coding agent. Implement the requested changes efficiently. "
            "Return a summary of what you changed."
        ),
    },
    "plan": {
        "description": "Planning agent for designing implementation strategies",
        "tools": {"bash", "read_file"},  # Read-only
        "prompt": (
            "You are a planning agent. Analyze the codebase and output a numbered "
            "implementation plan. Do NOT make any changes."
        ),
    },
}


def filter_tools(agent_type: str, base_tools: list) -> list:
    """Filter tools based on agent type permissions.
    
    - explore: only bash + read_file (read-only)
    - code: all tools
    - plan: only bash + read_file (read-only)
    """
    allowed = AGENT_TYPES.get(agent_type, {}).get("tools")
    if allowed is None:
        return base_tools  # All tools
    return [t for t in base_tools if t["function"]["name"] in allowed]


def run_subagent(
    task: str,
    client: OpenAI,
    model: str,
    base_tools: list,
    agent_type: str = "code",
) -> str:
    """Execute a subagent task with isolated context.
    
    Args:
        task: Detailed instructions for the subagent
        client: OpenAI client
        model: Model name
        base_tools: List of tool definitions (CHILD_TOOLS)
        agent_type: Type from AGENT_TYPES registry
    
    Returns:
        Final text output from subagent (summary only)
    """
    if agent_type not in AGENT_TYPES:
        return f"Error: Unknown agent type '{agent_type}'"

    config = AGENT_TYPES[agent_type]
    sub_tools = filter_tools(agent_type, base_tools)

    # ISOLATED context - subagent starts fresh, no parent history
    sub_messages = [
        {"role": "system", "content": config["prompt"]},
        {"role": "user", "content": task},
    ]

    for _ in range(30):
        response = client.chat.completions.create(
            model=model,
            messages=sub_messages,
            tools=sub_tools,
            tool_choice="auto",
            max_tokens=8000,
        )
        message = response.choices[0].message
        sub_messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            break

        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments)
                handler = TOOL_HANDLERS.get(call.function.name)
                output = handler(**args) if handler else f"Unknown tool: {call.function.name}"
            except Exception as e:
                output = f"Error: {e}"

            sub_messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })

    # Return only the final text summary
    content = sub_messages[-1].get("content", "")
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return "\n".join(texts)
    return str(content)
