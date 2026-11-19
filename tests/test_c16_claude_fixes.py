"""C1.6 Claude 接手时的修复里，C1.8 之后仍然有效的那部分（Claude 写）。

C1.6 收尾时 Claude 在卡的副本里写过 tests/test_flowfix_c16_extra_claude.py，但放进项目只收卡上登记过的测试文件，
那份没进来（2026-11-18 C1.8 施工时发现）。其中“整段退回时正文里的严重问题不丢”那 7 项测的代码已在 C1.8 删掉，
不再恢复；“证据写成一句话不出错”这部分在这里补回。
"""
import unittest


class EvidenceAsSentence(unittest.TestCase):

    def test_problem_text_accepts_string_evidence(self):
        from collab.dispatch import _problem_text
        text = _problem_text({"级别": "严重", "来源": "这次改的", "类型": "功能问题", "现象": "退回时报错",
                              "位置": "collab/dispatch.py:1", "证据": "看了一眼代码，确实会报错"})
        self.assertIn("退回时报错", text)
        self.assertIn("看了一眼代码，确实会报错", text)

    def test_problem_text_keeps_falsy_evidence(self):
        from collab.dispatch import _problem_text
        for value, shown in ((0, "0"), (False, "false"), ([], "[]")):
            text = _problem_text({"级别": "中等", "来源": "这次改的", "类型": "功能问题", "现象": "x",
                                  "位置": "a.py:1", "证据": value})
            self.assertIn(f"对照 {shown}", text)

    def test_problem_text_without_evidence(self):
        from collab.dispatch import _problem_text
        text = _problem_text({"级别": "中等", "来源": "这次改的", "类型": "功能问题", "现象": "没有证据"})
        self.assertIn("没有证据", text)


if __name__ == "__main__":
    unittest.main()
