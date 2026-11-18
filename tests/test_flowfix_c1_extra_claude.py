"""CARD-003 第 4 轮复查后 Claude 接手补的测试（观察第 35 条）。

病根：程序靠猜复查员随手写的格式，猜漏一种就可能把这次改出来的问题当成“只剩原来就有的”放行。
兜底规矩：复查原文里出现的级别标签，比认出来的条目开头的级别标签多，就算“没认全”，整段退回，不自动放行。
宁可多退（正文引用了标签也算），不能漏放。治根见卡 C1.5（复查意见固定格式）。
注：放进项目时这个文件没被带上（只收写测试的登记过的测试文件），Claude 在主项目补回。
"""
import unittest

from collab.dispatch import _fix_items, _review_items, _triage, _unparsed_serious

OLD = "1. 【中等】【原来就有的】切换对话时卡在正在取回。"


class LevelTagCount(unittest.TestCase):
    def check_not_closed(self, text):
        items = _review_items(text)
        back, later = _triage(items)
        unparsed = _unparsed_serious(text, items)
        self.assertTrue(back or unparsed, f"不能按“只剩原来就有的”放行：{text!r}")
        self.assertTrue(any("数据丢失" in i for i in _fix_items(text)), "退回给写代码的要带上这条")

    def test_bold_issue_after_numbered_old(self):
        self.check_not_closed(f"结论：不通过\n{OLD}\n\n**【严重】【这次改的】保存后数据丢失。**")

    def test_heading_issue(self):
        self.check_not_closed(f"结论：不通过\n{OLD}\n\n### 【严重】【这次改的】保存后数据丢失")

    def test_quoted_issue(self):
        self.check_not_closed(f"结论：不通过\n{OLD}\n\n> 【中等】【这次改的】保存后数据丢失")

    def test_trailer_words_inside_issue(self):
        self.check_not_closed(f"结论：不通过\n【轻微】【这次改的】测试命令显示错了，保存后数据丢失\n{OLD}")

    def test_clean_old_only_still_closes(self):
        text = f"结论：不通过\n{OLD}\n\n指定测试全部通过，未修改文件。"
        items = _review_items(text)
        back, later = _triage(items)
        self.assertEqual(back, [])
        self.assertFalse(_unparsed_serious(text, items), "格式规整、只有原来就有的问题，照旧放行")


if __name__ == "__main__":
    unittest.main()
