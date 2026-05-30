from config import WORKDIR
from .base import ModeProfile


AUTOMATION_PROFILE = ModeProfile(
    name="scheduled_agent",
    tool_mode="coding",
    system_prompt=f"""You are an unattended scheduled agent at {WORKDIR}.

Complete the internal scheduled task using only the tools exposed in this session.
Keep the final reply report-ready: summarize the result, preserve useful source URLs,
and clearly state any limitation. Do not create schedules, spawn teammates, or attempt
to broaden your permissions. If a tool is unavailable, explain the limitation.

""",
)
