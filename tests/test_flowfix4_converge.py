"""流程修理第四轮 任务 4（57）：复查收口。

69b 复查一轮比一轮细：断网重问 → 切换对话那一瞬间 → 结果文件被人删掉。退回过以后的复查，每条标
【这次改的】【原来就有的】：这次改的严重、中等照样退回；原来就有的（除了严重）和所有轻微记进“以后再修”，不退回；
连着两轮在同一处冒新问题，第二次退回改成“先找共同原因”。
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
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
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
        self.assertEqual(back, [items[0], items[1], items[4], items[5]])
        self.assertEqual(later, [items[2], items[3]])


class Converge(Base):
    def test_first_review_has_no_tags(self):
        _, calls = self.go([R1, "结论：通过"])
        self.assertNotIn("【原来就有的】", calls["review"][0])
        self.assertIn("【原来就有的】", calls["review"][1], "退回过以后的复查要标")

    def test_only_old_and_minor_counts_as_pass(self):
        r2 = ("结论：不通过\n1. 【中等】【原来就有的】切换对话时卡在正在取回。\n"
              "2. 【轻微】【这次改的】提示字太小。")
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
        r2 = ("结论：不通过\n1. 【中等】【这次改的】改完后按钮没反应。\n"
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
        r2 = "结论：不通过\n1. 【中等】【这次改的】取结果文件断网就说文件没了。位置：[chat.js:610](/x/chat.js:610)"
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
            self.assertIn("以后再修", ph["detail"])

    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_accept_confirm_lists_later(self):
        script = ("const P=require('./desktop/static/panel.js');"
                  "const t=P.acceptConfirm(['切换对话时卡住','提示字小']);"
                  "if(!t.includes('切换对话时卡住')||!t.includes('以后再修'))process.exit(1);"
                  "if(P.acceptConfirm([])!==null)process.exit(2);")
        done = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
