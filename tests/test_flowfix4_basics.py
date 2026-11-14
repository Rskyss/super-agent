"""流程修理第四轮 任务 1：干活的人用中文汇报（54）、返工次数把复查退回也算上（58）。

69b 千问 DeepSeek 的进度话是英文；台账写“返工 0 次”，其实复查退回了 3 次、用户继续改 1 次。
"""
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow, record, rework_words
from collab.judge import PASS, Verdict
from collab.state import Card


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
        prompts = []
        reviews = list(reviews)
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            prompts.append(prompt)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex")
        return card, prompts


class Chinese(Base):
    def test_every_prompt_asks_for_chinese(self):
        _, prompts = self.go(["结论：不通过\n1. 【中等】按钮没反应。", "结论：通过"])
        self.assertGreaterEqual(len(prompts), 4)
        for p in prompts:
            self.assertIn("用中文", p)


class ReworkCount(Base):
    def test_end_summary_counts_review_returns(self):
        card, _ = self.go(["结论：不通过\n1. 【中等】按钮没反应。", "结论：通过"])
        end = [e for e in read_flow(self.cdir) if e.get("kind") == "结束"][-1]
        self.assertIn("复查退回 1 次", end["summary"])
        self.assertIn("测试没过返工 0 次", end["summary"])

    def test_69b_like_history(self):
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开工")
        for _ in range(2):
            record(self.cdir, "退回", src="程序", dst="开发员", summary="复查不通过，自动退回")
        record(self.cdir, "转人工", src="程序", dst="调度员", summary="复查不通过")
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="只改这一块")
        record(self.cdir, "退回", src="程序", dst="开发员", summary="复查不通过，自动退回")
        words = rework_words(0, self.cdir)
        self.assertEqual(words, "测试没过返工 0 次、复查退回 4 次（其中你继续改 1 次）")

    def test_plan_change_before_start_not_counted(self):
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="方案再改改")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开工")
        self.assertEqual(rework_words(0, self.cdir), "测试没过返工 0 次、复查退回 0 次")

    def test_no_returns(self):
        self.assertEqual(rework_words(2, self.cdir), "测试没过返工 2 次、复查退回 0 次")

    def test_ledger_line_uses_words(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from collab import cli
        project = Path(self.tmp.name) / "proj"
        (project / "docs").mkdir(parents=True)
        seen = []
        with patch.object(cli, "ledger_add", lambda p, text: seen.append(text)):
            cli._finish_run_line(project, "CARD-001", Card("CARD-001", state="待验收", rework=0), self.cdir)
        self.assertTrue(seen and "复查退回" in seen[0], seen)


if __name__ == "__main__":
    unittest.main()


class RecordLock(unittest.TestCase):
    """第四轮复查：起草和本机服务同时记流转，序号不能撞。"""

    def test_parallel_processes_get_unique_seq(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            code = ("import sys; sys.path.insert(0, %r)\nfrom collab.flow import record\n"
                    "for i in range(40): record(%r, 'x', summary=str(i))\n") % (str(Path(__file__).resolve().parents[1]), d)
            procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
            for p in procs:
                p.wait()
            seqs = [e["seq"] for e in read_flow(d)]
            self.assertEqual(len(seqs), 160)
            self.assertEqual(len(set(seqs)), 160)
