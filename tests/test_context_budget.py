import unittest
from unittest.mock import patch

from runtime.context_budget import ContextBudgeter
from runtime.context_history import budget_active_turn


def _assistant_tool_call(call_id: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }],
    }


class ContextBudgetTests(unittest.TestCase):
    def test_budgeter_defaults_enabled_and_tuned(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CONTEXT_ENABLE_SECTION_BUDGET": "",
                "CONTEXT_MEMORY_BUDGET": "",
                "CONTEXT_RETRIEVED_HISTORY_BUDGET": "",
                "CONTEXT_SECURITY_KNOWLEDGE_BUDGET": "",
                "CONTEXT_TASK_RUNTIME_EVENTS_BUDGET": "",
                "CONTEXT_CONVERSATION_HISTORY_BUDGET": "",
            },
            clear=False,
        ):
            budgeter = ContextBudgeter.from_env()

        self.assertTrue(budgeter.enabled)
        self.assertEqual(2000, budgeter.rules["memory"].budget_chars)
        self.assertEqual(2500, budgeter.rules["retrieved_history"].budget_chars)
        self.assertEqual(3000, budgeter.rules["security_knowledge"].budget_chars)
        self.assertEqual(1500, budgeter.rules["task_runtime_events"].budget_chars)
        self.assertEqual(10000, budgeter.rules["conversation_history"].budget_chars)

    def test_active_turn_compresses_old_unpreserved_tool_results(self) -> None:
        rule = ContextBudgeter.from_env().rules["active_turn"]
        messages = [
            {"role": "user", "content": "current request"},
            _assistant_tool_call("call-1", "web_search"),
            {"role": "tool", "tool_call_id": "call-1", "content": "old search result" * 100},
            _assistant_tool_call("call-2", "read_file"),
            {"role": "tool", "tool_call_id": "call-2", "content": "important file result" * 100},
            _assistant_tool_call("call-3", "web_search"),
            {"role": "tool", "tool_call_id": "call-3", "content": "recent search result" * 100},
        ]

        with patch.dict(
            "os.environ",
            {
                "CONTEXT_ACTIVE_TURN_KEEP_RECENT_RESULTS": "1",
                "CONTEXT_ACTIVE_TURN_PRESERVE_TOOLS": "read_file",
            },
            clear=False,
        ):
            rule = ContextBudgeter.from_env().rules["active_turn"]
            budgeted = budget_active_turn(messages, enabled=True, rule=rule)

        contents = [message.get("content") for message in budgeted.rendered_messages]
        self.assertIn("<web_search result compressed for context budget>", contents)
        self.assertTrue(any("important file result" in str(item) for item in contents))
        self.assertTrue(any("recent search result" in str(item) for item in contents))
        self.assertEqual(1, budgeted.metadata["compressed_tool_results"])


if __name__ == "__main__":
    unittest.main()
