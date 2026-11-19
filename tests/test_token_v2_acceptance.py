"""token 用量第二版 A2 卡验收测试（Claude 写；**施工方不许改**，补充测试另写 test_token_v2_extra*.py）。

依据 docs/token用量第二版/方案.md、施工单-A2.md。样本见 tests/token_samples/README.md 第二版一节。
测试不读本机真实的 ~/.codex（CODEX_HOME 指到临时目录）。
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import ProjectCase, needs_node, run_node
from collab.flow import read_flow, record

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "token_samples"
MAIN = "11111111-2222-7333-8444-555555555555"
HEADER = (SAMPLES / "live_header.log").read_text(encoding="utf-8")
CODEX_TOTAL = 1035411
AGY = {"total": 85500, "input": 85000, "cached": 80000, "output": 500, "reasoning": 310}
CURSOR = {"total": 1094659, "input": 1080675, "cached": 964864, "output": 13984, "reasoning": 9115}


def sample(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


def put_codex(home: Path):
    day = home / "sessions" / "2026" / "11" / "17"
    day.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / "normal.jsonl", day / f"rollout-2026-11-17T08-17-57-{MAIN}.jsonl")


def same(test, got, want):
    test.assertTrue(got and got.get("ok"), got)
    for key, value in want.items():
        test.assertEqual(got.get(key), value, key)


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "codex"
        (self.home / "sessions").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def live(self, name):
        path = self.tmp / name
        path.write_text(sample(name), encoding="utf-8")
        return path


# ---------------- 取数：四种工具一个入口 ----------------

class UsageFor(Home):
    def test_agy_finished_uses_result(self):
        from collab.token_usage import usage_for
        same(self, usage_for("agy", live=self.live("agy_done.log")), AGY)
        same(self, usage_for("agy", output=sample("agy_done.log")), AGY)

    def test_agy_running_adds_up_steps(self):
        from collab.token_usage import usage_for
        same(self, usage_for("agy", live=self.live("agy_running.log")), AGY)

    def test_agy_half_written_last_line_ignored(self):
        from collab.token_usage import usage_for
        same(self, usage_for("agy", live=self.live("agy_half.log")), AGY)

    def test_cursor_finished(self):
        from collab.token_usage import usage_for
        same(self, usage_for("cursor", output=sample("cursor_done.log")), CURSOR)
        same(self, usage_for("cursor", live=self.live("cursor_done.log")), CURSOR)

    def test_cursor_running_is_pending_not_missing(self):
        from collab.token_usage import usage_for
        got = usage_for("cursor", live=self.live("cursor_running.log"))
        self.assertFalse(got["ok"])
        self.assertTrue(got.get("pending"))
        self.assertIn("结束后出数", got["reason"])

    def test_codex_and_qwen_unchanged(self):
        from collab.token_usage import usage_for
        put_codex(self.home)
        for tool in ("codex", "qwen"):
            got = usage_for(tool, output=HEADER + "结论：通过\n")
            self.assertTrue(got["ok"], tool)
            self.assertEqual(got["total"], CODEX_TOTAL, tool)

    def test_unreadable_says_why_and_never_raises(self):
        from collab.token_usage import usage_for
        for tool in ("agy", "cursor"):
            for args in ({"live": self.live("unknown_tool.log")}, {"live": self.tmp / "没有这个文件.log"},
                         {"output": ""}, {"output": None}, {"live": None, "output": 12345}):
                got = usage_for(tool, **args)
                self.assertFalse(got["ok"], (tool, args))
                self.assertTrue(str(got.get("reason") or "").strip(), (tool, args))

    def test_other_tools_return_none(self):
        from collab.token_usage import usage_for
        self.assertIsNone(usage_for("claude", output="x"))
        self.assertIsNone(usage_for("", output="x"))


# ---------------- 记录：派活回复、交接资料都带用量 ----------------

class Recording(Home):
    def setUp(self):
        super().setUp()
        self.cdir = self.tmp / "card"
        self.cdir.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()

    def fake(self, text):
        from collab.agents import AgentResult

        def ask_fn(adapter, prompt, cwd, expect_file=None, models=None, live_path=None, **kw):
            if live_path:
                Path(live_path).write_text(text, encoding="utf-8")
            return AgentResult(ok=True, output=text, attempts=1, final="改好了")
        return ask_fn

    def last(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind][-1]

    def test_reply_carries_tokens_for_agy_and_cursor(self):
        from collab.dispatch import _dispatch
        for tool, name, want in (("agy", "agy_done.log", AGY), ("cursor", "cursor_done.log", CURSOR)):
            _dispatch(self.fake(sample(name)), self.cdir, "开发员", tool, "开发", "改", self.work, {}, 1)
            same(self, self.last("回复").get("tokens"), want)

    def test_handover_by_agy_carries_tokens(self):
        from collab.dispatch import _make_handover
        (self.cdir / "需求卡.md").write_text("# 改按钮\n", encoding="utf-8")
        _make_handover(self.fake(sample("agy_done.log")), self.cdir, "agy", {}, self.cdir / "需求卡.md", self.work)
        same(self, self.last("交接资料").get("tokens"), AGY)


# ---------------- 右栏取数：换人、补数、没读到、在跑 ----------------

def T(n):
    return {"ok": True, "total": n, "input": n, "cached": 0, "output": 0, "reasoning": 0, "children": 0}


class PanelData(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "codex"
        (self.home / "sessions").mkdir(parents=True)
        os.environ["CODEX_HOME"] = str(self.home)
        self.cdir = self.make_card(state="开发中")

    def station(self, view, key):
        return next(s for s in view["stations"] if s["key"] == key)

    def round_(self, role, tool, model, *, tokens=None, live_text=None, reply=True, round_no=1):
        sent = record(self.cdir, "派活", src="调度员", dst=role, tool=tool, model=model, step="开发",
                      round=round_no, summary="干活", live=True)
        if live_text is not None:
            (self.cdir / sent["live"]).write_text(live_text, encoding="utf-8")
        if reply:
            extra = {"tokens": tokens} if tokens is not None else {}
            record(self.cdir, "回复", src=role, dst="调度员", tool=tool, step="开发", round=round_no,
                   ok=True, seconds=10, reply_to=sent["seq"], summary="交了", **extra)
        return sent

    def test_switching_tools_adds_every_round_and_names_each(self):
        from desktop.view import card_view
        self.round_("开发员", "qwen", "DeepSeek V4.1 Flash", tokens=T(2000), round_no=1)
        self.round_("开发员", "qwen", "DeepSeek V4.1 Flash", tokens=T(3000), round_no=2)
        self.round_("开发员", "agy", "Gemini 4 Hydrogen（高）", tokens=dict(AGY, children=0), round_no=3)
        # 老卡：agy 交了活，记录里没有用量，但实时输出还在 → 补数
        self.round_("开发员", "agy", "Gemini 4 Hydrogen（高）", live_text=sample("agy_done.log"), round_no=4)
        view = card_view(self.project, "CARD-001")
        dev = self.station(view, "dev")
        self.assertEqual(dev["tokens"]["total"], 2000 + 3000 + AGY["total"] * 2)
        self.assertFalse(dev["tokens"].get("missing"))
        whos = [h.get("who") for h in dev["history"]]
        self.assertEqual(whos, ["千问 · DeepSeek V4.1 Flash"] * 2 + ["agy · Gemini 4 Hydrogen（高）"] * 2)
        self.assertEqual([h.get("tokens") for h in dev["history"]], [2000, 3000, AGY["total"], AGY["total"]])
        self.assertEqual(view["tokens"]["total"], 2000 + 3000 + AGY["total"] * 2)

    def test_old_codex_round_is_backfilled_from_its_log(self):
        from desktop.view import card_view
        put_codex(self.home)
        self.round_("测试员", "codex", "GPT-6.2-Sol（high）", live_text=HEADER + "干活中\n")
        view = card_view(self.project, "CARD-001")
        self.assertEqual(self.station(view, "test")["tokens"]["total"], CODEX_TOTAL)

    def test_round_without_record_or_log_is_counted_missing(self):
        from desktop.view import card_view
        self.round_("开发员", "agy", "Gemini 4 Hydrogen（高）", tokens=dict(AGY, children=0), round_no=1)
        self.round_("开发员", "agy", "Gemini 4 Hydrogen（高）", round_no=2)   # 没记录、日志也没了
        view = card_view(self.project, "CARD-001")
        dev = self.station(view, "dev")["tokens"]
        self.assertEqual(dev["total"], AGY["total"])
        self.assertEqual(dev.get("missing"), 1)
        last = self.station(view, "dev")["history"][-1]
        self.assertTrue(last.get("missing"))
        self.assertTrue(str(last.get("reason") or "").strip())
        self.assertEqual(view["tokens"].get("missing"), 1)

    def test_running_cursor_round_is_pending_not_missing(self):
        from desktop.view import card_view
        self.round_("开发员", "cursor", "Auto", live_text=sample("cursor_running.log"), reply=False)
        view = card_view(self.project, "CARD-001")
        dev = self.station(view, "dev")["tokens"]
        self.assertFalse(dev.get("ok"))
        self.assertTrue(dev.get("pending"))
        self.assertFalse(view["tokens"].get("missing"))

    def test_running_agy_round_grows_from_its_log(self):
        from desktop.view import card_view
        self.round_("开发员", "agy", "Gemini 4 Hydrogen（高）", live_text=sample("agy_running.log"), reply=False)
        view = card_view(self.project, "CARD-001")
        self.assertEqual(self.station(view, "dev")["tokens"]["total"], AGY["total"])


# ---------------- 右栏显示 ----------------

@needs_node()
class PanelText(unittest.TestCase):
    def test_node_rules(self):
        out = run_node("require('./tests/token_v2_panel_check.js')")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
