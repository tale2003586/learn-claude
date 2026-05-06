"""Skills system - On-demand domain knowledge for the agent.

Per agent-builder skill:
  "Knowledge answers: What does the agent KNOW?"
  "Design principle: Make knowledge available, not mandatory. 
   Load it when relevant, not upfront."

This enables Level 4 (Skills) of progressive complexity:
  "Skills: On-demand knowledge | Domain expertise needed"

Skills are Markdown files loaded on-demand when the agent needs domain expertise.
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skill(name: str) -> str:
    """Load a skill's SKILL.md content.
    
    Skills are loaded on-demand, not upfront.
    This prevents context bloat.
    
    Args:
        name: Skill name (directory name under skills/)
    
    Returns:
        Skill content as text, or error message if not found.
    """
    skill_dir = SKILLS_DIR / name
    skill_file = skill_dir / "SKILL.md"

    if not skill_dir.exists():
        # Try to find it via fuzzy match
        for d in SKILLS_DIR.iterdir():
            if d.is_dir() and (name.lower() in d.name.lower()):
                skill_file = d / "SKILL.md"
                if skill_file.exists():
                    return skill_file.read_text()
        return f"Error: Skill '{name}' not found. Available: {list_skills()}"

    if not skill_file.exists():
        return f"Error: SKILL.md not found in {skill_dir}"

    return skill_file.read_text()


def list_skills() -> str:
    """List all available skills.
    
    Returns:
        Formatted list of skill names and descriptions.
    """
    available = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir():
            skill_file = d / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text()
                # Try to extract description from frontmatter
                desc = d.name
                for line in content.splitlines():
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"')
                        break
                available.append(f"  - {d.name}: {desc}")

    if not available:
        return "No skills found."

    return "Available skills:\n" + "\n".join(available)


# Skill tool definition for use in agent tools
SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load domain expertise for a specific topic. Use when you need specialized knowledge about a domain.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to load"
                },
            },
            "required": ["name"],
        },
    },
}
