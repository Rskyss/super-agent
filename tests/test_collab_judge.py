import sys
import unittest

from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, judge


class JudgeTest(unittest.TestCase):
    def test_exit_zero_is_pass(self):
        v = judge([sys.executable, "-c", "print('ok')"], cwd=".", timeout=30)
        self.assertEqual(v.result, PASS)
        self.assertIn("ok", v.output)

    def test_nonzero_exit_is_fail_and_keeps_output(self):
        v = judge([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
                  cwd=".", timeout=30)
        self.assertEqual(v.result, FAIL)
        self.assertEqual(v.returncode, 3)
        self.assertIn("boom", v.output)

    def test_missing_command_is_env_error_not_fail(self):
        v = judge(["definitely-not-a-real-command-xyz"], cwd=".", timeout=30)
        self.assertEqual(v.result, ENV_ERROR)

    def test_slow_command_is_timeout(self):
        v = judge([sys.executable, "-c", "import time; time.sleep(5)"], cwd=".", timeout=1)
        self.assertEqual(v.result, TIMEOUT)

    def test_output_is_truncated_to_tail(self):
        code = "print('x' * 20000)"
        v = judge([sys.executable, "-c", code], cwd=".", timeout=30)
        self.assertLessEqual(len(v.output), 4000)


if __name__ == "__main__":
    unittest.main()
