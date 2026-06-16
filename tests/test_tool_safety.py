import unittest

from tools.executor import ToolExecutionRequest, ToolExecutor
from tools.hooks import ShellSafetyHook, ToolLoopGuardHook


def _ok(_name, _args):
    return "ok"


class ToolSafetyTests(unittest.TestCase):
    def test_tool_loop_guard_blocks_same_tool_argument_churn(self) -> None:
        executor = ToolExecutor([ToolLoopGuardHook(tool_repeat_limit=3)])

        outputs = []
        for index in range(3):
            result = executor.execute(
                ToolExecutionRequest(
                    call_id=f"call-{index}",
                    tool_name="web_search",
                    arguments={"q": f"query {index}"},
                    session_id="web:test",
                ),
                _ok,
            )
            outputs.append(result)

        self.assertEqual("success", outputs[0].status)
        self.assertEqual("success", outputs[1].status)
        self.assertEqual("denied", outputs[2].status)
        self.assertIn("same tool", outputs[2].output)

    def test_shell_safety_blocks_high_risk_commands(self) -> None:
        executor = ToolExecutor([ShellSafetyHook()])
        risky_commands = [
            "rm -rf .",
            "rm -rf /home/tale",
            "mkfs.ext4 /dev/sda",
            "dd if=image of=/dev/sda",
            "chmod 777 -R /",
        ]

        for index, command in enumerate(risky_commands):
            result = executor.execute(
                ToolExecutionRequest(
                    call_id=f"call-{index}",
                    tool_name="bash",
                    arguments={"command": command},
                    session_id="web:test",
                ),
                _ok,
            )
            self.assertEqual("denied", result.status, command)


if __name__ == "__main__":
    unittest.main()
