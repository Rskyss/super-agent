"""C1.8 复查第 1 轮补的续跑测试（Claude 写）。"""
import unittest

from collab.flow import record, text_of
from test_flowfix_c15_acceptance import item, review
from test_flowfix_c16_extra_round54_recheck import checked
import test_flowfix_c15_extra_round54_returns as _returns

# 只借 ReviewReturns 的跑卡和查记录的办法，不重复跑它自己的测试
class _Harness(_returns.ReviewReturns):
    pass


for _name in [n for n in dir(_returns.ReviewReturns) if n.startswith("test_")]:
    setattr(_Harness, _name, None)
del _name

RAW = "结论：不通过\n保存后草稿丢失，位置 app/save.py:12。另外按钮颜色不对。"


class ResumeAfterUnreadable(_Harness):
    def test_resume_reviews_again_instead_of_sending_words_to_developer(self):
        """看不懂停下后点“接着跑”：重新复查，不把原话当退回意见派给写代码的（严重 1）。"""
        card = self.run_flow([RAW, RAW])
        self.assertEqual(card.state, "需Claude介入")
        devs_before = len(self.prompts("开发员"))
        card = self.run_flow([review("通过")], resume=True)
        self.assertEqual(len(self.prompts("开发员")), devs_before, "原话不能交给写代码的")
        self.assertNotIn(RAW, "\n".join(self.prompts("开发员")))
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.prompts("复查员")), 3, "接着跑重新复查一次")

    def test_stop_words_list_what_the_review_said(self):
        """看不懂停下时，给人看的说明里列出原话里的问题（只给人看）。"""
        self.run_flow([RAW, RAW])
        stop = self.events("转人工")[-1]
        self.assertIn("看不懂", stop.get("say") or "")
        self.assertIn("保存后草稿丢失", text_of(self.cdir, stop) or "", "完整原话存着（说明里不挑）")


class ResumeOldWholeTextRecord(_Harness):
    def test_old_whole_text_return_stops_on_resume(self):
        """老卡留下的“整段原话退回”结论（退回、没有清单、有原文），写代码的还没交活：续跑停下，不按原话派活。"""
        sent = record(self.cdir, "派活", src="调度员", dst="开发员", tool="qwen", step="开发", round=1, summary="写")
        record(self.cdir, "回复", src="开发员", dst="调度员", tool="qwen", step="开发", round=1, ok=True,
               reply_to=sent["seq"], summary="汇报：写了")
        task = record(self.cdir, "派活", src="调度员", dst="复查员", tool="codex", step="复查", round=1, summary="复查",
                      text=RAW)
        reply = record(self.cdir, "回复", src="复查员", dst="调度员", tool="codex", step="复查", round=1, ok=True,
                       reply_to=task["seq"], summary="结论：不通过", text=RAW)
        record(self.cdir, "复查结论", src="程序", result="退回", reply=reply["seq"], later=0,
               summary="复查退回 1 件", text=RAW)
        record(self.cdir, "退回", src="程序", dst="开发员", summary="复查不通过，自动退回写代码的（第 1 次，最多 2 次）")
        from collab.state import Card
        card = Card(self.cdir.name, state="开发中")
        card.save(self.cdir / "状态.json")
        card = self.run_flow([], resume=True)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.prompts("开发员")), 1, "不再按整段原话派活")


class ResumeRound2(_Harness):
    """C1.8 复查第 2 轮补的测试。"""

    def test_stop_words_keep_severe_sentence_on_the_same_line(self):
        """同一行前半句是小问题、后半句标了严重：停下说明里要看得到严重的那句（中等 4）。"""
        raw = "结论：不通过\n导出按钮颜色偏浅。【严重】取消导出会删除原文件。"
        self.run_flow([raw, raw])
        stop = self.events("转人工")[-1]
        self.assertIn("写到了“严重”", stop.get("say") or "")
        self.assertIn("取消导出会删除原文件", text_of(self.cdir, stop) or "")


class ResumeRound3(_Harness):
    """C1.8 复查第 3 轮补的测试（修补未经独立复查，见台账）。"""

    def test_severe_sentence_with_semicolon_kept_whole(self):
        raw = "结论：不通过\n【严重】先点取消；再次打开项目时，所有原文件都被删除。"
        self.run_flow([raw, raw])
        stop = self.events("转人工")[-1]
        self.assertIn("写到了“严重”", stop.get("say") or "")
        self.assertIn("所有原文件都被删除", text_of(self.cdir, stop) or "")

class StopWordsPrevSevere(_Harness):
    """C1.8a 复查第 3 轮轻微：已正式确认修好的严重问题，停下说明不能再说成“还没确认修好”。"""

    def test_fixed_severe_issue_is_not_listed_again(self):
        severe = item(level="严重", what="关闭应用会丢草稿")
        self.run_flow([review("不通过", [severe]), review("通过")])
        record(self.cdir, "转人工", src="程序", dst="调度员", summary="全部测试超时")
        from collab.state import Card
        Card(self.cdir.name, state="开发中").save(self.cdir / "状态.json")
        card = self.run_flow([RAW, RAW], resume=True)
        self.assertEqual(card.state, "需Claude介入")
        self.assertNotIn("关闭应用会丢草稿", self.events("转人工")[-1].get("say") or "")

    def test_unfixed_severe_issue_is_listed(self):
        severe = item(level="严重", what="关闭应用会丢草稿")
        self.run_flow([review("不通过", [severe]), RAW, RAW])
        self.assertIn("关闭应用会丢草稿", self.events("转人工")[-1].get("say") or "")


if __name__ == "__main__":
    unittest.main()
