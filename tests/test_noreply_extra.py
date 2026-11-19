"""补测看门人和派活的边界情形，沿用现有验收入口。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from collab import agents
from collab.dispatch import _dispatch, run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask


STUCK = agents.AgentResult(
    ok=False,
    output="看门人：超过 15 分钟模型没回话（工具在等模型，没在跑命令、没改文件），已停止",
    attempts=1,
)
DONE = agents.AgentResult(ok=True, output="", attempts=1, final="好了")


class RetrySettings(unittest.TestCase):
    def test_stream_settings_are_passed_as_qwen_config_only(self):
        qwen = agents.qwen_command("你好", model="example")
        configs = [value for flag, value in zip(qwen, qwen[1:]) if flag == "-c"]
        self.assertIn("model_providers.qwtp.stream_idle_timeout_ms=120000", configs)
        self.assertIn("model_providers.qwtp.stream_max_retries=3", configs)
        for arg in agents.codex_command("你好"):
            self.assertNotIn("stream_idle_timeout_ms", arg)
            self.assertNotIn("stream_max_retries", arg)


class ProcessTree(unittest.TestCase):
    def watch(self, process_table, on_quiet):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            agents, "_process_table", side_effect=process_table
        ):
            with self.assertRaises(agents.QuietTimeout) as caught:
                agents.watched_runner(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    tmp, timeout=10, quiet_limit=1, poll=0.05,
                    on_quiet=on_quiet, remind_at=(0.2, 0.4),
                )
        return caught.exception

    def test_plugin_and_its_descendants_do_not_count_as_commands(self):
        def process_table():
            tool = agents._active_tool.pid
            return {
                tool: (0, 1, "codex exec"),
                tool + 1: (tool, 1, "/usr/bin/node /plugins/mcp-server.js"),
                tool + 2: (tool + 1, 1, "/usr/bin/env-wrapper"),
                tool + 3: (tool + 2, 1, "/bin/sh -c sleep 30"),
                tool + 4: (0, 1, "/usr/bin/python3 unrelated.py"),
            }

        reminders = []
        stop = self.watch(process_table, lambda seconds, waiting: reminders.append(waiting))
        self.assertEqual(reminders, [True, True], "插件的下级进程和无关进程都不能算工具在干活")
        self.assertIn("模型没回话", str(stop))

    def test_nested_command_can_finish_between_reminders_and_stop(self):
        reminders = []

        def process_table():
            tool = agents._active_tool.pid
            table = {tool: (0, 1, "codex exec"), tool + 1: (tool, 1, "/usr/bin/env-wrapper")}
            if len(reminders) < 2:
                table[tool + 2] = (tool + 1, 1, "/usr/bin/python3 check.py")
            return table

        stop = self.watch(process_table, lambda seconds, waiting: reminders.append(waiting))
        self.assertEqual(reminders, [False, False], "隔了一层的真命令也算在干活")
        self.assertTrue(stop.waiting, "命令结束后，停下时要重新判断")
        self.assertIn("模型没回话", str(stop))

    def test_missing_process_table_keeps_working_assumption(self):
        reminders = []
        stop = self.watch(lambda: {}, lambda seconds, waiting: reminders.append(waiting))
        self.assertEqual(reminders, [False, False])
        self.assertFalse(stop.waiting)
        self.assertIn("没有任何文件变化", str(stop))

    def test_callback_with_unreadable_signature_still_gets_seconds(self):
        reminders = []

        class OldCallback:
            @property
            def __signature__(self):
                raise ValueError("签名不可读")

            def __call__(self, seconds):
                reminders.append(seconds)

        self.watch(lambda: {}, OldCallback())
        self.assertEqual(len(reminders), 2)
        self.assertTrue(all(type(value) is int for value in reminders))


class DispatchCases(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.worktree = root / "wt"
        self.worktree.mkdir()
        self.collab = root / ".collab"
        self.cdir = self.collab / "cards" / "CARD-012"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 测试卡\n", encoding="utf-8")
        self.counts = self.collab / "模型卡住.json"
        self.today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def send(self, result=DONE, *, tool="qwen", model="model-a", cdir=None, remind=None):
        def ask(tool, prompt, cwd, on_quiet=None, **kwargs):
            if remind is not None:
                on_quiet(*remind)
            return result

        return _dispatch(
            ask, cdir or self.cdir, "开发员", tool, "开发", "按测试施工",
            self.worktree, {tool: model}, 1,
        )

    def history(self, card, tool, model, *, ok=True, ts=None, old_time=None, pending_model=None):
        cdir = self.collab / "cards" / card
        sent = record(cdir, "派活", src="调度员", dst="开发员", tool=tool, model=model)
        if pending_model is not None:
            record(cdir, "派活", src="调度员", dst="开发员", tool=tool, model=pending_model)
        record(cdir, "回复", src="开发员", dst="调度员", tool=tool,
               ok=ok, reply_to=sent["seq"], ts=ts, time=old_time)
        if ts is None:
            events = read_flow(cdir)
            events[-1].pop("ts")
            (cdir / "流转.jsonl").write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8",
            )

    def reminder(self, seconds=600):
        self.send(remind=(seconds, True))
        reminders = [e for e in read_flow(self.cdir) if e["kind"] == "提醒"]
        self.assertTrue(reminders, "看门人应把提醒放进流转记录")
        event = reminders[-1]
        return event.get("say") or event["summary"]

    def test_latest_success_today_uses_reply_ts_and_linked_dispatch(self):
        self.history("CARD-001", "qwen", "今日最近", ts=self.today.timestamp() + 2, pending_model="未回复的模型")
        self.history("CARD-002", "agy", "今日早些", ts=self.today.timestamp() + 1)
        self.history("CARD-003", "qwen", "model-a", ts=self.today.timestamp() + 3)
        self.history("CARD-004", "agy", "失败的模型", ok=False, ts=self.today.timestamp() + 4)

        tip = self.reminder()
        self.assertIn("10 分钟", tip)
        self.assertIn("没回话", tip)
        self.assertIn("qwen（今日最近）", tip)

    def test_ts_takes_precedence_over_conflicting_old_date(self):
        self.history("CARD-001", "agy", "昨天的模型", ts=(self.today - timedelta(seconds=1)).timestamp(),
                     old_time=self.today.isoformat())

        tip = self.reminder(300)
        self.assertIn("没回话", tip)
        self.assertIn("换一个模型", tip)
        self.assertNotIn("昨天的模型", tip)

    def test_time_only_legacy_reply_can_still_be_recommended(self):
        self.history("CARD-001", "agy", "旧记录里的模型", old_time=self.today.isoformat())

        self.assertIn("agy（旧记录里的模型）", self.reminder(300))

    def test_corrupt_count_file_is_treated_as_empty_and_counting_resumes(self):
        self.counts.write_text('{"qwen|model-a":', encoding="utf-8")

        self.send(STUCK)

        self.assertEqual([e["kind"] for e in read_flow(self.cdir)], ["派活", "回复"])
        try:
            counts = json.loads(self.counts.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.fail(f"计数记录损坏后，应从第一次重新计数并写回。实际读不到新记录（{exc}）")
        self.assertEqual(counts, {"qwen|model-a": 1})

    def test_count_write_failure_does_not_block_recording_the_reply(self):
        self.counts.mkdir()

        self.send(STUCK)

        events = read_flow(self.cdir)
        self.assertEqual([e["kind"] for e in events], ["派活", "回复"])
        self.assertFalse(events[-1]["ok"])
        self.assertEqual(events[-1]["reply_to"], events[0]["seq"])

    def test_count_follows_tool_and_model_across_cards(self):
        self.counts.write_text('{"cursor|other-model": 1}', encoding="utf-8")
        self.send(STUCK)
        self.send(STUCK)
        self.assertFalse(any(e["kind"] == "提醒" for e in read_flow(self.cdir)))
        other = self.collab / "cards" / "CARD-013"
        with patch("collab.flow.time") as clock:
            clock.time.return_value = self.today.timestamp() + 1
            self.send(cdir=other, model="model-b")
            clock.time.return_value = self.today.timestamp() + 2
            self.send(cdir=other, tool="agy")
        self.send(cdir=other)

        events = read_flow(other)
        warnings = [e for e in events if e["kind"] == "提醒"]
        self.assertEqual(len(warnings), 1, "只有原来的工具和模型组合该提醒")
        warn = warnings[0]
        following = events[events.index(warn) + 1]
        self.assertEqual((following["kind"], following["tool"], following["model"]), ("派活", "qwen", "model-a"))
        words = warn.get("say") or warn["summary"]
        self.assertIn("连续 2 次", words)
        self.assertIn("换成 agy（model-a）", words, "应推荐今天最近成功的那个组合，包括本卡的成功回复")
        self.assertEqual(json.loads(self.counts.read_text(encoding="utf-8")), {"cursor|other-model": 1})

    def run_stuck_card(self, verdict):
        def ask(tool, prompt, cwd, **kwargs):
            if "只读复查" in prompt:
                return agents.AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                return DONE
            return STUCK

        card, _ = run_card(
            Card("CARD-012", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=listed_ask(ask), judge_fn=lambda *args, **kwargs: verdict,
            reviewer_adapter="codex", models={"qwen": "model-a"},
        )
        return card

    def test_stop_suggests_successful_testers_tool_without_model(self):
        card = self.run_stuck_card(Verdict(FAIL, 1, "1 项没过"))
        self.assertEqual(card.state, "需Claude介入")
        events = read_flow(self.cdir)
        stop = next(e for e in events if e["kind"] == "转人工")
        words = stop.get("say") or stop["summary"]
        self.assertIn("模型没回话", words)
        self.assertIn("换成 codex", words, "Codex 测试员交活成功，没记型号也应推荐工具名")
        self.assertNotIn("换成 codex（", words, "没记型号时只推荐工具名")
        self.assertEqual(sum(e["kind"] == "派活" and e["dst"] == "开发员" for e in events), 1)

    def test_stopped_developer_with_passing_tests_still_goes_to_review(self):
        card = self.run_stuck_card(Verdict(PASS, 0, "本卡测试全过"))
        self.assertEqual(card.state, "待验收")
        events = read_flow(self.cdir)
        self.assertEqual(
            [e["dst"] for e in events if e["kind"] == "派活" and e["dst"] != "测试员"],
            ["开发员", "复查员"],
        )
        self.assertFalse(any(e["kind"] == "转人工" for e in events))
