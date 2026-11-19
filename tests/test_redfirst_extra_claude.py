"""测试先红卡：Claude 接手时补的两项（复查第 2 轮退回里碰得上的两条）。"""
from pathlib import Path
from unittest.mock import patch

import test_redfirst_acceptance as acceptance
from collab.agents import AgentResult
from collab.flow import read_flow
from collab.state import Card

ROOT = Path(__file__).resolve().parents[1]


class Interrupted(Exception):
    pass


class ResumeAfterDeveloperTimedOut(acceptance.Base):
    def test_no_red_check_after_developer_was_dispatched_even_if_it_timed_out(self):
        normal_ask = self.ask_fn

        def dev_times_out_then_review_interrupted(adapter, prompt, cwd, **kwargs):
            if adapter == "agy":
                raise Interrupted
            if adapter == "cursor":
                self.calls.append(("代码", prompt))
                (Path(cwd) / "app.py").write_text("A = 99\n", encoding="utf-8")   # 改了一半就超时
                return AgentResult(ok=False, output="超时：30 分钟没交活", attempts=1, final="")
            return normal_ask(adapter, prompt, cwd, **kwargs)

        with patch.object(self, "ask_fn", dev_times_out_then_review_interrupted):
            with self.assertRaises(Interrupted):
                self.go(red_check=False)
        events = read_flow(self.cdir)
        self.assertTrue(any(e["kind"] == "回复" and e["src"] == "开发员" and e["ok"] is False for e in events))
        self.assertEqual(self.checks(), [])
        self.calls.clear()
        self.go(card=Card.load(self.cdir / "状态.json"))
        self.assertEqual(self.checks(), [], "派过写代码的（哪怕超时没交成），副本已经改过，续跑不能补做先红检查")
        self.assertNotIn("测试", self.who(), "不能退回去重写测试")


class CommandKeptAsIs(acceptance.Base):
    def test_run_passes_the_test_command_unchanged(self):
        src = (ROOT / "collab" / "cli.py").read_text(encoding="utf-8")
        body = src[src.index("def cmd_run("):]
        self.assertIn('test_command = str(args.test_command or "")\n', body, "测试命令原样用，不删首尾空格")
        self.assertNotIn("args.page_check or \"\").strip() or None", body, "页面检查命令也原样用")
