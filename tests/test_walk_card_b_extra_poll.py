"""卡 B 复查退回补充测试（对应退回第 1 条）：

验证行政对话（主对话、普通卡片、积木库管家）每轮查完消息后仍按原有零等待继续下一轮轮询，
不会在行政模式下被统一加上 50 毫秒额外等待。
"""
import subprocess
import unittest
from pathlib import Path

from acceptance_helpers import ROOT, needs_node


class ClerkPollZeroDelayReworkTest(unittest.TestCase):
    """复查退回第 1 条：行政对话正常轮询结束后不应多等 50ms。"""

    @needs_node()
    def test_clerk_poll_resumes_immediately_without_extra_50ms_delay(self):
        proc = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "walk_card_b_extra_poll_dom.js")],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
