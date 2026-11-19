"""C1.5 放进项目后 Claude 验收补修（2026-11-18，用户同意由 Claude 直接修，见 docs/开发进度跟踪.md 同日记录）。

1 补测前备份不碰卡片记录目录：.collab 落在项目里又没被忽略时，备份曾把自己一层层套进去，全部测试卡死
2 正文说“不通过”、清单却写“通过”（比如贴的是格式示例）：当清单不合格，绝不放行
3 停下时，长句里的严重问题不能被正文里的短句盖掉
4 停下 / 退回的说法用大白话，严重的标出来；翻不成大白话的只计数并说明其中有没有严重的
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.dispatch import _not_card_data, _whole_text_issues, plain_problems
from test_flowfix_c15_acceptance import Flow, NOW, item, review


class CardDataNotBackedUp(unittest.TestCase):
    def test_paths_inside_card_records_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj"
            cdir = root / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            paths = ["src/app.py", ".collab/cards/CARD-001/派测前备份/补测试/manifest.json",
                     ".collab/任务板.md", "tests/test_a.py", ".collabX/keep.py"]
            self.assertEqual(_not_card_data(root, cdir, paths), ["src/app.py", "tests/test_a.py", ".collabX/keep.py"])

    def test_card_records_outside_project_change_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d) / "main" / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            wt = Path(d) / "wt"
            wt.mkdir()
            self.assertEqual(_not_card_data(wt, cdir, ["a.py", ".collab/x.md"]), ["a.py", ".collab/x.md"])


class ProseRejectNeverPassedByList(Flow):
    def example_review(self):
        block = json.dumps({"结论": "通过", "问题": []}, ensure_ascii=False)
        return ("结论：不通过\n\n1. 【中等】【这次改的】保存后草稿丢失。\n\n清单格式示例：\n"
                f"````\n复查清单：\n```json\n{block}\n```\n````\n")

    def test_mismatch_asks_for_rewrite_and_never_passes(self):
        card, calls = self.run_flow([self.example_review(), self.example_review()])
        self.assertEqual(len(self.of(calls, "reformat")), 1, "两处对不上要先请复查员按格式重写")
        self.assertIn("对不上", self.of(calls, "reformat")[0][1])
        # （C1.8 按新口径改，登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：重写仍不合格 → 停下转 Claude（原来是整段退回）；绝不放行不变
        self.assertEqual(card.state, "需Claude介入")
        self.assertFalse([e for e in self.conclusions() if e.get("result") == "通过"])

    def test_rewrite_saying_pass_cannot_overturn_prose_reject(self):
        card, calls = self.run_flow([self.example_review(), review("通过")])
        # （C1.8 按新口径改，登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：靠重写的“通过”清单不能放行（不变），重写对不上就停下（原来是整段退回）
        self.assertEqual(card.state, "需Claude介入", "原话说不通过，靠重写的“通过”清单不能放行")
        self.assertFalse([e for e in self.conclusions() if e.get("result") == "通过"])


class FirstLineOnly(Flow):
    def test_log_line_in_body_does_not_block_a_real_pass(self):
        text = review("通过", prose="跑了测试，日志里有一行：\n结论：不通过（这是旧日志）\n现在都过了。")
        card, calls = self.run_flow([text])
        self.assertEqual(card.state, "待验收", "只看第一行的结论，正文引用的日志不算")
        self.assertFalse(self.of(calls, "reformat"))

    def test_rewrite_that_contradicts_itself_cannot_pass(self):
        no_list = "结论：通过\n看了改动，没问题。"
        bad_rewrite = review("通过").replace("结论：通过", "结论：不通过", 1)
        card, calls = self.run_flow([no_list, bad_rewrite])
        self.assertEqual(len(self.of(calls, "reformat")), 1)
        self.assertNotEqual(card.state, "待验收", "重写第一行说不通过、清单却写通过：不能放行")


class FirstLineWithNote(Flow):
    def test_reject_with_note_and_pass_list_goes_to_rewrite(self):
        text = review("通过").replace("结论：通过", "结论：不通过（有一项回归）", 1)
        card, calls = self.run_flow([text, text])
        self.assertEqual(len(self.of(calls, "reformat")), 1, "首行带说明的不通过也要先请重写，不能直接当没结论")
        # （C1.8 按新口径改，登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：重写仍对不上 → 停下转 Claude，不放行（原来是整段退回）
        self.assertEqual(card.state, "需Claude介入")
        self.assertFalse([e for e in self.conclusions() if e.get("result") == "通过"])


class FirstLineWords(unittest.TestCase):
    def test_cannot_pass_is_not_pass(self):
        from collab.dispatch import first_line_verdict
        self.assertEqual(first_line_verdict("结论：通过不了，保存后草稿丢失"), "")
        self.assertEqual(first_line_verdict("**结论：不通过（有一项回归）**"), "reject")
        self.assertEqual(first_line_verdict("\n结论：通过。\n结论：不通过"), "pass")


class CountMatchesList(Flow):
    def test_two_unplain_items_counted_as_two(self):
        # C1.6：不严重的退满会自动进待验收；这里测停下时的件数，问题改成严重的
        bad = review("不通过", [item(level="严重", what="`a()` 返回 None"),
                               item(level="严重", what="`b()` 抛错", where="app/b.py:3")])
        self.run_flow([bad, bad, bad])
        stop = [e for e in __import__("collab.flow", fromlist=["read_flow"]).read_flow(self.cdir)
                if e.get("kind") == "转人工"][-1]
        self.assertIn("复查还剩 2 件事", stop.get("say", ""))
        self.assertIn("2 件都没写成大白话（其中有严重的）", stop.get("say", ""))


class SevereLongerTextKept(unittest.TestCase):
    def test_longer_severe_sentence_not_swallowed(self):
        block = json.dumps({"结论": "不通过", "问题": [item(level="严重", what="草稿列表显示异常，切换设备会删除所有草稿")]},
                           ensure_ascii=False)
        text = f"结论：不通过\n1. 草稿列表显示异常\n\n复查清单：\n```json\n{block}\n```"
        words = " ".join(_whole_text_issues(text))
        self.assertIn("删除所有草稿", words)


class PlainWords(unittest.TestCase):
    def test_plain_and_severe_marked(self):
        words, hidden, severe_hidden = plain_problems([
            item(level="严重", what="**[P1] 保存后草稿全丢。** [save.py:12](/x/save.py:12) 原因是没写盘"),
            item(what="按钮文字多一个空格"),
            item(level="严重", what="`read_review_list()` 返回 None"),
        ])
        self.assertEqual(words, ["保存后草稿全丢（严重）", "按钮文字多一个空格"])
        self.assertEqual((hidden, severe_hidden), (1, True))


class StopNeverCallsSevereSmall(Flow):
    def test_all_unplain_severe_still_counted(self):
        bad = review("不通过", [item(level="严重", what="`save_draft()` 返回 None")])
        card, _ = self.run_flow([bad, bad, bad])
        stop = [e for e in __import__("collab.flow", fromlist=["read_flow"]).read_flow(self.cdir)
                if e.get("kind") == "转人工"][-1]
        say = stop.get("say", "")
        self.assertIn("1 件都没写成大白话（其中有严重的）", say)
        self.assertNotIn("没按格式写", say)
        self.assertNotIn("剩下的都是小问题", say)
        # 三轮同一个问题：判断说“反复出现”是对的；严重已在件数里标出（上面那句）


class StopTextPlain(Flow):
    def test_stop_says_plain_words_and_marks_severe(self):
        bad = review("不通过", [item(level="严重", what="**保存后草稿丢失。** [save.py:12](/x/save.py:12) 没写盘")])
        card, _ = self.run_flow([bad, bad, bad])
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in __import__("collab.flow", fromlist=["read_flow"]).read_flow(self.cdir)
                if e.get("kind") == "转人工"][-1]
        self.assertIn("保存后草稿丢失（严重）", stop.get("say", ""))
        self.assertNotIn("save.py", stop.get("say", ""), "停下的话里不放文件名")


if __name__ == "__main__":
    unittest.main()
