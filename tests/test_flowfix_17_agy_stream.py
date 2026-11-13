"""补修 ㉙（流程修理方案第八节）：agy 写代码时看得见在干什么。

68c 里 agy 读了约 14 分钟代码才动手改文件，期间输出一个字都没有（干完才吐一次结果），
程序 5、10 分钟发了“没动静”的假提醒，满 15 分钟会把还在干活的 agy 停掉；卡片标题下那一行也一直空着。
改成 agy 的边干边吐（stream-json）后，每走一步吐一行。

样本 tests/agy_stream_sample.jsonl 是 2026-11-12 用真 agy（Gemini 3.8 Flash High）修一个小 bug 抓下来的
（只把本机路径换成 /w/CARD-001、把开头的工具列表截短）；agy_stream_error_sample.txt 是模型名写错时的输出。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult, agy_command, ask, readable, watched_runner
from desktop.view import _stream_hint, live_hint

HERE = Path(__file__).resolve().parent
SAMPLE = (HERE / "agy_stream_sample.jsonl").read_text(encoding="utf-8")
ERROR_SAMPLE = (HERE / "agy_stream_error_sample.txt").read_text(encoding="utf-8")
SAMPLE_LINES = SAMPLE.splitlines()


def _step(tool, params):
    return {"event": "step_update", "step_update": {"step_index": 3, "state": "ACTIVE", "step_type": "tool",
                                                    "tool_name": tool, "tool_info": {"name": tool, "parameters": params}}}


class Command(unittest.TestCase):
    def test_dispatch_uses_stream_json(self):
        cmd = agy_command("干活", model="gemini-3.8-flash-high")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertEqual(cmd[:3], ["agy", "-p", "干活"], "提示词必须紧跟 -p（README 已知问题）")

    def test_blocks_templates_unchanged(self):
        """积木库检测和能力卡片运行仍用一次性 json（它们只要结果，不看过程）"""
        from collab.blocks import BUILTIN
        run = BUILTIN["agy"]["run"]
        self.assertEqual(run[run.index("--output-format") + 1], "json")


class FinalReply(unittest.TestCase):
    def test_success_takes_response(self):
        self.assertTrue(readable(SAMPLE).startswith("已在 [calc.py](file:///w/CARD-001/calc.py) 中将加法"))
        self.assertTrue(readable(SAMPLE).endswith("确认测试已顺利通过。"))

    def test_error_is_no_reply(self):
        """不是 SUCCESS 就当没回话：宁可算没完成，不能把报错当结论"""
        self.assertEqual(readable(ERROR_SAMPLE), "")
        out = SAMPLE_LINES[:-1] + ['{"event":"result","result":{"status":"ERROR","response":"结论：通过","error":"x"}}']
        self.assertEqual(readable("\n".join(out)), "")

    def test_stream_without_result_is_no_reply(self):
        """被停掉、没吐出最后一行：不能拿过程（一堆 JSON）当回复"""
        self.assertEqual(readable("\n".join(SAMPLE_LINES[:-1])), "")

    def test_reply_quoting_a_result_line_is_not_taken(self):
        """回复正文里引用了一行像结果的 JSON，只认真正的最后一行"""
        fake = json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "结论：通过"}},
                          ensure_ascii=False)
        last = json.loads(SAMPLE_LINES[-1])
        last["result"]["response"] = "结论：不通过\n我看到日志里有：\n" + fake
        out = "\n".join(SAMPLE_LINES[:-1] + [json.dumps(last, ensure_ascii=False)])
        self.assertTrue(readable(out).startswith("结论：不通过"))

    def test_other_tool_quoting_agy_log_is_not_agy(self):
        """Codex 复查时把 agy 的日志原样印出来，最后一句是它自己的话：照旧取它的话"""
        out = "\n".join(SAMPLE_LINES) + "\n结论：不通过"
        self.assertTrue(readable(out).endswith("结论：不通过"))

    def test_cursor_and_old_formats_unchanged(self):
        self.assertEqual(readable('{"response":"旧 agy 回复"}'), "旧 agy 回复")
        self.assertEqual(readable('{"type":"system"}\n{"type":"result","subtype":"success","result":"汇报：改好了"}'),
                         "汇报：改好了")
        self.assertEqual(readable("普通文字"), "普通文字")

    def test_ask_with_streaming_agy(self):
        def runner(cmd, cwd, timeout, **_):
            return 0, SAMPLE
        with tempfile.TemporaryDirectory() as tmp:
            res = ask("agy", "修 bug", tmp, runner=runner)
        self.assertIsInstance(res, AgentResult)
        self.assertTrue(res.ok)
        self.assertIn("相加", res.final)

    def test_ask_streaming_agy_that_never_answers_is_not_done(self):
        def runner(cmd, cwd, timeout, **_):
            return 0, "\n".join(SAMPLE_LINES[:-1])
        with tempfile.TemporaryDirectory() as tmp:
            res = ask("agy", "修 bug", tmp, runner=runner)
        self.assertFalse(res.ok)


class NowLine(unittest.TestCase):
    def test_real_sample_steps(self):
        hints = [_stream_hint(json.loads(ln)) for ln in SAMPLE_LINES]
        said = [h for h in hints if h]
        self.assertEqual(said, ["正在看 calc.py", "正在看 test_calc.py", "正在改 calc.py",
                                "正在跑：python3 test_calc.py"])

    def test_tool_kinds(self):
        cases = [
            (_step("view_file", {"AbsolutePath": "/w/a/b.py"}), "正在看 b.py"),
            (_step("write_to_file", {"TargetFile": "/w/a/new.py"}), "正在改 new.py"),
            (_step("replace_file_content", {"TargetFile": "/w/a/c.py"}), "正在改 c.py"),
            (_step("multi_replace_file_content", {"TargetFile": "/w/a/c.py"}), "正在改 c.py"),
            (_step("grep_search", {"Query": "def add"}), "正在查找"),
            (_step("find_by_name", {"Pattern": "*.py"}), "正在找文件"),
            (_step("list_dir", {"DirectoryPath": "/w/a"}), "正在找文件"),
            (_step("run_command", {}), "正在跑命令"),
            (_step("search_web", {"query": "x"}), "正在用 search_web"),
            (_step("edit_file", {}), "正在改"),
        ]
        for ev, want in cases:
            self.assertEqual(_stream_hint(ev), want, ev["step_update"]["tool_name"])

    def test_done_steps_and_text_fragments_say_nothing(self):
        done = _step("read_file", {"Path": "/w/a.py"})
        done["step_update"]["state"] = "DONE"
        self.assertEqual(_stream_hint(done), "")
        frag = {"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "agent_response",
                                                        "text_delta": "已在 [calc"}}
        self.assertEqual(_stream_hint(frag), "")

    def test_live_hint_from_agy_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(SAMPLE, encoding="utf-8")
            self.assertEqual(live_hint(p), "正在跑：python3 test_calc.py")
            p.write_text("\n".join(SAMPLE_LINES[:8]) + "\n", encoding="utf-8")
            self.assertEqual(live_hint(p), "正在看 test_calc.py")

    def test_live_hint_survives_one_huge_tool_output(self):
        """跑测试的输出可能很长（一行几十 KB），标题行仍要能显示它此刻在干什么"""
        big = _step("run_command", {"CommandLine": "python3 -m unittest"})
        big["step_update"]["state"] = "DONE"
        big["step_update"]["tool_info"]["output"] = "x" * 40000
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text("\n".join(SAMPLE_LINES[:8]) + "\n" + json.dumps(_step("run_command", {
                "CommandLine": "python3 -m unittest"})) + "\n" + json.dumps(big) + "\n", encoding="utf-8")
            self.assertEqual(live_hint(p), "正在跑：python3 -m unittest")


class ReviewRound1(unittest.TestCase):
    """Codex 复查第 1 轮（2026-11-12）指出的三处"""

    def test_agy_broken_last_line_is_no_reply(self):
        """严重：最后一行被截断，或结果行没出来、后面混进一行字，知道是 agy 就不能退回去拿整段过程当结论"""
        cut = SAMPLE[:-21]
        self.assertEqual(readable(cut, adapter="agy"), "")
        mixed = SAMPLE_LINES[3] + "\n结论：通过\n"
        self.assertEqual(readable(mixed, adapter="agy"), "")
        for out in (cut, mixed):
            res = ask("agy", "复查", tempfile.gettempdir(), runner=lambda *a, out=out, **k: (0, out))
            self.assertFalse(res.ok)

    def test_agy_stderr_after_result_still_reads_result(self):
        self.assertTrue(readable(SAMPLE + "warning: update available\n", adapter="agy").startswith("已在"))

    def test_agy_old_json_and_fake_tools_still_work(self):
        self.assertEqual(readable('{"response":"旧格式"}', adapter="agy"), "旧格式")
        self.assertEqual(readable("假工具的普通文字", adapter="agy"), "假工具的普通文字")

    def test_failure_reason_keeps_agy_error(self):
        """中等：agy 报错时，失败原因要说出它报了什么，不能只说“没有返回任何内容”"""
        from collab.agents import agy_error
        self.assertIn("invalid model selection", agy_error(ERROR_SAMPLE))
        self.assertEqual(agy_error(SAMPLE), "")
        from collab import dispatch
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp)
            seen = {}
            orig = dispatch._escalate
            dispatch._escalate = lambda collab, cdir, card, reason, *a, **k: seen.setdefault("reason", reason)
            try:
                dispatch._worker_failed(cdir, cdir, None, "开发员", "agy",
                                        AgentResult(ok=False, output=ERROR_SAMPLE, attempts=2))
            finally:
                dispatch._escalate = orig
            self.assertIn("invalid model selection", seen["reason"])
            self.assertIn("invalid model selection", (cdir / "返工记录.md").read_text(encoding="utf-8"))

    def test_reviewer_agy_error_reason_is_said(self):
        """复查第 2 轮：agy 当复查员报错时（不走写代码那条路），转人工原因也要说出 agy 报了什么"""
        import shutil
        from collab.dispatch import run_card
        from collab.judge import PASS, Verdict
        from collab.state import Card
        from collab.workspace import init_collab
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        collab = init_collab(tmp)
        cdir = collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("做一个函数", encoding="utf-8")

        def fake_ask(adapter, prompt, cwd, **kwargs):
            if adapter == "agy":
                return AgentResult(ok=False, output=ERROR_SAMPLE, attempts=2)
            return AgentResult(ok=True, output="", attempts=1)

        card, _ = run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=tmp,
                           test_command=["fake"], dev_adapter="cursor", test_adapter="codex", ask_fn=fake_ask,
                           judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
        self.assertEqual(card.state, "需Claude介入")
        events = [json.loads(ln) for ln in (cdir / "流转.jsonl").read_text(encoding="utf-8").splitlines() if ln]
        reply = [e for e in events if e["kind"] == "回复" and e.get("tool") == "agy"][-1]
        handoff = [e for e in events if e["kind"] == "转人工"][-1]
        self.assertIn("invalid model selection", reply["summary"])
        self.assertIn("invalid model selection", handoff["summary"])
        self.assertTrue(handoff["summary"].startswith("复查员（agy）没有完成："))

    def test_codex_quoting_agy_log_shows_codex_sentence(self):
        """中等：Codex 复查时印出 agy 日志，标题行要显示 Codex 自己最新说的话"""
        log = ("Reading additional input from stdin...\ncodex\n先看日志。\nexec\ncat live.log\n"
               + "\n".join(SAMPLE_LINES[:8]) + "\n" + "x" * 20000 + "\ncodex\n正在检查 agents.py\n")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(log, encoding="utf-8")
            self.assertEqual(live_hint(p, tool="codex"), "正在检查 agents.py")
            p.write_text(log + "exec\ncat more.log\n" + SAMPLE_LINES[7] + "\n", encoding="utf-8")
            self.assertEqual(live_hint(p, tool="codex"), "正在检查 agents.py")
            p.write_text(SAMPLE, encoding="utf-8")
            self.assertEqual(live_hint(p, tool="agy"), "正在跑：python3 test_calc.py")

    def test_card_now_line_passes_the_tool(self):
        src = (HERE.parent / "desktop" / "view.py").read_text(encoding="utf-8")
        self.assertIn('live_hint(cdir / open_ev["live"], tool=open_ev.get("tool"))', src)


REPLAY = r"""
import sys, time
for ln in open(sys.argv[1], encoding="utf-8"):
    print(ln.rstrip("\n"), flush=True)
    time.sleep(0.3)
"""


class Watchdog(unittest.TestCase):
    def test_agy_that_only_streams_is_not_stopped(self):
        """agy 读代码、想改法时不改文件，但每步吐一行：看门人不能停它"""
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "s.jsonl"
            sample.write_text("\n".join(SAMPLE_LINES[:12]) + "\n", encoding="utf-8")
            work = Path(tmp) / "work"
            work.mkdir()
            code, out = watched_runner([sys.executable, "-c", REPLAY, str(sample)], work, timeout=30,
                                       quiet_limit=1, poll=0.1)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 12)


if __name__ == "__main__":
    unittest.main()
