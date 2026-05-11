import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

from skill_runtime import SKILL_LOADER

load_dotenv(override=True)

os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

MODEL = "deepseek-v4-flash"
WORKDIR = Path.cwd() 

SKILLS_DIR = WORKDIR / "skills"

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
    "plan_approval_request",
}

SYSTEM = f"""You are a team lead at {WORKDIR}.Spawn teammates and communicate via inboxes.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.

Skills available:
{SKILL_LOADER.get_descriptions()}"""

KEEP_RECENT = 3

PRESERVE_RESULT_TOOLS = {"read_file"}

TRANSCRIPT_DIR = WORKDIR / ".transcripts"

THRESHOLD = 50000

TASKS_DIR = WORKDIR / ".tasks"
