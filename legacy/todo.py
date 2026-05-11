"""Todo manager - Task tracking for the agent.

Per agent-builder skill: "Constraints enable."
- Only ONE task can be in_progress at a time (forces sequential focus)
- Max 20 items (prevents context bloat)
"""


class TodoManager:
    """Simple task list manager with constraint enforcement."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """Update the task list. Only one task can be in_progress."""
        if len(items) > 20:
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
        """Render the task list as formatted text."""
        if not self.items:
            return "No tasks."

        lines = []
        for item in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")

        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()
