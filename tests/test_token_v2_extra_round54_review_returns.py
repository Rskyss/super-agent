"""第四轮 54 复查退回的四条用量回归测试。"""

import os
import subprocess
from functools import partial
from pathlib import Path

import test_token_v2_acceptance as samples
from acceptance_helpers import ProjectCase
from collab.agents import DEFAULT_MODELS, ask
from collab.dispatch import _dispatch
from collab.flow import read_flow, record
from collab.token_usage import usage_for
from desktop.view import card_view


class ReviewReturns(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "codex"
        (self.home / "sessions").mkdir(parents=True)
        os.environ["CODEX_HOME"] = str(self.home)
        self.cdir = self.make_card(state="开发中")

    def dev_tokens(self):
        view = card_view(self.project, "CARD-001")
        dev = next(station for station in view["stations"] if station["key"] == "dev")
        return dev.get("tokens") or {}

    def test_stopped_cursor_does_not_hide_completed_codex_round_total(self):
        cursor = record(self.cdir, "派活", src="调度员", dst="开发员", tool="cursor",
                        step="开发", round=1, live=True)
        (self.cdir / cursor["live"]).write_text(samples.sample("cursor_running.log"), encoding="utf-8")
        record(self.cdir, "停下", src="你", dst="程序", summary="施工已停下")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="接着上次中断处继续施工",
               tools={"开发员": "codex"})
        codex = record(self.cdir, "派活", src="调度员", dst="开发员", tool="codex",
                       step="开发", round=1, live=True)
        record(self.cdir, "回复", src="开发员", dst="调度员", tool="codex", step="开发",
               round=1, reply_to=codex["seq"], ok=True, seconds=10, tokens=samples.T(1200))

        tokens = self.dev_tokens()
        self.assertEqual((tokens.get("total"), bool(tokens.get("pending"))), (1200, False), tokens)

    def test_agy_timeout_keeps_usage_already_shown_while_running(self):
        expected = samples.AGY["total"]

        def time_out_after_steps(cmd, cwd, timeout, *, live_path, **kwargs):
            Path(live_path).write_text(samples.sample("agy_running.log"), encoding="utf-8")
            self.assertEqual(self.dev_tokens().get("total"), expected, "超时前右栏已有用量")
            raise subprocess.TimeoutExpired(cmd, timeout)

        result = _dispatch(partial(ask, runner=time_out_after_steps), self.cdir, "开发员",
                           "agy", "开发", "改按钮", self.project, DEFAULT_MODELS, 1)
        self.assertFalse(result.ok)
        reply = read_flow(self.cdir)[-1]
        saved = reply.get("tokens") or {}
        shown = self.dev_tokens()
        self.assertEqual((saved.get("total"), shown.get("total")), (expected, expected),
                         f"超时后的回复用量 {saved}，右栏用量 {shown}")

    def test_card_total_backfills_old_draft_and_counts_unreadable_rounds(self):
        samples.put_codex(self.home)
        record(self.cdir, "交接资料", src="调度员", dst="开发员", ok=True,
               tokens={"ok": False, "reason": "交接输出里没有会话编号"})
        old_draft = record(self.cdir, "派活", src="调度员", dst="调度员", tool="codex",
                           step="起草", round=1, live=True)
        (self.cdir / old_draft["live"]).write_text(samples.HEADER, encoding="utf-8")
        record(self.cdir, "回复", src="调度员", dst="调度员", tool="codex", step="起草",
               round=1, reply_to=old_draft["seq"], ok=True)
        later_draft = record(self.cdir, "派活", src="调度员", dst="调度员", tool="codex",
                             step="起草", round=2, live=True)
        record(self.cdir, "回复", src="调度员", dst="调度员", tool="codex", step="起草",
               round=2, reply_to=later_draft["seq"], ok=True,
               tokens={"ok": False, "reason": "起草输出里没有会话编号"})

        tokens = card_view(self.project, "CARD-001")["tokens"]
        self.assertEqual((tokens["total"], tokens.get("missing", 0)), (samples.CODEX_TOTAL, 2), tokens)

    def test_empty_reply_keeps_first_reason_when_live_log_is_unreadable(self):
        live = Path(self.tmp) / "实时输出.log"
        # 实时文件给得出编号，但临时会话目录里没有对应记录。
        live.write_text(samples.HEADER, encoding="utf-8")
        for tool in ("codex", "qwen"):
            with self.subTest(tool=tool):
                usage = usage_for(tool, output="", live=live)
                self.assertEqual((usage["ok"], usage.get("reason")), (False, "输出里没有会话编号"))
