"""01 号单：复查员报错、超时、没给结论，都不能算通过。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import review_verdict, run_card
from collab.judge import PASS, Verdict
from collab.state import Card
from collab.workspace import init_collab


class ReviewVerdictTest(unittest.TestCase):
    def test_reading_the_conclusion(self):
        self.assertEqual(review_verdict("结论：通过\n没问题"), "pass")
        self.assertEqual(review_verdict("结论: 通过"), "pass")
        self.assertEqual(review_verdict("结论：不通过\n有问题"), "reject")
        self.assertEqual(review_verdict("结论：通过\n后来改主意\n结论：不通过"), "reject")

    def test_anything_else_is_unclear(self):
        for text in ("", "看起来没问题", "通过", "我觉得可以通过"):
            self.assertEqual(review_verdict(text), "unclear", text)


class ReviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.collab = init_collab(self.tmp)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("做一个函数", encoding="utf-8")

    def run_with(self, text, ok=True):
        def ask(adapter, prompt, cwd, **kwargs):
            if adapter == "agy":
                return AgentResult(ok=ok, output=text, attempts=1)
            return AgentResult(ok=True, output="", attempts=1)

        card, _ = run_card(Card("CARD-001", state="待派发"), collab_dir=self.collab, worktree=self.tmp,
                           test_command=["fake"], dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                           judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
        return card

    def test_reviewer_failed_is_not_a_pass(self):
        self.assertEqual(self.run_with("结论：通过", ok=False).state, "需Claude介入")

    def test_timeout_with_no_words_is_not_a_pass(self):
        self.assertEqual(self.run_with("", ok=False).state, "需Claude介入")

    def test_no_conclusion_is_not_a_pass(self):
        self.assertEqual(self.run_with("我看过了，大体可以").state, "需Claude介入")

    def test_reason_is_recorded_in_plain_words(self):
        self.run_with("我看过了")
        flow = (self.cdir / "流转.jsonl").read_text(encoding="utf-8")
        self.assertIn("没有给出结论", flow)

    def test_real_pass_still_reaches_acceptance(self):
        self.assertEqual(self.run_with("结论：通过\n符合需求").state, "待验收")

    def test_review_text_is_kept(self):
        self.run_with("我看过了")
        self.assertIn("我看过了", (self.cdir / "复查结果.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
