"""Configuration - The harness environment for the agent.

Per agent-builder skill: Configuration is part of the HARNESS, not the agent.
The model IS the agent. Config just sets up its world.
"""
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

# Proxy setup (harness environment, not agent logic)
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

# API client (the vehicle, not the driver)
client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

MODEL = "deepseek-v4-flash"
WORKDIR = Path.cwd()

# System prompt - keep it simple per the skill's philosophy:
# "The model already knows how to be an agent. Your job is to get out of the way."
SYSTEM = f"You are a coding agent at {os.getcwd()}. Use tools to solve tasks. Act, don't explain."
