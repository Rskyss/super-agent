"""流程修理第四轮 任务 5（56 甲）：复查退回用完停下时，调度员先替用户起草“继续改”。

69b 停下后“继续改”的话只能人写；用户不看代码写不出，Claude 不在就只能等。
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow, record
from collab.judge import PASS, Verdict
from collab.state import Card

REJ = ["结论：不通过\n1. 【中等】断网时页面一直重问。",
       "结论：不通过\n1. 【中等】【这次改的】取文件断网说文件没了。",
       "结论：不通过\n1. 【中等】【这次改的】切换对话卡在正在取回。"]
DRAFT = "每轮都是“找回结果”这一块的新问题，因为三处各管各的。\n---\n把三处合成一处：只有 404 算没了……"


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

    def go(self, reviews, draft=True, draft_ok=True):
        reviews = list(reviews)
        seen = {"draft": [], "models": []}
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, models=None, **kw):
            if "起草" in prompt and "继续改" in prompt:
                seen["draft"].append(prompt)
                seen["models"].append(models or {})
                return AgentResult(ok=draft_ok, output="", attempts=1, final=DRAFT if draft_ok else "")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex", draft=draft)
        return card, seen

    def drafts(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "起草"]


class Draft(Base):
    def test_drafts_after_returns_used_up(self):
        card, seen = self.go(REJ)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(seen["draft"]), 1)
        prompt = seen["draft"][0]
        for words in ("断网时页面一直重问", "取文件断网", "切换对话"):
            self.assertIn(words, prompt, "几轮复查都要给它看")
        self.assertIn("共同原因", prompt)
        self.assertIn("不要修改任何文件", prompt)
        self.assertEqual(seen["models"][0].get("codex_effort"), "xhigh", "用最高强度")
        phases = [e.get("phase") for e in self.drafts()]
        self.assertEqual(phases, ["开始", "好了"])
        self.assertIn("把三处合成一处", (self.cdir / self.drafts()[-1]["text"]).read_text(encoding="utf-8"))
        kinds = [e["kind"] for e in read_flow(self.cdir)]
        self.assertLess(kinds.index("转人工"), kinds.index("起草"), "先停下再起草，不耽误停下的提示")

    def test_off_by_default(self):
        _, seen = self.go(REJ, draft=False)
        self.assertEqual(seen["draft"], [])

    def test_failed_draft_only_noted(self):
        card, _ = self.go(REJ, draft_ok=False)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual([e.get("phase") for e in self.drafts()], ["开始", "没成"])

    def test_test_problem_not_drafted(self):
        rej = ["结论：不通过\n1. 【测试的问题】【中等】测试编号写错。"] * 4
        _, seen = self.go(rej)
        self.assertEqual(seen["draft"], [])


class DraftShown(unittest.TestCase):
    def setUp(self):
        from collab.workspace import init_collab
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        self.cdir = init_collab(self.project) / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", dst="调度员", cause="复查不通过", summary="复查不通过")

    def tearDown(self):
        self.tmp.cleanup()

    def test_view_carries_latest_unused_draft(self):
        from desktop.view import card_view
        record(self.cdir, "起草", src="程序", dst="你", phase="好了", say="起草好了", text=DRAFT)
        self.assertEqual(card_view(self.project)["draft"], DRAFT)
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="改")
        self.assertFalse(card_view(self.project).get("draft"), "用过的草稿不再填")

    def test_phase_says_drafting(self):
        from collab.phase import card_phase
        record(self.cdir, "起草", src="程序", dst="你", phase="开始", say="正在起草")
        self.assertIn("正在替你起草", card_phase(str(self.project), "CARD-001")["detail"])

    def test_clerk_plays_draft_news(self):
        from desktop.clerk import tasks
        self.assertIn("起草", tasks._NEWS_KINDS)

    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_revise_box_prefill(self):
        script = ("const P=require('./desktop/static/panel.js');"
                  "if(P.reviseDraft('', '草稿')!=='草稿')process.exit(1);"
                  "if(P.reviseDraft('我自己写的', '草稿')!=='我自己写的')process.exit(2);"
                  "if(P.reviseDraft('', '')!=='')process.exit(3);")
        done = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()


class DraftReadOnly(Base):
    def test_read_only_and_keeps_higher_effort(self):
        _, seen = self.go(REJ)
        self.assertEqual(seen["models"][0].get("codex_sandbox"), "read-only")

    def test_ultra_kept(self):
        from collab.dispatch import _draft_revise
        got = {}

        def ask_fn(adapter, prompt, cwd, models=None, **kw):
            got.update(models or {})
            return AgentResult(ok=True, output="", attempts=1, final=DRAFT)
        _draft_revise(ask_fn, self.cdir, "codex", {"codex_effort": "ultra"}, self.worktree, "卡", lambda w: [], 1)
        self.assertEqual(got["codex_effort"], "ultra")


class EndDrafting(unittest.TestCase):
    def test_stops_run_and_notes(self):
        from unittest.mock import patch
        from desktop import server
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d) / "card"
            cdir.mkdir()
            record(cdir, "起草", src="程序", dst="你", phase="开始", summary="正在起草")
            stopped = []
            from desktop.clerk import tasks as ctasks
            proj = Path(d) / "proj"
            cdir = proj / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            record(cdir, "起草", src="程序", dst="你", phase="开始", summary="正在起草")
            with patch.object(ctasks, "running", lambda p, k: {"card": "CARD-001", "pid": 1}), \
                    patch.object(ctasks, "stop_construction", lambda p, c: stopped.append(c)):
                self.assertTrue(server.end_drafting(str(proj), "CARD-001", cdir))
            self.assertEqual(stopped, ["CARD-001"])
            self.assertEqual(read_flow(cdir)[-1].get("phase"), "没成")

    def test_other_card_untouched(self):
        from unittest.mock import patch
        from desktop import server
        with tempfile.TemporaryDirectory() as d:
            from desktop.clerk import tasks as ctasks
            with patch.object(ctasks, "running", lambda p, k: {"card": "CARD-002", "pid": 1}), \
                    patch.object(ctasks, "stop_construction", lambda p, c: self.fail("不该停别的卡")):
                self.assertFalse(server.end_drafting(d, "CARD-001", Path(d)))


class ReviseStopsDraft(unittest.TestCase):
    def test_clerk_confirm_path_stops_draft_first(self):
        from unittest.mock import patch
        from desktop.clerk import tasks as ctasks
        order = []

        class Stop(Exception):
            pass

        def fake_running(p, k):
            order.append("running")
            raise Stop()
        with patch.object(ctasks, "card_phase", lambda p, c: {"code": "blocked", "actions": ["revise"]}), \
                patch.object(ctasks, "end_drafting", lambda p, c: order.append("end")), \
                patch.object(ctasks, "running", fake_running):
            with self.assertRaises(Stop):
                ctasks.apply_revise("/x", "CARD-001", "改一下")
        self.assertEqual(order[:2], ["end", "running"])
