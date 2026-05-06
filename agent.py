"""Tool definitions for the coding agent.

Per agent-builder skill: Tools are the agent's HANDS.
Each tool answers: "What can the agent DO?"

Design principle (from skill): 
  "Start with 3-5 capabilities. Add more only when the agent consistently
   fails because a capability is missing."

We have 5 core tools: bash, read_file, write_file, edit_file, todo
Plus: task (subagent) for parent agent only - prevents infinite recursion.
"""

# =============================================================================
# CORE TOOLS - Available to ALL agents (including subagents)
# =============================================================================

CHILD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Use for: ls, find, grep, git, python, pip, cat, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents. Returns UTF-8 text. Optionally limit lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default: all)"
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path for the file"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write"
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. Use for surgical edits - find exact old text and replace with new text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file"
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must match precisely)"
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text"
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update task list. Track progress on multi-step tasks. Only ONE task can be 'in_progress' at a time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Complete list of all tasks with their current status",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Task identifier (e.g. '1', '2')"
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Task description"
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "pending=not started, in_progress=actively working, completed=done"
                                },
                            },
                            "required": ["id", "text", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]

# =============================================================================
# PARENT TOOLS - Only the root agent has Task (spawn subagents)
# Subagents don't get Task to prevent infinite recursion
# =============================================================================

PARENT_TOOLS = CHILD_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": """Spawn a subagent for a focused subtask with FRESH context.
Subagents run in ISOLATED context - they don't see parent's conversation history.
Use this to keep the main conversation clean, especially for exploration.

Agent types:
- explore: Read-only agent for searching, analyzing, finding files. Never modifies anything.
- code: Full-powered agent for implementing features and fixing bugs.
- plan: Read-only agent for designing implementation strategies.

Example: task("Explore the codebase to find auth-related files", "explore")
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed instructions for the subagent"
                    },
                    "description": {
                        "type": "string",
                        "description": "Short task name (3-5 words) for display"
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["explore", "code", "plan"],
                        "description": "Type of agent to spawn: explore(read-only search), code(full implementation), plan(read-only design)"
                    },
                },
                "required": ["prompt"],
            },
        },
    },
]
