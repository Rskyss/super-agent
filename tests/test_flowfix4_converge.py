"""流程修理第四轮 任务 4（57）：复查收口。

69b 复查一轮比一轮细：断网重问 → 切换对话那一瞬间 → 结果文件被人删掉。退回过以后的复查，每条标
【这次改的】【原来就有的】：这次改的严重、中等照样退回；原来就有的（除了严重）和所有轻微记进“以后再修”，不退回；
连着两轮在同一处冒新问题，第二次退回改成“先找共同原因”。
2026-11-18 第二批（跑卡规矩，用户批准）改口径：每一轮复查都标；这次改的不分轻重一律退回；
只有原来就有的（除了严重）记进“以后再修”，点确认时不再列给用户，只记台账。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, _triage, run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask  # C1.5：老测试的随手复查意见补上固定清单（tests/review_fmt.py）

R1 = "结论：不通过\n1. 【中等】断网时页面一直重问。位置：[chat.js:600](/x/chat.js:600)"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("资料\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, reviews):
        reviews = list(reviews)
        calls = {"dev": [], "review": []}
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"].append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex")
        return card, calls

    def later(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "以后再修"]


class Triage(unittest.TestCase):
    def test_rules(self):
        items = ["【严重】【原来就有的】重启后结果没了。", "【中等】【这次改的】按钮没反应。",
                 "【中等】【原来就有的】切换对话时卡住。", "【轻微】【这次改的】字小了。",
                 "【中等】没标是哪种。", "【测试的问题】【轻微】【原来就有的】测试编号写错。"]
        back, later = _triage(items)
        # 第二批 A1（11-18 用户批准）：这次改的轻微也退回，只有原来就有的（非严重）记进以后再修
        self.assertEqual(back, [items[0], items[1], items[3], items[4], items[5]])
        self.assertEqual(later, [items[2]])


class Converge(Base):
    def test_every_review_has_tags(self):
        # 第二批 A1：第 1 轮也要标（原来只有退回过以后才标）
        _, calls = self.go([R1, "结论：通过"])
        self.assertIn("【原来就有的】", calls["review"][0])
        self.assertIn("【原来就有的】", calls["review"][1])

    def test_only_old_counts_as_pass(self):
        # 第二批 A1：这次改的轻微不再算放行，这里只留原来就有的
        r2 = "结论：不通过\n1. 【中等】【原来就有的】切换对话时卡在正在取回。"
        card, calls = self.go([R1, r2])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["dev"]), 2)
        later = self.later()
        self.assertTrue(later)
        self.assertIn("切换对话", json.dumps(later, ensure_ascii=False))
        self.assertIn("切换对话", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))
        end = [e for e in read_flow(self.cdir) if e.get("kind") == "结束"][-1]
        self.assertIn("复查退回 1 次", end["summary"], "收口的那次不算退回")

    def test_mixed_returns_only_current(self):
        r2 = ("结论：不通过\n1. 【严重】【这次改的】改完后按钮没反应。\n"  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的
              "2. 【中等】【原来就有的】结果文件被删时显示破图。")
        card, calls = self.go([R1, r2, "结论：通过"])
        back = calls["dev"][2]
        self.assertIn("按钮没反应", back)
        self.assertNotIn("破图", back)
        self.assertIn("破图", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))

    def test_severe_old_still_returns(self):
        r2 = "结论：不通过\n1. 【严重】【原来就有的】重启后试做结果全丢。"
        card, calls = self.go([R1, r2, "结论：通过"])
        self.assertIn("全丢", calls["dev"][2])

    def test_same_place_new_issue_asks_for_root_cause(self):
        r2 = "结论：不通过\n1. 【严重】【这次改的】取结果文件断网就说文件没了。位置：[chat.js:610](/x/chat.js:610)"  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的
        card, calls = self.go([R1, r2, "结论：通过"])
        self.assertIn("共同原因", calls["dev"][2])
        self.assertNotIn("共同原因", calls["dev"][1], "第一次退回照旧")


class LaterShown(unittest.TestCase):
    def test_phase_lists_later(self):
        from collab.flow import record
        from collab.phase import card_phase
        from collab.workspace import init_collab
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "proj"
            project.mkdir()
            collab = init_collab(project)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            Card("CARD-001", state="待验收").save(cdir / "状态.json")
            record(cdir, "以后再修", src="程序", items=["切换对话时卡住", "提示字小"], summary="记进以后再修 2 件")
            record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
            ph = card_phase(str(project), "CARD-001")
            self.assertEqual(ph["later"], ["切换对话时卡住", "提示字小"])
            self.assertIn("台账", ph["detail"])   # 第二批 A2：说明改成“已记进台账”，不再说点确认时列给你看

    # test_accept_confirm_lists_later 已删：第二批 A2 点确认不再弹以后再修清单，改由 test_flowfix_c1_acceptance 检查


if __name__ == "__main__":
    unittest.main()


class ReviewRound1(Base):
    """第四轮 Codex 复查第 1 轮（收口部分）。"""

    def test_old_means_before_card_started(self):
        _, calls = self.go([R1, "结论：通过"])
        self.assertIn("开工前", calls["review"][1])
        self.assertNotIn("上次复查时就已经在", calls["review"][1])

    def test_resume_after_closed_does_not_return(self):
        from collab.dispatch import _review_reject_pending
        from collab.flow import record
        rep = record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过",
                     text="结论：不通过\n1. 【轻微】【这次改的】字小。")
        record(self.cdir, "以后再修", src="程序", reply=rep["seq"], closed=True, items=["字小"], summary="收口")
        self.assertEqual(_review_reject_pending(self.cdir, read_flow(self.cdir)), (False, ""))

    def test_resume_after_partial_returns_only_kept(self):
        from collab.dispatch import _review_reject_pending
        from collab.flow import record
        rep = record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过",
                     text="结论：不通过\n1. 【中等】【这次改的】按钮没反应。\n2. 【轻微】【这次改的】字小。")
        record(self.cdir, "以后再修", src="程序", reply=rep["seq"], items=["字小"], summary="收口",
               back="结论：不通过\n1. 【中等】【这次改的】按钮没反应。")
        pending, notes = _review_reject_pending(self.cdir, read_flow(self.cdir))
        self.assertTrue(pending)
        self.assertNotIn("字小", notes)

    def test_same_name_other_folder_not_same_place(self):
        from collab.dispatch import _places
        a = _places(["x 位置：[frontend/index.js:10](/w/frontend/index.js:10)"])
        b = _places(["y 位置：[backend/index.js:90](/w/backend/index.js:90)"])
        self.assertFalse({p for p, _ in a} & {p for p, _ in b})

    def test_same_line_again_is_not_root_cause(self):
        r1 = "结论：不通过\n1. 【中等】断网重问。位置：[chat.js:600](/x/chat.js:600)"
        r2 = "结论：不通过\n1. 【严重】【这次改的】切走再回来立刻重查。位置：[chat.js:600](/x/chat.js:600)"  # C1.6：第 2 轮新冒出的不严重问题不退回，测“同一行不算共同原因”要让它退回，改严重
        _, calls = self.go([r1, r2, "结论：通过"])
        self.assertNotIn("共同原因", calls["dev"][2])


class LaterKept(unittest.TestCase):
    def test_later_survives_revise(self):
        from collab.flow import record
        from collab.phase import card_phase
        from collab.workspace import init_collab
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "proj"
            project.mkdir()
            cdir = init_collab(project) / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            Card("CARD-001", state="待验收").save(cdir / "状态.json")
            record(cdir, "以后再修", src="程序", items=["切换对话时卡住"], summary="记进以后再修 1 件")
            record(cdir, "追加要求", src="你", dst="调度员", summary="再改一处")
            record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
            self.assertEqual(card_phase(str(project), "CARD-001")["later"], ["切换对话时卡住（继续改之前记的，可能已经修了）"])
