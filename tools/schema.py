"""Tool schemas for the team agent harness.

This file only describes what the model can call. The actual Python handlers
live in tools/handlers.py and are selected by identity: lead vs teammate.
"""


def function_tool(name: str, description: str, properties: dict,
                  required: list = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


BASE_TOOLS = [
    function_tool(
        "bash",
        "Run a shell command. Use for quick commands like ls, rg, git, python, cat, etc.",
        {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
        },
        ["command"],
    ),
    function_tool(
        "read_file",
        "Read UTF-8 file contents. Optionally limit the number of lines.",
        {
            "path": {
                "type": "string",
                "description": "Relative path to the file.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read. If omitted, read the whole file.",
            },
        },
        ["path"],
    ),
    function_tool(
        "write_file",
        "Write content to a file. Creates parent directories if needed.",
        {
            "path": {
                "type": "string",
                "description": "Relative path for the file.",
            },
            "content": {
                "type": "string",
                "description": "Content to write.",
            },
        },
        ["path", "content"],
    ),
    function_tool(
        "edit_file",
        "Replace exact text in a file. Use for precise edits.",
        {
            "path": {
                "type": "string",
                "description": "Relative path to the file.",
            },
            "old_text": {
                "type": "string",
                "description": "Exact text to find.",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text.",
            },
        },
        ["path", "old_text", "new_text"],
    ),
]


SKILL_TOOLS = [
    function_tool(
        "load_skill",
        "Load a specialized skill by name before tackling unfamiliar work.",
        {
            "name": {
                "type": "string",
                "description": "Skill name to load.",
            },
        },
        ["name"],
    ),
]


TASK_TOOLS = [
    function_tool(
        "task_create",
        "Create a persistent task in the task system.",
        {
            "subject": {
                "type": "string",
                "description": "Task title or short subject.",
            },
            "description": {
                "type": "string",
                "description": "Optional detailed task description.",
            },
        },
        ["subject"],
    ),
    function_tool(
        "task_update",
        "Update a task's status or dependency blockers.",
        {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New task status.",
            },
            "addBlockedBy": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs that this task should be blocked by.",
            },
            "removeBlockedBy": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs to remove from this task's blockers.",
            },
        },
        ["task_id"],
    ),
    function_tool(
        "task_list",
        "List all persistent tasks with status summary.",
        {},
    ),
    function_tool(
        "task_get",
        "Get full details of a task by ID.",
        {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to inspect.",
            },
        },
        ["task_id"],
    ),
    function_tool(
        "claim_task",
        "Claim an available task by ID and mark it as your in-progress work.",
        {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to claim.",
            },
        },
        ["task_id"],
    ),
]


BACKGROUND_TOOLS = [
    function_tool(
        "background_run",
        "Run a long shell command in a background thread. Returns a task_id immediately.",
        {
            "command": {
                "type": "string",
                "description": "The shell command to run in the background.",
            },
        },
        ["command"],
    ),
    function_tool(
        "check_background",
        "Check background task status. Omit task_id to list all background tasks.",
        {
            "task_id": {
                "type": "string",
                "description": "Optional background task ID to inspect.",
            },
        },
    ),
]


COMMUNICATION_TOOLS = [
    function_tool(
        "send_message",
        "Send a message to another teammate's inbox.",
        {
            "to": {
                "type": "string",
                "description": "Recipient teammate name.",
            },
            "content": {
                "type": "string",
                "description": "Message content to send.",
            },
            "msg_type": {
                "type": "string",
                "enum": [
                    "message",
                    "broadcast",
                    "shutdown_request",
                    "shutdown_response",
                    "plan_approval_request",
                    "plan_approval_response",
                ],
                "description": "Message type. Defaults to message.",
            },
        },
        ["to", "content"],
    ),
    function_tool(
        "read_inbox",
        "Read and drain your own inbox.",
        {},
    ),
]


TEAMMATE_PROTOCOL_TOOLS = [
    function_tool(
        "idle",
        "Indicate that your current work is done and enter the idle polling phase.",
        {},
    ),
    function_tool(
        "shutdown_response",
        "Respond to a shutdown_request from the lead.",
        {
            "request_id": {
                "type": "string",
                "description": "Shutdown request ID from the inbox message.",
            },
            "approve": {
                "type": "boolean",
                "description": "True if shutdown is accepted, false if it cannot be completed.",
            },
            "details": {
                "type": "string",
                "description": "Optional explanation or final status details.",
            },
        },
        ["request_id", "approve"],
    ),
    function_tool(
        "plan_approval_request",
        "Submit a plan to the lead for approval before major work.",
        {
            "plan": {
                "type": "string",
                "description": "Plan text to send to the lead for review.",
            },
        },
        ["plan"],
    ),
]


LEAD_ONLY_TOOLS = [
    function_tool(
        "task",
        """Spawn a short-lived subagent for a focused subtask with fresh context.
Use for bounded exploration or implementation work when a one-off result is enough.""",
        {
            "prompt": {
                "type": "string",
                "description": "Detailed instructions for the subagent.",
            },
            "description": {
                "type": "string",
                "description": "Short task name for display.",
            },
            "agent_type": {
                "type": "string",
                "enum": ["explore", "code", "plan"],
                "description": "Subagent type.",
            },
        },
        ["prompt"],
    ),
    function_tool(
        "compact",
        "Manually compress the conversation history into a continuity summary.",
        {
            "focus": {
                "type": "string",
                "description": "Optional focus: details to preserve in the summary.",
            },
        },
    ),
    function_tool(
        "spawn_teammate",
        "Spawn a persistent teammate that runs in its own thread and communicates through inbox messages.",
        {
            "name": {
                "type": "string",
                "description": "Unique teammate name, such as alice or tester.",
            },
            "role": {
                "type": "string",
                "description": "Teammate role, such as coder, tester, reviewer, researcher, or planner.",
            },
            "prompt": {
                "type": "string",
                "description": "Initial task instructions for the teammate.",
            },
        },
        ["name", "role", "prompt"],
    ),
    function_tool(
        "list_teammates",
        "List all teammates with their names, roles, and current statuses.",
        {},
    ),
    function_tool(
        "broadcast",
        "Broadcast a message from the lead to all teammates.",
        {
            "content": {
                "type": "string",
                "description": "Broadcast message content.",
            },
        },
        ["content"],
    ),
    function_tool(
        "shutdown_request",
        "Ask a teammate to shut down gracefully and create a tracked shutdown request.",
        {
            "teammate": {
                "type": "string",
                "description": "Name of the teammate to ask to shut down.",
            },
        },
        ["teammate"],
    ),
    function_tool(
        "shutdown_status",
        "Check the tracked status of a shutdown request by request_id.",
        {
            "request_id": {
                "type": "string",
                "description": "Shutdown request ID to inspect.",
            },
        },
        ["request_id"],
    ),
    function_tool(
        "plan_approval",
        "Approve or reject a teammate's submitted plan.",
        {
            "request_id": {
                "type": "string",
                "description": "Plan approval request ID from the lead inbox.",
            },
            "approve": {
                "type": "boolean",
                "description": "True to approve the plan, false to reject it.",
            },
            "feedback": {
                "type": "string",
                "description": "Optional feedback to send back to the teammate.",
            },
        },
        ["request_id", "approve"],
    ),
]

MEMORY_TOOLS = [
    function_tool(
        "memorize",
        "Save an important long-term memory, user preference, project convention, or current state.",
        {
            "content": {
                "type": "string",
                "description": "The memory content to save.",
            },
            "section": {
                "type": "string",
                "enum": ["memory", "self", "now", "pending"],
                "description": (
                    "Where to save it. Defaults to memory. In a coding task, use pending "
                    "for durable project conclusions that should be reviewed for promotion."
                ),
            },
        },
        ["content"],
    ),
    function_tool(
        "recall_memory",
        "Read long-term memory before answering questions that may depend on preferences, project conventions, or current state.",
        {
            "query": {
                "type": "string",
                "description": "Optional query or reason for recall.",
            },
        },
    ),
]

SEARCH_TOOLS = [
    function_tool(
        "tool_search",
        (
            "Search available deferred tools or unlock one for this turn. "
            "Use query='select:<tool_name>' to unlock a specific tool, such as select:bash."
        ),
        {
            "query": {
                "type": "string",
                "description": (
                    "Search text, or select:<tool_name> to unlock a deferred tool "
                    "that is allowed in the current mode."
                ),
            },
        },
        ["query"],
    ),
]



# Team-oriented tool sets.
TEAMMATE_TOOLS = (
    BASE_TOOLS
    + SKILL_TOOLS
    + TASK_TOOLS
    + BACKGROUND_TOOLS
    + COMMUNICATION_TOOLS
    + TEAMMATE_PROTOCOL_TOOLS
)

LEAD_TOOLS = TEAMMATE_TOOLS + LEAD_ONLY_TOOLS + MEMORY_TOOLS + SEARCH_TOOLS

# Temporary compatibility aliases for older imports. Prefer TEAMMATE_TOOLS and
# LEAD_TOOLS in new code.
CHILD_TOOLS = TEAMMATE_TOOLS
PARENT_TOOLS = LEAD_TOOLS
