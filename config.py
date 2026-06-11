import os
from pathlib import Path
from dotenv import load_dotenv

from models.model_pool import build_model_pool_from_env
from skill_runtime import SKILL_LOADER

load_dotenv(override=True)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()

USE_LOCAL_PROXY = os.getenv("USE_LOCAL_PROXY", "1").lower() not in {"0", "false", "no"}
if USE_LOCAL_PROXY:
    proxy_url = os.getenv("LOCAL_PROXY_URL", "http://127.0.0.1:7897")
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["HTTP_PROXY"] = proxy_url
else:
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("http_proxy", None)
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)


MODEL_POOL = build_model_pool_from_env()
MODEL_HEALTHCHECK_ON_STARTUP = os.getenv(
    "LLM_HEALTHCHECK_ON_STARTUP",
    "0",
).lower() in {"1", "true", "yes"}
MODEL_HEALTHCHECK_PURPOSES = [
    item.strip()
    for item in os.getenv(
        "LLM_HEALTHCHECK_PURPOSES",
        "chat,coding,summary,hybrid",
    ).split(",")
    if item.strip()
]
MODEL_HEALTHCHECK_RESULTS = (
    MODEL_POOL.health_check_purposes(MODEL_HEALTHCHECK_PURPOSES)
    if MODEL_HEALTHCHECK_ON_STARTUP
    else []
)
MODEL = MODEL_POOL.model_for("chat")
MAX_TOKENS_PARAM = MODEL_POOL.profile_for("chat").max_tokens_param
client = MODEL_POOL.client_for_purpose("chat")
REFLECTION_ENABLED = os.getenv("REFLECTION_ENABLED", "0").lower() in {"1", "true", "yes"}
REFLECTION_MAX_TOKENS = int(os.getenv("REFLECTION_MAX_TOKENS", "500"))
REFLECTION_MIN_REASONING_STEPS = int(os.getenv("REFLECTION_MIN_REASONING_STEPS", "6"))
WORKDIR = Path.cwd() 
WORKSPACE_ROOTS = [
    Path(item).expanduser().resolve()
    for item in os.getenv("WORKSPACE_ROOTS", str(WORKDIR)).split(os.pathsep)
    if item.strip()
]
DEFAULT_CODING_WORKSPACE = Path(
    os.getenv("DEFAULT_CODING_WORKSPACE", str(WORKDIR))
).expanduser().resolve()

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
