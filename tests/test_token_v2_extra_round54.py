"""A2 补测：读数先后、实时文件刷新，以及右栏逐轮记账。"""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import test_token_v2_acceptance as acceptance
import test_token_usage_extra as v1
from acceptance_helpers import ProjectCase, needs_node, run_node
from collab.flow import FLOW_FILE, read_flow, record


def agy_line(event, input_tokens, output_tokens=2, cached=3, thinking=1):
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
             "cache_read_tokens": cached, "thinking_tokens": thinking,
             "total_tokens": input_tokens + output_tokens}
    return json.dumps({"event": event, event: {"usage": usage}}) + "\n"


def cursor_result(input_tokens):
    return json.dumps({"type": "result", "usage": {
        "inputTokens": input_tokens, "outputTokens": 5, "cacheReadTokens": 30,
        "cacheWriteTokens": 7, "reasoningTokens": 2,
    }}) + "\n"


class UsageSources(acceptance.Home):
    def test_agy_result_replaces_steps_and_reply_wins_over_live(self):
        from collab.token_usage import usage_for

        live = self.live("agy_done.log")
        output = agy_line("step_update", 90) + agy_line("result", 20)
        acceptance.same(self, usage_for("agy", output=output, live=live), {
            "total": 25, "input": 23, "cached": 3, "output": 2, "reasoning": 1, "children": 0,
        })

    def test_agy_skips_bad_lines_and_steps_without_usage(self):
        from collab.token_usage import usage_for

        output = (agy_line("step_update", 10, 4, 7, 2)
                  + '坏行\n[]\n{"event":"step_update","step_update":{"usage":[]}}\n'
                  + agy_line("step_update", 30, 6, 3, 4)
                  + '{"event":"result","result":{"usage":null}}\n'
                  + '{"event":"result","result":')
        acceptance.same(self, usage_for("agy", output=output), {
            "total": 60, "input": 50, "cached": 10, "output": 10, "reasoning": 6, "children": 0,
        })

    def test_cursor_takes_last_result_through_bad_lines_and_half_written_tail(self):
        from collab.token_usage import usage_for

        output = (cursor_result(50) + '坏行\nnull\n' + cursor_result(70)
                  + '{"type":"assistant","usage":{"inputTokens":999}}\n'
                  + '{"type":"result","usage":')
        acceptance.same(self, usage_for("cursor", output=output, live=self.live("cursor_done.log")), {
            "total": 112, "input": 107, "cached": 30, "output": 5, "reasoning": 2, "children": 0,
        })

    def test_codex_and_qwen_prefer_reply_then_fall_back_to_live(self):
        from collab.token_usage import usage_for

        acceptance.put_codex(self.home)
        v1.put_session(self.home, total=234)
        live = self.live("live_header.log")
        for tool in ("codex", "qwen"):
            with self.subTest(tool=tool):
                acceptance.same(self, usage_for(tool, v1.HEADER, live), {"total": 234})
                acceptance.same(self, usage_for(tool, "结论：通过", live), {"total": acceptance.CODEX_TOTAL})

    def test_cursor_bad_file_is_missing_instead_of_waiting(self):
        from collab.token_usage import usage_for

        for path in (self.tmp, self.tmp / "已清理.log", self.live("unknown_tool.log")):
            with self.subTest(path=path.name):
                usage = usage_for("cursor", live=path)
                self.assertFalse(usage["ok"])
                self.assertFalse(usage.get("pending"), usage)
                self.assertTrue(usage["reason"].strip(), usage)

    def test_live_read_error_returns_a_reason(self):
        from collab.token_usage import usage_for

        for tool in ("agy", "cursor"):
            with self.subTest(tool=tool):
                live = self.live(f"{tool}_done.log")
                denied = PermissionError("模拟读取失败")
                with mock.patch("builtins.open", side_effect=denied), mock.patch("io.open", side_effect=denied):
                    usage = usage_for(tool, live=live)
                self.assertFalse(usage["ok"])
                self.assertFalse(usage.get("pending"), usage)
                self.assertTrue(usage["reason"].strip(), usage)


class LiveCache(acceptance.Home):
    def test_agy_reuses_unchanged_file_and_refreshes_when_only_size_changes(self):
        from collab.token_usage import usage_for

        live = self.tmp / "agy.log"
        live.write_text(agy_line("step_update", 10), encoding="utf-8")
        mtime = 1_700_000_000
        os.utime(live, (mtime, mtime))
        with v1.count_reads(live) as reads:
            acceptance.same(self, usage_for("agy", live=live), {"total": 15})
            self.assertGreater(sum(reads), 0, "第一次要从文件读出用量")
            reads.clear()
            acceptance.same(self, usage_for("agy", live=live), {"total": 15})
            self.assertEqual(sum(reads), 0, "文件没变，第二次不应重读内容")

        with live.open("a", encoding="utf-8") as log:
            log.write(agy_line("step_update", 20))
        os.utime(live, (mtime, mtime))
        acceptance.same(self, usage_for("agy", live=live), {"total": 40})

    def test_cursor_cache_distinguishes_paths_and_refreshes_same_size_rewrite(self):
        from collab.token_usage import usage_for

        first = self.tmp / "first.log"
        second = self.tmp / "second.log"
        mtime = 1_700_000_000
        for path, value in ((first, 11), (second, 22)):
            path.write_text(cursor_result(value), encoding="utf-8")
            os.utime(path, (mtime, mtime))
        self.assertEqual(first.stat().st_size, second.stat().st_size)
        acceptance.same(self, usage_for("cursor", live=first), {"total": 53})
        acceptance.same(self, usage_for("cursor", live=second), {"total": 64})

        first.write_text(cursor_result(33), encoding="utf-8")
        os.utime(first, (mtime + 10, mtime + 10))
        self.assertEqual(first.stat().st_size, second.stat().st_size)
        acceptance.same(self, usage_for("cursor", live=first), {"total": 75})


class FlowRecording(acceptance.Home):
    def setUp(self):
        super().setUp()
        self.cdir = self.tmp / "card"
        self.cdir.mkdir()
        self.needs = self.cdir / "需求卡.md"
        self.needs.write_text("# 改按钮\n", encoding="utf-8")

    def fake(self, output, live=""):
        from collab.agents import AgentResult

        def ask(adapter, prompt, cwd, live_path=None, **kwargs):
            if live_path:
                Path(live_path).write_text(live, encoding="utf-8")
            return AgentResult(ok=True, output=output, attempts=1, final="改好了")

        return ask

    def test_dispatch_reply_uses_live_when_output_is_empty_and_keeps_reply_link(self):
        from collab.dispatch import _dispatch

        fake = self.fake("", live=cursor_result(18))
        _dispatch(fake, self.cdir, "开发员", "cursor", "开发", "改按钮", self.tmp, {}, 1)
        sent, reply = read_flow(self.cdir)[-2:]
        self.assertEqual((sent["kind"], reply["kind"]), ("派活", "回复"))
        self.assertEqual(reply["reply_to"], sent["seq"])
        acceptance.same(self, reply.get("tokens"), {"total": 60})

    def test_cursor_handover_records_usage(self):
        from collab.dispatch import _make_handover

        fake = self.fake(cursor_result(30))
        _make_handover(fake, self.cdir, "cursor", {}, self.needs, self.tmp)
        handover = read_flow(self.cdir)[-1]
        self.assertEqual(handover["kind"], "交接资料")
        self.assertTrue(handover["ok"])
        acceptance.same(self, handover.get("tokens"), {"total": 72})

    def test_unsupported_tool_omits_usage_in_reply_and_handover(self):
        from collab.dispatch import _dispatch, _make_handover

        fake = self.fake(cursor_result(30))
        _make_handover(fake, self.cdir, "claude", {}, self.needs, self.tmp)
        self.assertNotIn("tokens", read_flow(self.cdir)[-1])
        _dispatch(fake, self.cdir, "开发员", "claude", "开发", "改按钮", self.tmp, {}, 1)
        self.assertNotIn("tokens", read_flow(self.cdir)[-1])


class PanelRounds(ProjectCase):
    def setUp(self):
        super().setUp()
        home = Path(self.tmp) / "codex"
        home.mkdir()
        os.environ["CODEX_HOME"] = str(home)
        self.cdir = self.make_card(state="开发中")

    def send(self, tool, model=None, number=1, role="开发员", output=None):
        sent = record(self.cdir, "派活", src="调度员", dst=role, tool=tool, model=model,
                      step="开发", round=number, live=output is not None)
        if output is not None:
            (self.cdir / sent["live"]).write_text(output, encoding="utf-8")
        return sent

    def reply(self, sent, seconds, tokens=None):
        record(self.cdir, "回复", src=sent["dst"], dst="调度员", tool=sent["tool"],
               step=sent["step"], round=sent["round"], reply_to=sent["seq"],
               seconds=seconds, ok=True, tokens=tokens)

    def view(self):
        from desktop.view import card_view

        return card_view(self.project, "CARD-001")

    def station(self, view, key="dev"):
        return next(s for s in view["stations"] if s["key"] == key)

    def test_old_cursor_uses_its_own_dispatch_and_saved_reply_wins_over_log(self):
        older = self.send("cursor", "Auto", output=cursor_result(100))
        newer = self.send("agy", number=2, output=agy_line("result", 900))
        # 第一条回复到达时，最近的派活已是另一个工具。
        self.reply(older, 11)
        self.reply(newer, 23, tokens=acceptance.T(40))
        flow = self.cdir / FLOW_FILE
        before = flow.read_bytes()

        view = self.view()
        dev = self.station(view)
        self.assertEqual(dev["spent"], 34)
        self.assertEqual([(h.get("who"), h["seconds"], h["tokens"]) for h in dev["history"]],
                         [("Cursor · Auto", 11, 142), ("agy", 23, 40)])
        self.assertEqual(dev["tokens"]["total"], 182)
        self.assertEqual(view["tokens"]["total"], 182)
        self.assertEqual(flow.read_bytes(), before, "补数只读卡片历史")

    def test_waiting_cursor_is_counted_only_after_result_and_freezes_on_reply(self):
        self.reply(self.send("agy", "Gemini"), 12, tokens=acceptance.T(40))
        cursor = self.send("cursor", "Auto", number=2, output=acceptance.sample("cursor_running.log"))
        waiting = self.view()
        pending = self.station(waiting).get("tokens") or {}
        self.assertTrue(pending.get("pending"), pending)
        self.assertEqual(pending["reason"], "这一轮结束后出数")
        self.assertEqual(waiting["tokens"]["total"], 40)
        self.assertFalse(waiting["tokens"].get("missing"))

        with (self.cdir / cursor["live"]).open("a", encoding="utf-8") as log:
            log.write(cursor_result(10))
        self.assertEqual(self.view()["tokens"]["total"], 92)
        self.reply(cursor, 14, tokens=acceptance.T(52))
        (self.cdir / cursor["live"]).write_text(cursor_result(999), encoding="utf-8")
        finished = self.view()
        self.assertEqual(finished["tokens"]["total"], 92)
        dev = self.station(finished)
        self.assertFalse(dev["tokens"].get("pending"))
        self.assertEqual([h["tokens"] for h in dev["history"]], [40, 52])

    def test_missing_counts_across_stations_and_preserves_recorded_reason(self):
        rejected = {"ok": False, "reason": "那轮的会话文件找不到"}
        self.reply(self.send("agy", "Gemini", output=agy_line("result", 99)), 9, tokens=rejected)
        # 已经交活但没有结果行，老卡上的这一轮也要算读不到。
        self.reply(self.send("cursor", role="测试员", output=acceptance.sample("cursor_running.log")), 6)
        self.reply(self.send("agy", number=2), 14, tokens=acceptance.T(50))
        view = self.view()
        dev = self.station(view)
        test = self.station(view, "test")

        self.assertEqual(dev.get("tokens", {}).get("missing"), 1)
        self.assertEqual(test.get("tokens", {}).get("missing"), 1)
        self.assertFalse(test["tokens"].get("pending"))
        self.assertEqual(view["tokens"]["total"], 50)
        self.assertEqual(view["tokens"].get("missing"), 2)
        first = dev["history"][0]
        self.assertTrue(first.get("missing"))
        self.assertIsNone(first["tokens"])
        self.assertEqual(first.get("reason"), rejected["reason"])
        self.assertTrue(test["history"][0]["reason"].strip())

    def test_card_counts_handover_and_nonstation_reply_once(self):
        record(self.cdir, "交接资料", src="调度员", dst="开发员", ok=True, tokens=acceptance.T(21))
        self.reply(self.send("agy", role="调度员"), 8, tokens=acceptance.T(32))
        self.reply(self.send("agy"), 10, tokens=acceptance.T(100))
        view = self.view()
        self.assertEqual(view["tokens"]["other"], 53)
        self.assertEqual(self.station(view)["tokens"]["total"], 100)
        self.assertEqual(view["tokens"]["total"], 153)


@needs_node()
class PanelText(unittest.TestCase):
    def test_active_cursor_keeps_timer_and_wait_message_on_refresh(self):
        result = run_node(r'''
const assert = require('assert');
const panel = require('./desktop/static/panel.js');
const station = {state: 'active', rounds: 2, elapsed: 12,
  tokens: {ok: false, pending: true, reason: '这一轮结束后出数'}};
assert.strictEqual(panel.metaText(station), '第 2 轮 · 已干 0:12 · 这一轮结束后出数');
assert.strictEqual(panel.metaText(station, 5), '第 2 轮 · 已干 0:17 · 这一轮结束后出数');
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
