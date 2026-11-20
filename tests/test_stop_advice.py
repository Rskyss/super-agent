"""卡停下时那句建议要说对“为什么停、该谁动”（docs/右栏显示小修/方案.md 口径 2c，CARD-019 复盘第 5 条）。

CARD-019 退回用完停下时，剩下 4 件里有 2 件严重（其中 1 件丢数据）、2 件测试写错，程序却先说
“复查说测试本身写错了……建议点‘不满意，继续改’让写测试的改测试”，严重问题被盖住，用户差点照着点。
口径：有严重或丢数据的，先说这个，建议交给 Claude 接手或讲清楚再跑一轮；测试写错只附带一提。
只有测试写错、没有严重的，照旧建议让写测试的改测试（㊿）。用 CARD-019 真实的停下记录测。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.dispatch import _stop_reason_list

FLOW = Path(__file__).resolve().parent / "fixtures" / "card019" / "流转.jsonl"


def card019_stop():
    for line in FLOW.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("kind") == "转人工":
            return e["清单"], e["退回问题"]
    raise AssertionError("夹具里没有停下记录")


class SevereComesFirst(unittest.TestCase):
    def setUp(self):
        self.data, self.back = card019_stop()
        self.reason = _stop_reason_list(self.data, None, back_problems=self.back)

    def test_does_not_lead_with_test_problems(self):
        self.assertFalse(self.reason.startswith("判断：复查说测试本身写错了"), self.reason)
        self.assertIn("严重", self.reason.split("。")[0], "第一句先说严重问题：" + self.reason)

    def test_says_who_should_act(self):
        self.assertIn("Claude 接手", self.reason)
        self.assertIn("不满意，继续改", self.reason, "也可以自己讲清楚再跑一轮")

    def test_mentions_test_problems_as_a_side_note(self):
        self.assertIn("其中 2 件是测试写错", self.reason)

    def test_data_loss_counts_like_severe(self):
        only_loss = [dict(p, 级别="中等") for p in self.back if p.get("类型") == "安全或丢数据"]
        reason = _stop_reason_list(self.data, None, back_problems=only_loss)
        self.assertIn("Claude 接手", reason)
        self.assertIn("丢数据", reason)


class OtherHintsKept(unittest.TestCase):
    """Codex 复查（11-20）：先说严重问题时，原来另外两条具体提示不能丢——同一问题反复出现（换模型）、要改范围外的文件。"""

    def setUp(self):
        from collab.flow import record
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cdir = Path(tmp.name)
        self.record = record
        self.data, self.back = card019_stop()   # 有严重、有丢数据、还有 2 件测试写错

    def replies(self, *bodies):
        for body in bodies:
            self.record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过", text=body)

    def test_repeated_problem_hint_survives(self):
        body = "结论：不通过\n1. 【严重】切回后草稿没了。位置：[main.swift:848](/x/main.swift:848)"
        self.replies(body, body, body)
        code_only = [p for p in self.back if p.get("类型") != "测试写错"]
        reason = _stop_reason_list(self.data, self.cdir, back_problems=code_only)
        self.assertTrue(reason.startswith("判断：自动退回用完了"), reason)
        self.assertIn("同一个问题反复出现", reason)
        self.assertIn("换写代码的工具或模型", reason)

    def test_with_test_problems_no_model_blame_but_scope_hint_kept(self):
        # Codex 复查第 2 轮：有测试写错时说不清反复出现的是测试还是代码，不怪写代码的模型（和原来一样）；范围外照样提示
        body = ("结论：不通过\n1. 【测试的问题】【中等】测试只查调用。位置：[test_a.py:30](/x/test_a.py:30)\n"
                "2. 【严重】草稿没了。位置：[main.swift:848](/x/main.swift:848)")
        self.replies(body, body)
        self.record(self.cdir, "范围", src="程序", dst="开发员", summary="写代码的改了范围外的文件：a.py，已撤销")
        reason = _stop_reason_list(self.data, self.cdir, back_problems=self.back)
        self.assertTrue(reason.startswith("判断：自动退回用完了"), reason)
        self.assertNotIn("换写代码的工具或模型", reason)
        self.assertIn("写明要多改哪个文件", reason)

    def test_out_of_scope_hint_survives(self):
        self.replies("结论：不通过\n1. 【严重】甲。", "结论：不通过\n1. 【严重】乙。")
        self.record(self.cdir, "范围", src="程序", dst="开发员", summary="写代码的改了范围外的文件：a.py，已撤销")
        loss = [dict(p, 级别="中等") for p in self.back if p.get("类型") == "安全或丢数据"]
        reason = _stop_reason_list(self.data, self.cdir, back_problems=loss)
        self.assertIn("Claude 接手", reason)
        self.assertIn("写明要多改哪个文件", reason)


    def test_test_problems_in_returned_list_count_even_if_latest_text_omits_them(self):
        # Codex 复查第 3 轮：最新复查原文只重列反复出现的严重问题，测试写错是从上轮核对继承进退回清单的，也算
        body = "结论：不通过\n1. 【严重】草稿没了。位置：[main.swift:848](/x/main.swift:848)"
        self.replies(body, body, body)
        reason = _stop_reason_list(self.data, self.cdir, back_problems=self.back)   # 清单里有 2 件测试写错
        self.assertIn("其中 2 件是测试写错", reason)
        self.assertNotIn("换写代码的工具或模型", reason)


class TestProblemsOnlyUnchanged(unittest.TestCase):
    def test_only_test_problems_still_asks_to_fix_tests(self):
        data, back = card019_stop()
        tests_only = [p for p in back if p.get("类型") == "测试写错"]
        reason = _stop_reason_list(data, None, back_problems=tests_only)
        self.assertIn("测试本身写错了", reason)
        self.assertIn("写测试的", reason)

    def test_minor_only_unchanged(self):
        data, back = card019_stop()
        minor = [dict(p, 级别="轻微", 类型="功能问题") for p in back]
        self.assertIn("剩下的都是小问题", _stop_reason_list(data, None, back_problems=minor))


if __name__ == "__main__":
    unittest.main()
