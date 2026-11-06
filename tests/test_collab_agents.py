import tempfile
import unittest
from pathlib import Path

from collab.agents import (AgentResult, agy_command, ask, codex_command, cursor_command)


class CommandShapeTest(unittest.TestCase):
    def test_cursor_uses_agent_binary_with_trust_and_no_force(self):
        cmd = cursor_command("做事")
        self.assertEqual(cmd[0], "agent")
        self.assertIn("--trust", cmd)
        self.assertIn("auto", cmd)
        self.assertNotIn("--force", cmd)
        self.assertNotIn("--yolo", cmd)
        self.assertEqual(cmd[-1], "做事")

    def test_codex_uses_pinned_model_and_writable_sandbox(self):
        cmd = codex_command("做事")
        self.assertIn("gpt-6.1-sol", cmd)
        self.assertIn('model_reasoning_effort="xhigh"', cmd)
        self.assertIn("workspace-write", cmd)

    def test_agy_puts_prompt_right_after_dash_p(self):
        cmd = agy_command("做事")
        self.assertEqual(cmd[1], "-p")
        self.assertEqual(cmd[2], "做事")
        self.assertIn("gemini-4-argon-high", cmd)


class AskRetryTest(unittest.TestCase):
    def test_retries_when_expected_file_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def runner(cmd, cwd, timeout):
                calls.append(cmd)
                if len(calls) == 2:  # 第二次才真的落盘（模拟 Cursor 偶发不落盘）
                    (Path(cwd) / "out.txt").write_text("x")
                return 0, "DONE"

            result = ask("cursor", "写 out.txt", tmp, expect_file="out.txt", runner=runner)
            self.assertTrue(result.ok)
            self.assertEqual(result.attempts, 2)

    def test_gives_up_after_retries_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            def runner(cmd, cwd, timeout):
                return 0, "Creating the file now. DONE"  # 说了但没写

            result = ask("cursor", "写 out.txt", tmp, expect_file="out.txt", runner=runner)
            self.assertFalse(result.ok)
            self.assertEqual(result.attempts, 2)

    def test_nonzero_exit_is_failure_without_file_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ask("codex", "做事", tmp, runner=lambda c, d, t: (1, "err"))
            self.assertFalse(result.ok)
            self.assertEqual(result.attempts, 1)

    def test_unknown_adapter_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                ask("gemini-old", "做事", tmp, runner=lambda c, d, t: (0, ""))


if __name__ == "__main__":
    unittest.main()
