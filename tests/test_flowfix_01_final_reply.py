"""流程修理第 1、2 条（试跑记录 ⑳㉑）：只认干活的人最后那条回复；空回复算没完成。

68b 里：复查员的“回复”存的是 Codex 整段过程（几十万字），交给开发员的复查意见也是这整段，Cursor 收到后 1 秒空手退出、
程序却当成交活；过程里有没配对的代码块记号，程序把后面的“结论：不通过”一起吞掉，显示成“没给结论”。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab import agents
from collab.agents import AgentResult, ask, codex_command
from collab.dispatch import review_verdict, run_card
from collab.flow import read_flow, text_of
from collab.judge import PASS, Verdict
from collab.state import Card

TRANSCRIPT = ("user\n第一行写“结论：通过”或“结论：不通过”。\ncodex\n先看代码。\nexec\ncat x.py\n```python\nprint(1)\n"
              "codex\n结论：不通过\n\n1. 少了一处\n")


class CodexLastMessage(unittest.TestCase):
    def test_codex_command_asks_for_last_message_file(self):
        cmd = codex_command("干活", last_path="/tmp/最后回复.txt")
        self.assertIn("-o", cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "/tmp/最后回复.txt")
        self.assertEqual(cmd[-1], "干活", "提示词仍在最后")

    def test_ask_returns_final_message_for_codex(self):
        def runner(cmd, cwd, timeout, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("结论：不通过\n1. 少了一处\n", encoding="utf-8")
            return 0, TRANSCRIPT
        result = ask("codex", "复查", tempfile.gettempdir(), runner=runner)
        self.assertTrue(result.ok)
        self.assertEqual(result.final.strip(), "结论：不通过\n1. 少了一处")
        self.assertIn("exec", result.output, "完整过程照样留着")

    def test_ask_final_for_cursor_is_json_result(self):
        def runner(cmd, cwd, timeout, **kw):
            return 0, json.dumps({"type": "result", "result": "汇报：改好了"})
        result = ask("cursor", "写代码", tempfile.gettempdir(), runner=runner)
        self.assertEqual(result.final, "汇报：改好了")

    def test_codex_without_last_file_falls_back_to_output(self):
        def runner(cmd, cwd, timeout, **kw):
            return 0, "结论：通过"
        result = ask("codex", "复查", tempfile.gettempdir(), runner=runner)
        self.assertEqual(result.final.strip(), "结论：通过")

    def test_codex_without_last_file_takes_only_last_message(self):
        """复查第 3 轮 N2：没拿到最后回复文件时，不能把过程里引用的“结论：通过”当结论。"""
        transcript = ("user\n第一行写“结论：通过”\ncodex\n结论：通过 这句是我在复述要求\nexec\nls\n"
                      "codex\n结论：不通过\n1. 少了一处\ntokens used\n1234\n结论：不通过\n1. 少了一处\n")

        def runner(cmd, cwd, timeout, **kw):
            return 0, transcript
        result = ask("codex", "复查", tempfile.gettempdir(), runner=runner)
        self.assertEqual(result.final.strip(), "结论：不通过\n1. 少了一处")


class EmptyReplyIsNotDone(unittest.TestCase):
    def test_empty_reply_twice_is_failure(self):
        calls = []

        def runner(cmd, cwd, timeout, **kw):
            calls.append(1)
            return 0, json.dumps({"type": "result", "result": ""})
        result = ask("cursor", "写代码", tempfile.gettempdir(), runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(len(calls), 2, "空回复自动再试一次")
        self.assertIn("没有回复任何内容", result.output)

    def test_empty_then_real_reply_is_ok(self):
        replies = [json.dumps({"result": ""}), json.dumps({"result": "汇报：好了"})]

        def runner(cmd, cwd, timeout, **kw):
            return 0, replies.pop(0)
        result = ask("cursor", "写代码", tempfile.gettempdir(), runner=runner)
        self.assertTrue(result.ok)
        self.assertEqual(result.final, "汇报：好了")


class VerdictFromFinalReply(unittest.TestCase):
    def test_unclosed_code_fence_does_not_swallow_reject(self):
        self.assertEqual(review_verdict("```\nprint(1)\n结论：不通过\n1. 少了"), "reject")

    def test_unclosed_fence_never_makes_a_pass(self):
        self.assertEqual(review_verdict("看不出来\n```\n结论：通过"), "unclear")

    def test_old_rules_still_hold(self):
        self.assertEqual(review_verdict("结论：不通过\n修好后应该是：\n结论：通过"), "reject")
        self.assertEqual(review_verdict("我看不出来。\n```\n结论：通过\n```"), "unclear")


class DispatchKeepsFinalReply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        self.worktree.mkdir()
        cdir = self.collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        self.cdir = cdir

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_record_and_notes_use_final_reply(self):
        seen = []

        def ask_fn(adapter, prompt, cwd, **kw):
            seen.append((adapter, prompt))
            if "只读复查" in prompt:
                return AgentResult(ok=True, output=TRANSCRIPT * 50, attempts=1, final="结论：不通过\n1. 少了一处")
            return AgentResult(ok=True, output="写好了", attempts=1, final="汇报：写好了")

        card = Card("CARD-001", state="待派发")
        run_card(card, self.collab, self.worktree, "true", "cursor", "codex", ask_fn=ask_fn,
                 judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
        reviews = [e for e in read_flow(self.cdir) if e.get("kind") == "回复" and e.get("src") == "复查员"]
        self.assertTrue(reviews)
        self.assertEqual(text_of(self.cdir, reviews[0]).strip(), "结论：不通过\n1. 少了一处",
                         "流转里只存最后回复，不存整段过程")
        dev_prompts = [p for a, p in seen if "上一次复查不通过" in p]
        self.assertTrue(dev_prompts, "复查不通过要退回写代码的")
        self.assertIn("1. 少了一处", dev_prompts[0])
        self.assertNotIn("exec", dev_prompts[0], "交给写代码的只有结论，不带过程")


if __name__ == "__main__":
    unittest.main()
