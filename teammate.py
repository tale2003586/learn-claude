import json
from pathlib import Path
import threading

from mytry.agent import TEAMMATE_TOOLS
from mytry.config import MODEL, WORKDIR, client
from mytry.message_bus import BUS
from mytry.tools import make_teammate_handlers


def execute_tool_call(call,handlers) -> str:
    """Execute a single tool call and return its output."""
    try:
        args = json.loads(call.function.arguments)
    except Exception as e:
        return f"Error parsing arguments for {call.function.name}: {e}"

    handler = handlers.get(call.function.name)
    if not handler:
        return f"Unknown tool: {call.function.name}"

    try:
        return handler(**args)
    except Exception as e:
        return f"Error: {e}"


class TeammateManager:
    def __init__(self, team_dir: Path):
        self.dir = team_dir
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}
        self._clear_stale_working_members()

    def _load_config(self):
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find_member(self, name: str) -> dict:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def _clear_stale_working_members(self):
        changed = False
        for member in self.config["members"]:
            if member.get("status") == "working":
                member["status"] = "idle"
                changed = True
        if changed:
            self._save_config()
    
    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find_member(name)
        if member:
            if member["status"] not in {"idle", "shutdown"}:
                return f"Error: Member '{name}' already active"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save_config()
        thread = threading.Thread(target=self._run_member, 
                                  args=(name, role, prompt), daemon=True)
        self.threads[name] = thread
        thread.start()
        return f"Teammate '{name}' spawned with role '{role}'"
    
    def _run_member(self, name: str, role: str, prompt: str):
        sys_prompt = (
            f"You are '{name}', role: {role}, at {WORKDIR}. "
            f"Use send_message to communicate. Complete your task."
        )
        messages = [{"role": "user", "content": prompt}]
        handlers = make_teammate_handlers(name)
        tools = TEAMMATE_TOOLS
        should_shutdown = False
        try:
            for _ in range(50):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    messages.append({"role": "user", "content": json.dumps(msg)})
                try:
                    response = client.chat.completions.create(
                        model=MODEL,
                        messages=[{"role": "system", "content": sys_prompt}, *messages],
                        tools=tools,
                        tool_choice="auto",
                        max_tokens=8000,
                    )
                except Exception as e:
                    print(f"[{name}] Error: {e}")
                    break
                
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))

                if not message.tool_calls:
                    break

                for call in message.tool_calls:
                    try:
                        output = execute_tool_call(call, handlers)
                    except Exception as e:
                        output = f"Error: {e}"
                    if call.function.name == "shutdown_response" and output.startswith("Approved"):
                        should_shutdown = True
                    print(f"[{name}] {call.function.name}: {str(output)[:120]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": output,
                    })
                if should_shutdown:
                    break
        finally:
            member = self._find_member(name)
            if member and member["status"] != "shutdown":
                member["status"] = "idle"
                self._save_config()

    def list_all(self) -> str:
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)
    
    def member_names(self) -> list:
        return [m["name"] for m in self.config["members"]]


TEAM = TeammateManager(WORKDIR / ".team")
