import json
import os
from pathlib import Path
import subprocess
from xml.sax import handler

from openai import OpenAI

try:
    import readline
    # #143 UTF-8 backspace fix for macOS libedit
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
    readline.parse_and_bind('set enable-meta-keybindings on')
except ImportError:
    pass

from dotenv import load_dotenv

load_dotenv(override=True)

os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

MODEL = "deepseek-chat"

WORKDIR = Path.cwd()
#MODEL = os.getenv("MODEL_ID", "deepseek-chat")

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use tools to solve tasks. Act, don't explain."

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":       lambda **kw: TODO.update(kw["items"]),
}


CHILD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update task list. Track progress on multi-step tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["id", "text", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
        },    
    }
]

class TodoManager:
    def update(self, items: list) -> str:
        if len(items) >20:
            return "Error: Too many items (max 20)"
        
        validated = []
        in_progress_count = 0

        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))

            if not text:
                return f"Error: Item {item_id} text required"
            if status not in ("pending", "in_progress", "completed"):
                return f"Error: Item {item_id} invalid status '{status}'"
            if status == "in_progress":
                in_progress_count += 1
            
            validated.append({"id": item_id, "text": text, "status": status})
        if in_progress_count > 1:
            return "Error: Only one task can be in_progress at a time"
        self.items = validated
        return self.render()
            
    def render(self) -> str:
        if not self.items:
            return "No tasks."
        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)
    
TODO = TodoManager()

PARENT_TOOLS = CHILD_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "description": {"type": "string", "description": "Short description of the task"},
                },
                "required": ["prompt"],
            },
        },
    }
]

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
    
def run_subagent(task: str) -> str:
    sub_messages = [
        {"role": "system", "content": f"You are a helpful coding assistant. {SYSTEM}"},
        {"role": "user", "content": task},
    ]
    for _ in range(30):
        response = client.chat.completions.create(
            model=MODEL,
            messages=sub_messages,
            tools=CHILD_TOOLS,
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
                print(f"Error occurred while processing {call.function.name}: {e}")
                output = f"Error: {e}"
            sub_messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })
    response_content = sub_messages[-1]["content"]
    if isinstance(response_content, list):
        return "\n".join(block.text for block in response_content if hasattr(block, "text"))
    return str(response_content)

def agent_loop(messages: list):

    rounds_since_todo = 0

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                *messages,
            ],
            tools=PARENT_TOOLS,
            tool_choice="auto",
            max_tokens=8000,
        )

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return
        
        used_todo = False

        for call in message.tool_calls:
            if call.function.name == "task":
                try:
                    args = json.loads(call.function.arguments)
                    print(f"> Spawning subagent for task: {call.function.arguments}")
                    desc = args.get("description", "subtask")
                    prompt = args["prompt"]
                    print(f"> task ({desc}): {prompt[:80]}")
                    output = run_subagent(prompt)
                except Exception as e:
                    print(f"Error occurred while spawning subagent: {e}")
                    output = f"Error: {e}"

            else:
                try:
                    args = json.loads(call.function.arguments)
                    handler = TOOL_HANDLERS.get(call.function.name)
                    output = handler(**args) if handler else f"Unknown tool: {call.function.name}"
                except Exception as e:
                    print(f"Error occurred while processing {call.function.name}: {e}")
                    output = f"Error: {e}"
                print(f"> {call.function.name}:")
                print(output[:200])

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })

            if call.function.name == "todo":
                used_todo = True
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 5:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })


    

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not query.strip():
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)

        response_content = history[-1].get("content")
        if response_content:
            print(response_content)

        print("---")

    