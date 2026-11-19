"""旧的成功记录没写型号也参加换模型推荐；有记型号的优先（11-19 Claude 接手定：验收测试要具体推荐），都没有型号才只写工具名。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from collab.agents import AgentResult
from collab.dispatch import _dispatch
from collab.flow import read_flow, record


class ToolOnlyRecommendation(unittest.TestCase):
    def test_waiting_reminder_prefers_success_with_model_over_later_tool_only(self):
        now = datetime(2026, 11, 19, 12)
        latest = now - timedelta(hours=1)
        earlier = latest - timedelta(hours=1)

        def ask(tool, prompt, cwd, on_quiet=None, **kwargs):
            on_quiet(300, True)
            return AgentResult(ok=True, output="", attempts=1, final="写好了")

        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "wt"
            worktree.mkdir()
            cards = Path(tmp) / ".collab" / "cards"
            legacy = cards / "CARD-001"
            legacy.mkdir(parents=True)
            old_events = [
                {"seq": 7, "kind": "派活", "tool": "codex", "dst": "测试员",
                 "time": (latest - timedelta(minutes=7)).isoformat()},
                {"seq": 9, "kind": "回复", "tool": "codex", "src": "测试员",
                 "time": latest.isoformat(), "ok": True, "reply_to": 7},
            ]
            (legacy / "流转.jsonl").write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in old_events),
                encoding="utf-8",
            )

            other = cards / "CARD-002"
            sent = record(other, "派活", tool="agy", model="earlier-model",
                          ts=(earlier - timedelta(minutes=7)).timestamp())
            record(other, "回复", tool="agy", ok=True, reply_to=sent["seq"], ts=earlier.timestamp())

            current = cards / "CARD-012"
            with patch("collab.dispatch.datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                _dispatch(ask, current, "开发员", "qwen", "开发", "按测试写代码",
                          worktree, {"qwen": "developer-model"}, 1)
            reminders = [event for event in read_flow(current) if event["kind"] == "提醒"]

        self.assertEqual(len(reminders), 1)
        words = reminders[0].get("say") or reminders[0]["summary"]
        self.assertIn("换成 agy（earlier-model）", words, "有型号的成功记录优先，哪怕更早")
