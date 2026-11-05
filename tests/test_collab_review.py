import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.cli import build_parser
from collab.dispatch import run_card
from collab.judge import PASS, Verdict
from collab.state import Card
from collab.workspace import init_collab


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.collab = init_collab(self.tmp.name)
        self.card_dir = self.collab / "cards" / "CARD-001"
        self.card_dir.mkdir(parents=True)
        (self.card_dir / "需求卡.md").write_text("做一个函数", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, review_text, reviewer="agy"):
        calls = []

        def ask(adapter, prompt, cwd, **kwargs):
            calls.append(adapter)
            out = review_text if adapter == reviewer else ""
            return AgentResult(ok=True, output=out, attempts=1)

        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.tmp.name,
                           test_command=["fake"], dev_adapter="cursor", test_adapter="codex",
                           ask_fn=ask, judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"),
                           reviewer_adapter=reviewer)
        return card, calls

    def test_reviewer_pass_goes_to_acceptance_and_is_recorded(self):
        card, calls = self._run("结论：通过\n改动符合需求")
        self.assertEqual(card.state, "待验收")
        self.assertIn("agy", calls)
        self.assertIn("结论：通过", (self.card_dir / "复查结果.md").read_text(encoding="utf-8"))

    def test_reviewer_reject_escalates_to_claude(self):
        card, _ = self._run("结论：不通过\n改了不该改的逻辑")
        self.assertEqual(card.state, "需Claude介入")
        self.assertIn("不通过", (self.card_dir / "复查结果.md").read_text(encoding="utf-8"))

    def test_no_reviewer_skips_review(self):
        card, calls = self._run("结论：不通过", reviewer=None)
        self.assertEqual(card.state, "待验收")
        self.assertNotIn("agy", calls)


class RulesInPromptsTest(unittest.TestCase):
    def test_every_worker_is_told_to_read_the_rulebook(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collab = init_collab(tmp.name)
        card_dir = collab / "cards" / "CARD-001"
        card_dir.mkdir(parents=True)
        (card_dir / "需求卡.md").write_text("x", encoding="utf-8")
        prompts = {}

        def ask(adapter, prompt, cwd, **kw):
            prompts.setdefault(adapter, prompt)
            return AgentResult(ok=True, output="结论：通过", attempts=1)

        run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=tmp.name,
                 test_command=["t"], dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                 judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
        for adapter in ("cursor", "codex", "agy"):
            self.assertIn("规则.md", prompts[adapter], adapter)
            self.assertTrue(Path(prompts[adapter].split("规则：")[1].split("。")[0]).exists())


class ReportFromReplyTest(unittest.TestCase):
    """开发员不再自己写汇报文件（会在代码目录里留下多余文件被判越界），
    而是在回复末尾写“汇报：……”，由程序保存到卡片文件夹。"""

    def test_report_is_saved_from_reply_and_no_file_path_is_requested(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collab = init_collab(tmp.name)
        card_dir = collab / "cards" / "CARD-001"
        card_dir.mkdir(parents=True)
        (card_dir / "需求卡.md").write_text("x", encoding="utf-8")
        prompts = {}

        def ask(adapter, prompt, cwd, **kw):
            prompts.setdefault(adapter, prompt)
            out = '{"result":"改好了。汇报：新增 morning，测试全过。"}' if adapter == "cursor" else ""
            return AgentResult(ok=True, output=out, attempts=1)

        run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=tmp.name,
                 test_command=["t"], dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                 judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"))
        self.assertNotIn(str(card_dir / "汇报.md"), prompts["cursor"])
        self.assertIn("汇报：", prompts["cursor"])
        report = (card_dir / "汇报.md").read_text(encoding="utf-8")
        self.assertIn("新增 morning，测试全过。", report)
        self.assertNotIn('"}', report)


class ReadableOutputTest(unittest.TestCase):
    """agy / Cursor 返回的是 JSON 包装，记录里只保留真正的文字，方便用户直接看。"""

    def test_review_record_keeps_only_the_text(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collab = init_collab(tmp.name)
        card_dir = collab / "cards" / "CARD-001"
        card_dir.mkdir(parents=True)
        (card_dir / "需求卡.md").write_text("x", encoding="utf-8")
        agy_json = '{"conversation_id":"abc","status":"SUCCESS","response":"结论：通过\\n\\n- 无问题"}'

        def ask(adapter, prompt, cwd, **kw):
            return AgentResult(ok=True, output=agy_json if adapter == "agy" else "", attempts=1)

        card, _ = run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=tmp.name,
                           test_command=["t"], dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                           judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
        record = (card_dir / "复查结果.md").read_text(encoding="utf-8")
        self.assertIn("结论：通过", record)
        self.assertIn("- 无问题", record)
        self.assertNotIn("conversation_id", record)
        self.assertEqual(card.state, "待验收")

    def test_rejection_inside_json_is_still_detected(self):
        from collab.dispatch import readable
        self.assertEqual(readable('{"result":"结论：不通过\\n改错了"}'), "结论：不通过\n改错了")
        self.assertEqual(readable("普通文字"), "普通文字")


class ToolChoiceTest(unittest.TestCase):
    def test_default_tools_are_cursor_codex_agy(self):
        """命令里不写工具时交给团队配置决定；什么都没配，仍是 Cursor 写代码、Codex 写测试、agy 复查。"""
        from collab.config import run_tools
        args = build_parser().parse_args(["run", "/tmp/x", "CARD-001", "--test-command", "true"])
        self.assertEqual((args.dev_tool, args.test_tool, args.review_tool), (None, None, None))
        with tempfile.TemporaryDirectory() as tmp:
            tools = run_tools(tmp, args.dev_tool, args.test_tool, args.review_tool)
        self.assertEqual((tools["dev"], tools["test"], tools["review"]), ("cursor", "codex", "agy"))

    def test_tools_can_be_chosen_per_run(self):
        args = build_parser().parse_args(["run", "/tmp/x", "CARD-001", "--test-command", "true",
                                          "--dev-tool", "agy", "--review-tool", "none"])
        self.assertEqual((args.dev_tool, args.review_tool), ("agy", "none"))

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["run", "/tmp/x", "CARD-001", "--test-command", "true",
                                       "--dev-tool", "gemini-old"])


if __name__ == "__main__":
    unittest.main()
