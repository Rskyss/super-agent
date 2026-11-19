"""开发员等模型时，换模型建议也要看本卡测试员的成功回复。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, Verdict
from collab.state import Card


class SameCardRecommendation(unittest.TestCase):
    def test_waiting_developer_is_offered_todays_latest_success_including_this_card(self):
        now = datetime(2026, 11, 19, 12)
        earlier = now - timedelta(minutes=1)

        def ask(tool, prompt, cwd, on_quiet=None, **kwargs):
            if tool == "agy":
                return AgentResult(ok=True, output="", attempts=1, final="测试写好了")
            on_quiet(300, True)
            on_quiet(600, True)
            return AgentResult(ok=False, output="看门人：超过 15 分钟模型没回话，已停止", attempts=1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "wt"
            worktree.mkdir()
            collab = root / ".collab"
            cdir = collab / "cards" / "CARD-012"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 测试卡\n", encoding="utf-8")

            other = collab / "cards" / "CARD-013"
            sent = record(other, "派活", src="调度员", dst="开发员", tool="codex",
                          model="earlier-model", ts=(earlier - timedelta(seconds=1)).timestamp())
            record(other, "回复", src="开发员", dst="调度员", tool="codex", ok=True,
                   reply_to=sent["seq"], ts=earlier.timestamp())

            with patch("collab.dispatch.datetime", wraps=datetime) as clock, patch("collab.flow.time") as flow_clock:
                clock.now.return_value = now
                flow_clock.time.return_value = now.timestamp()
                run_card(
                    Card("CARD-012", state="待派发"), collab, worktree, "true", "qwen", "agy",
                    ask_fn=ask, judge_fn=lambda *args, **kwargs: Verdict(FAIL, 1, "1 项没过"),
                    reviewer_adapter="codex", models={"qwen": "developer-model", "agy": "tester-model"},
                )

            messages = [event.get("say") or event["summary"] for event in read_flow(cdir)
                        if event["kind"] in ("提醒", "转人工")]

        self.assertEqual(len(messages), 3, "应记录两次提醒和一次停下说明")
        recommendation = "换成 agy（tester-model）"
        self.assertTrue(all(recommendation in words for words in messages),
                        f"两次提醒和停下说明都应建议{recommendation}。实际记录：{messages}")
