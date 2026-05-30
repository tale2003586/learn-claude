import tempfile
import unittest
from pathlib import Path

from core.provider import LLMResponse
from memory.archive_store import ArchivedRecentTurn, MemoryArchiveStore
from memory.history_summary import HistorySummarizer
from memory.lifecycle import MemoryLifecycle
from memory.store import MemoryStore
from sessions.session import Session


class RecordingSummaryProvider:
    def __init__(self, summary: str = "compact assistant summary") -> None:
        self.summary = summary
        self.calls = []

    def chat(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self.summary)


class MemoryLifecycleArchiveTests(unittest.TestCase):
    def test_history_recent_window_and_sqlite_archive_share_one_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = MemoryStore(root / "memory")
            archive = MemoryArchiveStore(root / ".sessions" / "sessions.db")
            provider = RecordingSummaryProvider()
            lifecycle = MemoryLifecycle(
                memory,
                summarizer=HistorySummarizer(
                    provider=provider,
                    model="test-model",
                    direct_limit=5,
                ),
                archive_store=archive,
                recent_limit=2,
            )
            session = Session(id="web:default")

            for number in range(1, 4):
                session.add_message("user", f"user original text {number}")
                session.add_message("assistant", f"long assistant reply {number}")
                lifecycle.after_turn(session)

            history = memory.read_history()
            recent_turns = memory.read_recent_turns()
            archived_turns = archive.list_recent()

            self.assertIn("USER:\nuser original text 1", history)
            self.assertIn("ASSISTANT_SUMMARY:\ncompact assistant summary", history)
            self.assertEqual(3, len(provider.calls))
            self.assertEqual(2, len(recent_turns))
            self.assertEqual("user original text 2", recent_turns[0]["user_text"])
            self.assertEqual("compact assistant summary", recent_turns[0]["assistant_summary"])
            self.assertEqual(1, len(archived_turns))
            self.assertEqual("user original text 1", archived_turns[0]["user_text"])
            self.assertEqual("compact assistant summary", archived_turns[0]["assistant_summary"])
            self.assertIn("## Turn 2", memory.read_recent_context())

    def test_short_assistant_reply_skips_llm_summary_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MemoryStore(Path(tmp) / "memory")
            provider = RecordingSummaryProvider()
            lifecycle = MemoryLifecycle(
                memory,
                summarizer=HistorySummarizer(
                    provider=provider,
                    model="test-model",
                    direct_limit=20,
                ),
            )
            session = Session(id="web:default")
            session.add_message("user", "keep my full prompt")
            session.add_message("assistant", "done")

            lifecycle.after_turn(session)

            self.assertEqual([], provider.calls)
            self.assertIn("ASSISTANT_SUMMARY:\ndone", memory.read_history())

    def test_recent_markdown_bounds_context_but_json_keeps_full_user_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MemoryStore(Path(tmp) / "memory")
            lifecycle = MemoryLifecycle(memory)
            session = Session(id="web:default")
            user_text = "x" * 1300
            session.add_message("user", user_text)
            session.add_message("assistant", "done")

            lifecycle.after_turn(session)

            self.assertIn(user_text, memory.read_history())
            self.assertEqual(user_text, memory.read_recent_turns()[0]["user_text"])
            self.assertIn("... (100 chars omitted)", memory.read_recent_context())

    def test_archive_ignores_duplicate_source_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = MemoryArchiveStore(Path(tmp) / "sessions.db")
            turn = ArchivedRecentTurn(
                session_id="web:default",
                mode="hybrid",
                user_text="user",
                assistant_summary="summary",
                source_ref="web:default:1",
                created_at="2026-05-30T00:00:00+00:00",
            )

            self.assertTrue(archive.append(turn))
            self.assertFalse(archive.append(turn))
            self.assertEqual(1, len(archive.list_recent()))


if __name__ == "__main__":
    unittest.main()
