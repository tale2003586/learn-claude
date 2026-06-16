SUBTASK_TOOL_WHITELIST = {
    "explore": {
        "bash",
        "list_files",
        "read_file",
        "git_status",
        "git_diff",
        "git_log",
        "storage_list_files",
        "storage_read_file",
        "tool_search",
        "security_rag_search",
    },
    "code": {
        "bash",
        "list_files",
        "read_file",
        "write_file",
        "edit_file",
        "git_status",
        "git_diff",
        "git_log",
        "git_branch",
        "git_add",
        "git_commit",
        "background_run",
        "check_background",
        "load_skill",
        "tool_search",
        "security_rag_search",
        "memorize",
        "recall_memory",
    },
    "plan": {
        "bash",
        "list_files",
        "read_file",
        "git_status",
        "git_diff",
        "git_log",
        "tool_search",
        "security_rag_search",
    },
}


SUBTASK_SYSTEM_PROMPTS = {
    "explore": (
        "You are a short-lived exploration subagent. Inspect only what is needed, "
        "summarize findings clearly, and avoid changing files."
    ),
    "code": (
        "You are a short-lived coding subagent. Make focused edits for the assigned "
        "task, verify when practical, and report the exact changes."
    ),
    "plan": (
        "You are a short-lived planning subagent. Produce a concise actionable plan "
        "grounded in the repository context you inspect."
    ),
}


DEFAULT_SUBTASK_AGENT_TYPE = "explore"
