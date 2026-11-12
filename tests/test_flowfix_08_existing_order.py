"""流程修理第 11 条（试跑记录 ⑦）：施工单已经写好的需求，调度员写方案时只摘范围和测试命令，不重新设计。"""
import tempfile
import unittest
from pathlib import Path

from collab.planner import _build_prompt, existing_orders

NEED = "# 施工单 68b 统一叫法\n\n## 要做什么\n按 docs/积木库/施工单/68b-统一叫法.md 改用户看得见的字。\n"


class ExistingOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        order = self.project / "docs" / "积木库" / "施工单" / "68b-统一叫法.md"
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_order_that_exists(self):
        self.assertEqual(existing_orders(self.project, NEED), ["docs/积木库/施工单/68b-统一叫法.md"])

    def test_ignores_missing_order(self):
        self.assertEqual(existing_orders(self.project, "按 docs/施工单/不存在.md 做"), [])

    def test_prompt_says_extract_only(self):
        prompt = _build_prompt(self.project, NEED, False, project=self.project)
        self.assertIn("已经有写好的施工单：docs/积木库/施工单/68b-统一叫法.md", prompt)
        self.assertIn("不要重新设计", prompt)
        self.assertIn("allowed 照施工单", prompt)

    def test_plain_card_unchanged(self):
        prompt = _build_prompt(self.project, "# 改按钮\n## 要做什么\n改颜色\n", False, project=self.project)
        self.assertNotIn("施工单", prompt)


if __name__ == "__main__":
    unittest.main()
