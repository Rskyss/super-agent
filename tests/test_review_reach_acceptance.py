"""复查新规矩（方案-第二批第七节，用户 2026-11-19 批准）：退回的问题必须在本项目碰得上。

只能推演、又标“少见”“极巧合”的不退回（记以后再修）；“测试写错”“安全或丢数据”照退；
实跑复现的、推演但“常见”的照旧按级别处理。
"""
import json
import unittest
from pathlib import Path

from collab import dispatch
from collab.dispatch import _needs_return, read_review_list

ROOT = Path(__file__).resolve().parents[1]


def item(level="严重", source="这次改的", kind="功能问题", hit="少见", how="推演"):
    proof = ({"方式": "实跑", "命令": "python3 -m unittest x", "输出": "FAILED"} if how == "实跑"
             else {"方式": "推演", "对照": "collab/dispatch.py:10"})
    return {"级别": level, "来源": source, "类型": kind, "碰上": hit,
            "现象": "点接着跑又派了一遍写测试的", "位置": "collab/dispatch.py:10", "证据": proof}


class Reachable(unittest.TestCase):
    def test_guessed_and_unreachable_is_not_returned_even_if_severe(self):
        self.assertFalse(_needs_return(item(level="严重", hit="极巧合", how="推演")))
        self.assertFalse(_needs_return(item(level="中等", hit="极巧合", how="推演")))
        self.assertFalse(_needs_return(item(level="严重", hit="极巧合", how="推演"), first_round=False))

    def test_guessed_and_rare_only_severe_is_returned(self):
        # Codex 复查第 2 条：少见＝本项目会发生、要特定操作；严重的照退，不丢质量
        self.assertTrue(_needs_return(item(level="严重", hit="少见", how="推演")))
        self.assertTrue(_needs_return(item(level="严重", hit="少见", how="推演"), first_round=False))
        self.assertFalse(_needs_return(item(level="中等", hit="少见", how="推演")))
        self.assertFalse(_needs_return(item(level="轻微", hit="少见", how="推演")))

    def test_reproduced_is_still_returned(self):
        self.assertTrue(_needs_return(item(level="严重", hit="少见", how="实跑")))
        self.assertTrue(_needs_return(item(level="中等", hit="少见", how="实跑")), "第 1 轮实跑复现的中等照旧退回")

    def test_guessed_but_common_is_still_returned(self):
        self.assertTrue(_needs_return(item(level="严重", hit="常见", how="推演")))
        self.assertTrue(_needs_return(item(level="中等", hit="常见", how="推演")))

    def test_wrong_tests_are_always_returned(self):
        self.assertTrue(_needs_return(item(level="中等", kind="测试写错", hit="少见", how="推演")))

    def test_safety_or_data_loss_is_always_returned(self):
        self.assertTrue(_needs_return(item(level="中等", kind="安全或丢数据", hit="极巧合", how="推演")))
        self.assertTrue(_needs_return(item(level="轻微", kind="安全或丢数据", hit="少见", how="推演"), first_round=False))

    def test_later_rounds_keep_returning_only_severe(self):
        self.assertTrue(_needs_return(item(level="严重", hit="常见", how="推演"), first_round=False))
        self.assertFalse(_needs_return(item(level="中等", hit="常见", how="实跑"), first_round=False))

    def test_old_problems_rule_is_unchanged(self):
        self.assertFalse(_needs_return(item(level="中等", source="原来就有的", hit="常见", how="实跑")))
        self.assertTrue(_needs_return(item(level="严重", source="原来就有的", hit="常见", how="实跑")))


class SafetyNotAutoReleased(unittest.TestCase):
    def test_safety_kind_counts_like_severe_when_returns_run_out(self):
        import inspect
        src = inspect.getsource(dispatch)
        block = src[src.index("only_non_severe = ("):src.index("if verdict_kind == \"reject\" and prior_rejects >= MAX_REVIEW_RETURNS")]
        self.assertIn("安全或丢数据", block, "退回次数用完时，安全或丢数据和严重一样不自动放行")


class MergeKeepsSafety(unittest.TestCase):
    def test_relabelled_as_safety_is_kept_when_merging(self):
        from collab.dispatch import _merge_problem
        old = item(level="中等", kind="功能问题", hit="常见", how="实跑")
        new = item(level="中等", kind="安全或丢数据", hit="常见", how="实跑")
        self.assertEqual(_merge_problem(old, new)["类型"], "安全或丢数据")
        self.assertEqual(_merge_problem(new, old)["类型"], "安全或丢数据")


class NewKind(unittest.TestCase):
    def test_list_accepts_safety_kind(self):
        text = "结论：不通过\n\n复查清单：\n```json\n" + json.dumps(
            {"结论": "不通过", "问题": [item(kind="安全或丢数据")]}, ensure_ascii=False) + "\n```\n"
        _, errors = read_review_list(text)
        self.assertEqual(errors, [])


class Words(unittest.TestCase):
    def test_review_prompt_explains_the_rule(self):
        # 规矩的文字 11-20 起在 agents/手册/复查分级.md（规则包体检守着），这里查复查员真的拿到了它
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            prompt = dispatch._review_prompt(Path(d), "需求卡.md", "", "", d, False)
        self.assertIn("安全或丢数据", prompt)
        self.assertIn("碰得上", prompt)


if __name__ == "__main__":
    unittest.main()
