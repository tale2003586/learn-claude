import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.context import ContextBuilder
from sessions.session import Session


class MemoryStore:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_all(self) -> str:
        return self.text


class ContextInstructionTests(unittest.TestCase):
    def test_bot_profile_loads_assistant_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            (root / ".agent" / "assistant.md").write_text(
                "assistant rules",
                encoding="utf-8",
            )
            (root / ".agent" / "coding.md").write_text(
                "coding rules",
                encoding="utf-8",
            )
            context = ContextBuilder(instruction_root=root).build(
                session=Session(id="web:test"),
                profile=SimpleNamespace(
                    system_prompt="base",
                    tool_mode="bot",
                ),
            )

            system = context.messages[0]["content"]
            self.assertIn("assistant rules", system)
            self.assertNotIn("coding rules", system)
            report = context.report.to_dict()
            self.assertIn("mode_instructions", report["sections"])
            self.assertNotIn("project_instructions", report["sections"])
            self.assertEqual(
                [".agent/assistant.md"],
                report["sections"]["mode_instructions"]["metadata"]["sources"],
            )
            self.assertGreater(report["total_chars"], 0)

    def test_coding_profile_loads_coding_and_project_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            (root / ".agent" / "assistant.md").write_text(
                "assistant rules",
                encoding="utf-8",
            )
            (root / ".agent" / "coding.md").write_text(
                "coding rules",
                encoding="utf-8",
            )
            (root / "AGENTS.md").write_text(
                "project rules",
                encoding="utf-8",
            )
            context = ContextBuilder(instruction_root=root).build(
                session=Session(id="task:test"),
                profile=SimpleNamespace(
                    system_prompt="base",
                    tool_mode="coding",
                ),
            )

            system = context.messages[0]["content"]
            self.assertIn("coding rules", system)
            self.assertIn("project rules", system)
            self.assertNotIn("assistant rules", system)
            report = context.report.to_dict()
            self.assertIn("mode_instructions", report["sections"])
            self.assertIn("project_instructions", report["sections"])
            self.assertEqual(
                [".agent/coding.md"],
                report["sections"]["mode_instructions"]["metadata"]["sources"],
            )
            self.assertEqual(
                ["AGENTS.md"],
                report["sections"]["project_instructions"]["metadata"]["sources"],
            )

    def test_instruction_report_marks_truncated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            (root / ".agent" / "assistant.md").write_text(
                "x" * 1200,
                encoding="utf-8",
            )
            context = ContextBuilder(
                instruction_root=root,
                instruction_limit=50,
            ).build(
                session=Session(id="web:test"),
                profile=SimpleNamespace(
                    system_prompt="base",
                    tool_mode="bot",
                ),
            )

            section = context.report.to_dict()["sections"]["mode_instructions"]
            self.assertTrue(section["truncated"])
            self.assertEqual(1200, section["raw_chars"])
            self.assertGreater(section["rendered_chars"], 1000)

    def test_report_splits_current_request_from_history(self) -> None:
        session = Session(id="web:test")
        session.add_message("user", "old question")
        session.add_message("assistant", "old answer")
        session.add_message("user", "new question")

        context = ContextBuilder().build(
            session=session,
            profile=SimpleNamespace(
                system_prompt="base",
                tool_mode="bot",
            ),
        )

        sections = context.report.to_dict()["sections"]
        self.assertEqual(len("new question"), sections["current_request"]["raw_chars"])
        self.assertEqual(len("new question"), sections["current_request"]["rendered_chars"])
        self.assertTrue(sections["current_request"]["metadata"]["preserve"])
        self.assertEqual(2, sections["conversation_history"]["metadata"]["message_count"])

    def test_section_budget_trims_optional_sections_and_preserves_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            (root / ".agent" / "coding.md").write_text(
                "coding-rule-" * 80,
                encoding="utf-8",
            )
            (root / "AGENTS.md").write_text(
                "project-rule-" * 80,
                encoding="utf-8",
            )
            session = Session(id="task:test")
            session.add_message("user", "please keep this exact current request")

            with patch.dict(
                "os.environ",
                {
                    "CONTEXT_ENABLE_SECTION_BUDGET": "1",
                    "CONTEXT_BUDGET_CHARS": "1000",
                    "CONTEXT_MODE_INSTRUCTIONS_BUDGET": "80",
                    "CONTEXT_PROJECT_INSTRUCTIONS_BUDGET": "80",
                    "CONTEXT_MEMORY_BUDGET": "90",
                    "CONTEXT_TASK_RUNTIME_EVENTS_BUDGET": "90",
                    "CONTEXT_MODE_INSTRUCTIONS_FLOOR": "40",
                    "CONTEXT_PROJECT_INSTRUCTIONS_FLOOR": "40",
                    "CONTEXT_MEMORY_FLOOR": "40",
                    "CONTEXT_TASK_RUNTIME_EVENTS_FLOOR": "40",
                },
                clear=False,
            ):
                context = ContextBuilder(
                    memory_store=MemoryStore("memory-item-" * 80),
                    instruction_root=root,
                ).build(
                    session=session,
                    profile=SimpleNamespace(
                        system_prompt="base",
                        tool_mode="coding",
                    ),
                    inbox=[{"from": "alice", "body": "inbox-item-" * 80}],
                    background_results=[
                        {
                            "task_id": "bg1",
                            "status": "done",
                            "result": "background-result-" * 80,
                        }
                    ],
                )

            report = context.report.to_dict()
            sections = report["sections"]
            self.assertEqual("please keep this exact current request", context.messages[1]["content"])
            self.assertEqual(
                len("please keep this exact current request"),
                sections["current_request"]["rendered_chars"],
            )
            self.assertTrue(sections["mode_instructions"]["truncated"])
            self.assertTrue(sections["project_instructions"]["truncated"])
            self.assertTrue(sections["memory"]["truncated"])
            self.assertTrue(sections["task_runtime_events"]["truncated"])
            self.assertLessEqual(sections["mode_instructions"]["rendered_chars"], 80)
            self.assertLessEqual(sections["project_instructions"]["rendered_chars"], 80)
            self.assertLessEqual(sections["memory"]["rendered_chars"], 90)
            self.assertLessEqual(sections["task_runtime_events"]["rendered_chars"], 90)
            self.assertEqual(
                {
                    "memory",
                    "mode_instructions",
                    "project_instructions",
                    "task_runtime_events",
                },
                {item["section"] for item in report["reductions"]},
            )
            self.assertIn("...[truncated]", context.messages[0]["content"])
            self.assertIn("...[truncated]", context.messages[-1]["content"])
            self.assertTrue(report["metadata"]["section_budget_enabled"])


if __name__ == "__main__":
    unittest.main()
