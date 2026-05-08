import json
import os
from pathlib import Path
import subprocess


from background_task import BG
from config import WORKDIR
from bus.team_bus import BUS
from memory.store import MemoryStore
from protocols import PROTOCOLS
from skills import SKILL_LOADER
from task import TASKS


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"
    
def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
    
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"
    

BASE_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TASK_HANDLERS = {
    "task_create": lambda **kw: TASKS.create(
        kw["subject"],
        kw.get("description", "")
    ),
    "task_update": lambda **kw: TASKS.update(
        kw["task_id"],
        kw.get("status"),
        kw.get("addBlockedBy"),
        kw.get("removeBlockedBy"),
    ),
    "task_list": lambda **kw: TASKS.list_all(),
    "task_get": lambda **kw: TASKS.get(kw["task_id"]),
}

BACKGROUND_HANDLERS = {
    "background_run": lambda **kw: BG.run(kw["command"]),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
}

def make_protocol_handlers(sender: str):
    return {
        "idle": lambda **kw: "Entering idle phase. Will poll for new tasks.",
        "shutdown_response": lambda **kw: PROTOCOLS.handle_shutdown_response(
            sender,
            kw["request_id"],
            kw["approve"],
            kw.get("details", ""),
        ),
        "plan_approval_request": lambda **kw: PROTOCOLS.handle_plan_request(
            sender,
            kw["plan"],
        ),
    }


def make_lead_handlers(team):
    return {
        **BASE_HANDLERS,
        **TASK_HANDLERS,
        **BACKGROUND_HANDLERS,
        **MEMORY_HANDLERS,
        **make_protocol_handlers("lead"),

        "compact": lambda **kw: "Manual compression requested.",
        "task": lambda **kw: (
            "Error: The short-lived subagent task tool is not wired in this "
            "DeepSeek harness. Use spawn_teammate for persistent teammates."
        ),
        "claim_task": lambda **kw: TASKS.claim_task(
            kw["task_id"],
            "lead",
        ),

        "spawn_teammate": lambda **kw: team.spawn(
            kw["name"],
            kw["role"],
            kw["prompt"],
        ),
        "list_teammates": lambda **kw: team.list_all(),
        "broadcast": lambda **kw: BUS.broadcast(
            "lead",
            kw["content"],
            team.member_names(),
        ),
        "send_message": lambda **kw: BUS.send(
            "lead",
            kw["to"],
            kw["content"],
            kw.get("msg_type", "message"),
        ),
        "read_inbox": lambda **kw: json.dumps(
            BUS.read_inbox("lead"),
            indent=2,
            ensure_ascii=False,
        ),
        "shutdown_request": lambda **kw: PROTOCOLS.handle_shutdown_request(
            kw["teammate"],
        ),
        "shutdown_status": lambda **kw: PROTOCOLS._check_shutdown_status(
            kw["request_id"],
        ),
        "plan_approval": lambda **kw: PROTOCOLS.handle_plan_review(
            kw["request_id"],
            kw["approve"],
            kw.get("feedback", ""),
        ),
    }



def make_teammate_handlers(name: str):
    return {
        **BASE_HANDLERS,
        **TASK_HANDLERS,
        **BACKGROUND_HANDLERS,
        **make_protocol_handlers(name),
        "claim_task": lambda **kw: TASKS.claim_task(
            kw["task_id"],
            name,
        ),

        "send_message": lambda **kw: BUS.send(
            name,
            kw["to"],
            kw["content"],
            kw.get("msg_type", "message"),
        ),
        "read_inbox": lambda **kw: json.dumps(
            BUS.read_inbox(name),
            indent=2,
            ensure_ascii=False,
        ),
    }

TEAMMATE_HANDLER = make_teammate_handlers("")


MEMORY = MemoryStore()

MEMORY_HANDLERS = {
    "memorize": lambda **kw: MEMORY.append(
        kw.get("section", "memory"),
        kw["content"],
    ),
    "recall_memory": lambda **kw: MEMORY.recall(kw.get("query")),
}

