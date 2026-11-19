"""测试先红卡第四轮复查退回的五条问题。"""
import fcntl
import os
import shlex
import signal
import sys
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import test_redfirst_acceptance as acceptance
from acceptance_helpers import ProjectCase, wait_until
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow, working_now
from collab.judge import FAIL, PASS, judge
from collab.state import Card
from desktop.clerk.tasks import launch, stop_construction
from review_fmt import listed_ask


class RunInterrupted(Exception):
    """施工中断，保留已经写下的流转记录。"""


class RedFirstDispatchRegression(acceptance.Base):
    def resume(self):
        return self.go(card=Card.load(self.cdir / "状态.json"))

    def test_resume_after_green_supplement_goes_to_developer_with_warning(self):
        self.reviews = [acceptance.PRODUCT_BAD]
        self.verdicts = [FAIL, PASS, PASS]
        normal_ask = self.ask_fn

        def interrupt_returned_development(adapter, prompt, cwd, **kwargs):
            if adapter == "cursor" and "复查" in self.who():
                raise RunInterrupted
            return normal_ask(adapter, prompt, cwd, **kwargs)

        with patch.object(self, "ask_fn", interrupt_returned_development):
            with self.assertRaises(RunInterrupted):
                self.go()
        expected_checks = [("写测试", FAIL), ("补测试", PASS)]
        self.assertEqual([(e["step"], e["result"]) for e in self.checks()], expected_checks)

        self.calls.clear()
        card = self.resume()
        self.assertEqual(self.who(), ["代码", "复查"], "补测试全过后续跑，只该重新派开发员和复查员")
        self.assertIn("没抓住", self.calls[0][1], "补测试全过的提醒应交给开发员")
        self.assertEqual([(e["step"], e["result"]) for e in self.checks()], expected_checks)
        self.assertEqual(card.state, "待验收")

    def test_enabling_red_check_on_resume_does_not_check_after_developer_delivered(self):
        normal_ask = self.ask_fn

        def interrupt_review(adapter, prompt, cwd, **kwargs):
            if adapter == "agy":
                raise RunInterrupted
            return normal_ask(adapter, prompt, cwd, **kwargs)

        with patch.object(self, "ask_fn", interrupt_review):
            with self.assertRaises(RunInterrupted):
                self.go(red_check=False)
        events = read_flow(self.cdir)
        self.assertTrue(any(e["kind"] == "回复" and e["src"] == "开发员" and e["ok"] for e in events))
        self.assertTrue(any(e["kind"] == "判定" and e["result"] == PASS for e in events))
        self.assertEqual(self.checks(), [])

        self.calls.clear()
        card = self.resume()
        self.assertEqual(self.checks(), [], "开发员已经交活，续跑不能拿改好的代码补做先红检查")
        self.assertEqual(self.who(), ["复查"])
        self.assertEqual(card.state, "待验收")

    def test_deleting_the_only_card_test_during_rewrite_never_dispatches_developer(self):
        test_path = Path("tests/test_card.py")
        command = [sys.executable, str(test_path)]

        def ask(adapter, prompt, cwd, **kwargs):
            step = working_now(read_flow(self.cdir))["step"]
            path = Path(cwd) / test_path
            if step == "写测试":
                path.write_text(
                    "import unittest\n\n"
                    "class SmokeTest(unittest.TestCase):\n"
                    "    def test_smoke(self):\n"
                    "        self.assertTrue(True)\n\n"
                    "unittest.main()\n",
                    encoding="utf-8",
                )
            elif step == "重写测试":
                path.unlink()
            else:
                return AgentResult(ok=False, output="开发员收到派活", attempts=1)
            return AgentResult(ok=True, output="交活", attempts=1)

        run_card(
            Card(self.cdir.name, state="待派发"), self.collab, self.project, command, "cursor", "codex",
            ask_fn=listed_ask(ask), judge_fn=judge, require_tests=True, red_check=True,
        )
        events = read_flow(self.cdir)
        self.assertTrue(any(e["kind"] == "派活" and e["step"] == "重写测试" for e in events))
        developer_steps = [e["step"] for e in events if e["kind"] == "派活" and e["dst"] == "开发员"]
        self.assertEqual(developer_steps, [], "删光本卡测试后，不能因命令找不到测试而把卡交给开发员")


class RedFirstCliRegression(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("SUPERAGENT_NO_RED_CHECK", None)
        self.cdir = self.make_card(state="待派发", plan=True)

    def run_args(self, command):
        return ["run", self.project, self.cdir.name, "--test-command", command,
                "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none"]


    def test_whitespace_cli_test_command_cannot_pass_red_check(self):
        _, output = self.cli(*self.run_args(" \t "))
        events = read_flow(self.cdir)
        passes = [e["step"] for e in events if e["kind"] == "先红检查" and e["result"] == PASS]
        self.assertEqual(passes, [], f"空白命令没有运行测试，不能记成先红检查全过。\n{output}")
        self.assertFalse(any(e["kind"] == "转人工" and e.get("cause") == "测试先红" for e in events))
