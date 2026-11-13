"""流程修理第二轮 A 组：右栏每一步都写“工具 · 模型（强度）”和计时（㉗ ㊳），说明大白话、最多 3 行、不放命令代码路径（㊷）。

用户 11-13：“每一步右上角都写哪个工具 · 哪个模型（强度）”“最多三行字，涵盖现在在干嘛，自然语言大白话”“放这么多代码对我意义不大”。
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from collab.config import model_label
from collab.flow import record
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view, live_hint

ROOT = Path(__file__).resolve().parent.parent


def _station(view, key):
    return [s for s in view["stations"] if s["key"] == key][0]


def _agy(step_index, name, params, state="ACTIVE"):
    return json.dumps({"event": "step_update", "step_update": {
        "conversation_id": "c", "step_index": step_index, "state": state, "step_type": "tool",
        "tool_name": name, "tool_info": {"name": name, "parameters": params}}}, ensure_ascii=False)


# 69 里 agy 真实吐出的样子：读、改、跑本卡测试、跑自己写的小脚本，最后一行是带整段输出的“用完”
AGY_LIVE = "\n".join([
    '{"event":"init","conversation_id":"c","init":{"model":"gemini-3.8-flash-high"}}',
    _agy(1, "read_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/clerk/tasks.py"}),
    _agy(1, "read_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/clerk/tasks.py"}, "DONE"),
    _agy(2, "read_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/server.py"}),
    _agy(3, "read_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/clerk/tasks.py"}),
    _agy(4, "edit_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/server.py"}),
    _agy(5, "edit_file", {"Path": "/Users/x/super-agent-worktrees/CARD-003/desktop/server.py"}),
]) + "\n"


class ModelLabel(unittest.TestCase):
    def test_codex_with_effort(self):
        self.assertEqual(model_label("codex", {"codex": "gpt-6.1-sol", "codex_effort": "low"}), "GPT-6.1-Sol（低）")
        self.assertEqual(model_label("codex", {"codex": "gpt-6.1-sol", "codex_effort": "xhigh"}), "GPT-6.1-Sol（极高）")

    def test_agy_and_cursor(self):
        self.assertEqual(model_label("agy", {"agy": "gemini-3.8-flash-high"}), "Gemini 3.8 Flash（高）")
        self.assertEqual(model_label("agy", {"agy": "gemini-4-argon-high"}), "Gemini 4 Argon（高）")
        self.assertEqual(model_label("cursor", {"cursor": "auto"}), "Auto")

    def test_unknown_model_keeps_its_name(self):
        self.assertEqual(model_label("agy", {"agy": "some-new-model"}), "some-new-model")
        self.assertEqual(model_label("agy", {}), "")
        self.assertEqual(model_label("", {"agy": "x"}), "")


class PlainHint(unittest.TestCase):
    """㊷：说明是大白话，不出现命令、代码、文件路径、原始输出。"""

    def _hint(self, text, tool="agy", test_command=""):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(text, encoding="utf-8")
            return live_hint(p, tool=tool, test_command=test_command)

    def test_reading_and_editing_counts_without_file_names(self):
        hint = self._hint(AGY_LIVE)
        self.assertEqual(hint, "在改代码（已读 2 个文件、改了 2 处）")

    def test_running_card_tests(self):
        cmd = "for p in 'test_acceptance_69_*.py'; do python3 -m unittest discover -s tests -p \"$p\" || exit 1; done"
        hint = self._hint(AGY_LIVE + _agy(6, "run_command", {"CommandLine": cmd}) + "\n", test_command=cmd)
        self.assertTrue(hint.startswith("在跑本卡测试"), hint)
        one = self._hint(AGY_LIVE + _agy(6, "run_command", {
            "CommandLine": "python3 -m unittest discover -s tests -p test_acceptance_69_ability_draft.py -k x"}) + "\n")
        self.assertTrue(one.startswith("在跑测试"), one)

    def test_running_own_script(self):
        hint = self._hint(AGY_LIVE + _agy(6, "run_command", {
            "CommandLine": "python3 -c \"\nimport tempfile\nfrom desktop.server import Handler\n\""}) + "\n")
        self.assertTrue(hint.startswith("在跑一段自己写的验证小程序"), hint)
        other = self._hint(AGY_LIVE + _agy(6, "run_command", {"CommandLine": "ls -la desktop"}) + "\n")
        self.assertTrue(other.startswith("在跑命令"), other)

    def test_finished_step_with_huge_output_is_not_shown(self):
        done = json.dumps({"event": "step_update", "step_update": {
            "step_index": 7, "state": "DONE", "step_type": "tool", "tool_name": "run_command",
            "tool_info": {"name": "run_command", "parameters": {"CommandLine": "python3 x.py"}},
            "output": "Created At: 2026-11-13T10:46:49\\nCommand output:\\n def _blocks_proposal(self):"}})
        hint = self._hint(AGY_LIVE + done + "\n")
        self.assertEqual(hint, "在改代码（已读 2 个文件、改了 2 处）")

    def test_cursor_shell_and_read(self):
        stream = "\n".join([
            '{"type":"tool_call","subtype":"started","tool_call":{"readToolCall":{"args":{"path":"/w/CARD-001/calc.py"}}}}',
            '{"type":"tool_call","subtype":"started","tool_call":{"shellToolCall":{"args":{"command":"ls -la"}}}}',
        ]) + "\n"
        hint = self._hint(stream, tool="cursor")
        self.assertTrue(hint.startswith("在跑命令"), hint)
        self.assertNotIn("calc.py", hint)
        self.assertNotIn("ls", hint)

    def test_what_the_worker_says_is_kept(self):
        codex = "codex\n先读规则和任务卡片。\nexec\ncat a.py\ncodex\n测试已补齐，接着查草稿不落库。\n"
        self.assertEqual(self._hint(codex, tool="codex"), "测试已补齐，接着查草稿不落库。")


class StationsShowModelAndTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        self.collab = init_collab(self.project)
        (self.project.parent / "项目-worktrees" / "CARD-003").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-003"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 69 管家起草能力卡片\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_worker_step_shows_model(self):
        Card("CARD-003", state="开发中").save(self.cdir / "状态.json")
        record(self.cdir, "需求", src="你", dst="调度员", summary="69")
        record(self.cdir, "写方案", src="程序", dst="Codex", summary="开始写方案", model="GPT-6.1-Sol（低）")
        record(self.cdir, "方案", src="Codex", dst="你", summary="按施工单 69 补齐后台能力")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "agy", "复查员": "codex"},
               models={"测试员": "GPT-6.1-Sol（高）", "开发员": "Gemini 3.8 Flash（高）", "复查员": "GPT-6.1-Sol（极高）"})
        sent = record(self.cdir, "派活", src="调度员", dst="开发员", tool="agy", step="开发", round=1,
                      summary="按测试写代码", live=True)
        (self.cdir / sent["live"]).write_text(AGY_LIVE, encoding="utf-8")
        view = card_view(self.project)
        shown = {k: (_station(view, k)["who"], _station(view, k)["model"]) for k in ("disp", "test", "dev", "rev")}
        self.assertEqual(shown, {"disp": ("Codex", "GPT-6.1-Sol（低）"), "test": ("Codex", "GPT-6.1-Sol（高）"),
                                 "dev": ("agy", "Gemini 3.8 Flash（高）"), "rev": ("Codex", "GPT-6.1-Sol（极高）")})
        dev = _station(view, "dev")
        self.assertEqual(dev["detail"], "在改代码（已读 2 个文件、改了 2 处）")
        self.assertIsNotNone(dev["elapsed"])

    def test_old_cards_without_models_keep_tool_name(self):
        Card("CARD-003", state="开发中").save(self.cdir / "状态.json")
        record(self.cdir, "需求", src="你", dst="调度员", summary="69")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "agy", "复查员": "codex"})
        view = card_view(self.project)
        self.assertEqual(_station(view, "dev")["who"], "agy")
        self.assertEqual(_station(view, "dev")["model"], "")

    def test_dispatcher_writing_plan_has_timer(self):
        """㊳：调度写方案那步也要有“已干多久 · 多久前有动静”。"""
        Card("CARD-003", state="待对齐").save(self.cdir / "状态.json")
        (self.collab / "后台").mkdir(exist_ok=True)
        (self.collab / "后台" / "plan.json").write_text(
            json.dumps({"card": "CARD-003", "pid": os.getpid(), "started": ""}), encoding="utf-8")
        record(self.cdir, "需求", src="你", dst="调度员", summary="69")
        record(self.cdir, "写方案", src="程序", dst="Codex", summary="开始写方案", pid=os.getpid(),
               started="", model="GPT-6.1-Sol（低）")
        view = card_view(self.project)
        disp = _station(view, "disp")
        self.assertEqual(disp["state"], "active")
        self.assertEqual((disp["who"], disp["model"]), ("Codex", "GPT-6.1-Sol（低）"))
        self.assertTrue(view["live"], "标题下那一行也要计时（复查 A 第 6 条）")
        self.assertIsNotNone(view["worker_elapsed"])
        self.assertIsNotNone(disp["elapsed"])
        self.assertIsNotNone(disp["quiet"])


class ReviewRound1A(StationsShowModelAndTime):
    """Codex 复查第 1 轮 A 块。"""

    def _working(self, *extra):
        Card("CARD-003", state="开发中").save(self.cdir / "状态.json")
        record(self.cdir, "需求", src="你", dst="调度员", summary="69")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "agy", "复查员": "codex"})
        for kind, kw in extra:
            record(self.cdir, kind, **kw)

    def test_click_step_still_finds_handoffs_by_tool(self):
        panel = (ROOT / "desktop" / "static" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('s.who + (s.model ? " · " + s.model : "")', panel)
        self.assertIn("showLog(s.key === \"check\" || s.key === \"you\" || s.key === \"accept\" ? null : s.who)", panel)

    def test_new_round_hides_last_round_results(self):
        self._working(
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
            ("回复", dict(src="开发员", dst="调度员", tool="agy", step="开发", ok=True, summary="汇报：改了")),
            ("判定", dict(src="程序", result="过", summary="第 1 轮：测试全部通过", round=1)),
            ("派活", dict(src="调度员", dst="复查员", tool="codex", step="复查", summary="复查", round=1)),
            ("回复", dict(src="复查员", dst="调度员", tool="codex", step="复查", ok=True, summary="结论：不通过")),
            ("退回", dict(src="程序", dst="开发员", summary="退回")),
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
        )
        view = card_view(self.project)
        self.assertEqual(_station(view, "rev")["state"], "idle")
        self.assertEqual(_station(view, "check")["state"], "idle")
        self.assertIn("交接", _station(view, "rev")["detail"])

    def test_other_dispatcher_shows_its_own_model(self):
        """别家调度员不安 Codex 的模型（第 1 轮 A7），也不留空：用团队设置里它自己的（第 2 轮 A5）"""
        from collab.config import save_role_models, save_team
        save_team(self.project, {"dispatcher": "agy", "tester": "codex", "developer": "agy", "reviewer": "codex"})
        save_role_models(self.project, {"dispatcher": {"tool": "agy", "model": "gemini-3.8-flash-low"}})
        Card("CARD-003", state="待派发").save(self.cdir / "状态.json")
        record(self.cdir, "写方案", src="程序", dst="Codex", summary="开始写方案", model="GPT-6.1-Sol（低）")
        view = card_view(self.project, dispatcher="agy")
        self.assertEqual(_station(view, "disp")["model"], "Gemini 3.8 Flash（低）")

    def test_after_rework_handed_in_old_review_stays_hidden(self):
        """第 2 轮 A2：写代码的交了活、复查还没出新结论时，上一轮的红叉也不能冒回来"""
        self._working(
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
            ("回复", dict(src="开发员", dst="调度员", tool="agy", step="开发", ok=True, summary="汇报：改了")),
            ("判定", dict(src="程序", result="过", summary="第 1 轮：测试全部通过", round=1)),
            ("派活", dict(src="调度员", dst="复查员", tool="codex", step="复查", summary="复查", round=1)),
            ("回复", dict(src="复查员", dst="调度员", tool="codex", step="复查", ok=True, summary="结论：不通过")),
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
            ("回复", dict(src="开发员", dst="调度员", tool="agy", step="开发", ok=True, summary="汇报：又改了")),
        )
        view = card_view(self.project)
        self.assertNotEqual(_station(view, "rev")["state"], "fail")

    def test_cursor_strength_from_model_id(self):
        self.assertTrue(model_label("cursor", {"cursor": "claude-opus-5-thinking-high"}).endswith("（高）"))
        self.assertTrue(model_label("cursor", {"cursor": "gpt-5.6-sol-xhigh-fast"}).endswith("（极高）")
                        or "Extra High" in model_label("cursor", {"cursor": "gpt-5.6-sol-xhigh-fast"}))

    def test_cursor_edits_without_ids_all_count(self):
        line = '{"type":"tool_call","subtype":"started","tool_call":{"editToolCall":{"args":{"path":"/w/a.py"}}}}'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text("\n".join([line] * 20) + "\n", encoding="utf-8")
            self.assertEqual(live_hint(p, tool="cursor"), "在改代码（改了 20 处）")


class ReviewRound3A(ReviewRound1A):
    def test_stopped_rework_keeps_old_results_hidden(self):
        """第 3 轮 A1：返工中点了停下，上一轮结果也不冒回来"""
        self._working(
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
            ("回复", dict(src="开发员", dst="调度员", tool="agy", step="开发", ok=True, summary="汇报：改了")),
            ("判定", dict(src="程序", result="过", summary="第 1 轮：测试全部通过", round=1)),
            ("派活", dict(src="调度员", dst="复查员", tool="codex", step="复查", summary="复查", round=1)),
            ("回复", dict(src="复查员", dst="调度员", tool="codex", step="复查", ok=True, summary="结论：不通过")),
            ("派活", dict(src="调度员", dst="开发员", tool="agy", step="开发", summary="写", round=1)),
        )
        events = (self.cdir / "流转.jsonl").read_text(encoding="utf-8").splitlines()
        first = json.loads(events[1])
        first["pid"] = 999999  # 施工进程没了 = 中断
        events[1] = json.dumps(first, ensure_ascii=False)
        (self.cdir / "流转.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")
        view = card_view(self.project)
        self.assertNotEqual(_station(view, "rev")["state"], "fail")
        self.assertNotEqual(_station(view, "check")["state"], "done")


class WalkFixes(unittest.TestCase):
    """第二轮真实窗口走查（用户截图）：计时另起一行；右上角工具和模型写全，不用省略号。"""

    def test_clock_on_its_own_line(self):
        js = (ROOT / "desktop" / "static" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('<span class="k"></span>', js)
        part = js.split("function stepText(s)")[1].split("}")[0]
        self.assertNotIn("已干", part, "说明那行不带计时")
        self.assertIn('querySelector(".k").textContent = clockText(s)', js)

    def test_who_is_not_cut(self):
        css = (ROOT / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")
        rule = css.split("\n.w {")[1].split("}")[0]
        self.assertNotIn("ellipsis", rule)
        self.assertNotIn("nowrap", rule)


class ThreeLines(unittest.TestCase):
    def test_step_detail_and_now_line_clamped_to_three_lines(self):
        panel = (ROOT / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")
        chat = (ROOT / "desktop" / "static" / "chat.css").read_text(encoding="utf-8")
        self.assertRegex(panel, r"\.step \.d\s*\{[^}]*-webkit-line-clamp:\s*3")
        self.assertRegex(chat, r"-webkit-line-clamp:\s*3")


if __name__ == "__main__":
    unittest.main()
