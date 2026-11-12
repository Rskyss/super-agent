"""流程修理第 3 条（试跑记录 ⑧）：复查不通过自动退回写代码的，最多 2 次（用户 2026-11-12 定）。

68b 里复查不通过就停下等人，10:42 到 12:21 空等了 1 小时 40 分。
"""
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import MAX_REVIEW_RETURNS, run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.state import Card


class ReviewReturn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        self.worktree.mkdir()
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, verdicts):
        calls = {"dev": [], "review": 0}
        verdicts = list(verdicts)

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"] += 1
                return AgentResult(ok=True, output="过程" * 100, attempts=1, final=verdicts.pop(0))
            if "按任务卡 " in prompt and "编写自动化测试" in prompt:
                return AgentResult(ok=True, output="写了测试", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            return AgentResult(ok=True, output="改了", attempts=1, final="汇报：改了")

        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, self.collab, self.worktree, "true", "cursor", "codex", ask_fn=ask_fn,
                           judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
        return card, calls

    def test_limit_is_two(self):
        self.assertEqual(MAX_REVIEW_RETURNS, 2)

    def test_reject_then_pass_finishes_without_stopping(self):
        card, calls = self._run(["结论：不通过\n1. 按钮还是红的", "结论：通过"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["dev"]), 2)
        self.assertIn("1. 按钮还是红的", calls["dev"][1])
        self.assertEqual(card.rework, 0, "复查退回不算返工")
        kinds = [e.get("kind") for e in read_flow(self.cdir)]
        self.assertIn("退回", kinds, "流转里要看得见“自动退回”")
        self.assertNotIn("转人工", kinds)

    def test_three_rejects_stop_with_remaining_issues(self):
        card, calls = self._run(["结论：不通过\n1. 甲"] * 2 + ["结论：不通过\n1. 还是有问题乙\n2. 丙"])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(calls["dev"]), 3, "第 1 次开发 + 退回 2 次")
        self.assertEqual(calls["review"], 3)
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertIn("已自动退回 2 次", stop["summary"])
        self.assertIn("还是有问题乙", stop["summary"], "卡住原因里直接写剩下的问题")

    def test_revise_starts_count_again(self):
        from collab.flow import record
        self._run(["结论：不通过\n1. 甲"] * 3)
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="再改改", text="再改改")
        card = Card("CARD-001", state="需Claude介入")
        card.transition("开发中")
        calls = {"dev": 0}

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：不通过\n1. 丁")
            calls["dev"] += 1
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")
        card, _ = run_card(card, self.collab, self.worktree, "true", "cursor", "codex", ask_fn=ask_fn,
                           judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
        self.assertEqual(card.state, "需Claude介入")
        self.assertGreaterEqual(calls["dev"], 3, "追加要求之后重新数，仍能自动退回 2 次")


if __name__ == "__main__":
    unittest.main()
