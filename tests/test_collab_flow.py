"""流转记录：协作包边干边记，桌面窗口靠它实时显示谁在干、到哪一步、彼此说了什么。"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collab.agents import AgentResult, watched_runner
from collab.cli import main
from collab.dispatch import run_card
from collab.flow import read_flow, record, text_of, working_now
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from collab.workspace import init_collab


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cdir = Path(self.tmp.name) / "CARD-001"

    def tearDown(self):
        self.tmp.cleanup()

    def test_events_are_numbered_in_order_and_read_back(self):
        record(self.cdir, "需求", src="你", dst="调度员", summary="加问候")
        record(self.cdir, "批准", src="你", dst="调度员", summary="批准方案")
        events = read_flow(self.cdir)
        self.assertEqual([e["seq"] for e in events], [1, 2])
        self.assertEqual([e["kind"] for e in events], ["需求", "批准"])
        self.assertIn("time", events[0])

    def test_full_text_is_saved_beside_the_record(self):
        ev = record(self.cdir, "派活", src="调度员", dst="测试员", summary="写测试", text="完整的派活原文")
        self.assertEqual(text_of(self.cdir, ev), "完整的派活原文")

    def test_live_output_file_is_reserved_when_asked(self):
        ev = record(self.cdir, "派活", src="调度员", dst="开发员", summary="写代码", live=True)
        self.assertTrue((self.cdir / ev["live"]).exists())

    def test_reading_a_card_without_records_gives_empty_list(self):
        self.assertEqual(read_flow(self.cdir), [])

    def test_a_damaged_line_is_skipped_not_fatal(self):
        record(self.cdir, "需求", summary="a")
        with (self.cdir / "流转.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("{坏掉的一行\n")
        record(self.cdir, "批准", summary="b")
        self.assertEqual([e["kind"] for e in read_flow(self.cdir)], ["需求", "批准"])

    def test_working_now_is_the_open_dispatch(self):
        first = record(self.cdir, "派活", src="调度员", dst="测试员", tool="codex", summary="写测试")
        self.assertEqual(working_now(read_flow(self.cdir))["seq"], first["seq"])
        record(self.cdir, "回复", src="测试员", dst="调度员", reply_to=first["seq"], ok=True)
        self.assertIsNone(working_now(read_flow(self.cdir)))


class _Script:
    """假执行者：记录收到的参数；在干活时顺便看一眼流转记录，验证“正在干”能被读出来。"""

    def __init__(self, cdir, verdicts, outputs=None):
        self.cdir = cdir
        self.verdicts = list(verdicts)
        self.outputs = outputs or {}
        self.seen_working = []
        self.live_paths = []

    def ask(self, adapter, prompt, cwd, **kwargs):
        now = working_now(read_flow(self.cdir))
        self.seen_working.append((now or {}).get("dst"))
        live = kwargs.get("live_path")
        self.live_paths.append(live)
        if live:
            Path(live).write_text(f"{adapter} 正在干活\n", encoding="utf-8")
        return AgentResult(ok=True, output=self.outputs.get(adapter, f"{adapter} 的回复"), attempts=1)

    def judge(self, command, cwd, timeout):
        result = self.verdicts.pop(0)
        return Verdict(result=result, returncode=0 if result == PASS else 1, output=f"测试输出-{result}")


class DispatchFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.collab = init_collab(self.tmp.name)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("做一个函数", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script, **extra):
        card = Card("CARD-001", state="待派发")
        run_card(card, collab_dir=self.collab, worktree=self.tmp.name, test_command=["t"],
                 dev_adapter="cursor", test_adapter="codex", ask_fn=script.ask,
                 judge_fn=script.judge, **extra)
        return card

    def test_a_passing_run_is_recorded_step_by_step(self):
        script = _Script(self.cdir, [PASS], outputs={"agy": "结论：通过\n改动符合需求"})
        self._run(script, reviewer_adapter="agy", allowed=["*"], changed_fn=lambda w: ["a.py", "b.py"])
        events = read_flow(self.cdir)
        self.assertEqual([(e["kind"], e.get("dst") or e.get("src")) for e in events], [
            ("派活", "测试员"), ("回复", "调度员"),
            ("派活", "开发员"), ("回复", "调度员"),
            ("范围", "程序"), ("判定", "程序"),
            ("派活", "复查员"), ("回复", "调度员"),
            ("结束", "你"),  # 做完了交到你手上验收
        ])
        self.assertIn("2 个文件", events[4]["summary"])
        self.assertEqual(events[5]["result"], PASS)
        self.assertEqual(events[7]["summary"], "结论：通过")
        self.assertIn("等你验收", events[8]["summary"])

    def test_prompts_and_replies_are_kept_in_full(self):
        script = _Script(self.cdir, [PASS], outputs={"cursor": "改好了\n汇报：新增函数"})
        self._run(script)
        events = read_flow(self.cdir)
        dispatch_dev = [e for e in events if e["kind"] == "派活" and e["dst"] == "开发员"][0]
        reply_dev = [e for e in events if e["kind"] == "回复" and e["src"] == "开发员"][0]
        self.assertIn("测试已经写好", text_of(self.cdir, dispatch_dev))
        self.assertIn("改好了", text_of(self.cdir, reply_dev))
        self.assertEqual(reply_dev["summary"], "汇报：新增函数")
        self.assertEqual(reply_dev["reply_to"], dispatch_dev["seq"])
        self.assertTrue(reply_dev["ok"])
        self.assertIn("seconds", reply_dev)
        self.assertEqual(dispatch_dev["tool"], "cursor")

    def test_tester_quoting_the_rules_does_not_become_its_summary(self):
        """实跑时测试员读规则时引用了“汇报：……”那句，被错当成它的汇报；只有开发员才找“汇报：”。"""
        quoted = "读了规则：开发员最后一行写“汇报：”加不超过 100 字的说明。\n写好 10 条测试，覆盖英文和默认中文。"
        script = _Script(self.cdir, [PASS], outputs={"codex": quoted, "cursor": "改好了\n汇报：greet 加了 lang"})
        self._run(script)
        replies = {e["src"]: e["summary"] for e in read_flow(self.cdir) if e["kind"] == "回复"}
        self.assertEqual(replies["测试员"], "写好 10 条测试，覆盖英文和默认中文。")
        self.assertEqual(replies["开发员"], "汇报：greet 加了 lang")

    def test_who_is_working_can_be_read_while_they_work(self):
        script = _Script(self.cdir, [PASS])
        self._run(script)
        self.assertEqual(script.seen_working, ["测试员", "开发员"])

    def test_live_output_lands_in_the_card_folder(self):
        script = _Script(self.cdir, [PASS])
        self._run(script)
        dispatch_test = [e for e in read_flow(self.cdir) if e["kind"] == "派活"][0]
        self.assertEqual(Path(script.live_paths[0]), self.cdir / dispatch_test["live"])
        self.assertEqual((self.cdir / dispatch_test["live"]).read_text(encoding="utf-8"), "codex 正在干活\n")

    def test_failed_test_then_rework_is_visible(self):
        script = _Script(self.cdir, [FAIL, PASS])
        card = self._run(script)
        events = read_flow(self.cdir)
        verdicts = [e for e in events if e["kind"] == "判定"]
        self.assertEqual([v["result"] for v in verdicts], [FAIL, PASS])
        self.assertEqual(verdicts[0]["dst"], "开发员")
        self.assertEqual(text_of(self.cdir, verdicts[0]), "测试输出-没过")
        rework = [e for e in events if e["kind"] == "派活" and e.get("step") == "返工修改"]
        self.assertEqual(len(rework), 1)
        self.assertEqual(rework[0]["round"], 2)
        self.assertEqual(card.state, "待验收")

    def test_worker_failure_is_recorded_and_escalated(self):
        def ask(adapter, prompt, cwd, **kw):
            return AgentResult(ok=False, output="看门人：超过 15 分钟没有任何文件变化，已停止", attempts=1)
        card = Card("CARD-001", state="待派发")
        run_card(card, collab_dir=self.collab, worktree=self.tmp.name, test_command=["t"],
                 dev_adapter="cursor", test_adapter="codex", ask_fn=ask, judge_fn=None)
        events = read_flow(self.cdir)
        self.assertEqual([e["kind"] for e in events], ["派活", "回复", "转人工"])
        self.assertFalse(events[1]["ok"])
        self.assertIn("看门人", events[2]["summary"])

    def test_review_rejection_is_escalated_with_reason(self):
        script = _Script(self.cdir, [PASS], outputs={"agy": "结论：不通过\n改错了地方"})
        card = self._run(script, reviewer_adapter="agy")
        events = read_flow(self.cdir)
        self.assertEqual(events[-1]["kind"], "转人工")
        self.assertIn("复查", events[-1]["summary"])
        self.assertEqual(card.state, "需Claude介入")

    def test_out_of_scope_revert_is_recorded(self):
        changes = iter([["src/a.py", "README.md"], ["src/a.py"]])
        script = _Script(self.cdir, [PASS])
        self._run(script, allowed=["src/*"], changed_fn=lambda w: next(changes),
                  revert_fn=lambda w, paths: None)
        scope = [e for e in read_flow(self.cdir) if e["kind"] == "范围"][0]
        self.assertIn("README.md", scope["summary"])
        self.assertIn("撤销", scope["summary"])

    def test_resume_skips_completed_tester_step(self):
        """上次写测试已交活、在写代码时中断：续跑时直接跳过测试员，从写代码接着往下跑。"""
        d1 = record(self.cdir, "派活", src="调度员", dst="测试员", tool="codex", step="写测试", round=1)
        record(self.cdir, "回复", src="测试员", dst="调度员", tool="codex", step="写测试",
               round=1, ok=True, seconds=600, reply_to=d1["seq"], summary="已写好测试")
        record(self.cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1)

        called_roles = []
        def ask(adapter, prompt, cwd, **kw):
            called_roles.append(adapter)
            return AgentResult(ok=True, output="汇报：修复完成", attempts=1)

        card = Card("CARD-001", state="开发中")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.tmp.name, test_command=["t"],
                           dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                           judge_fn=lambda cmd, **kw: Verdict(PASS, 0, "ok"))
        self.assertEqual(called_roles, ["cursor"])  # 没再叫 codex 写测试
        self.assertEqual(card.state, "待验收")


class CliFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        init_collab(self.project)
        self.req = self.project / "需求.md"
        self.req.write_text("# 加一个早上好问候\n\n细节……", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            return main(list(argv)), buf.getvalue()

    def test_new_card_records_the_requirement(self):
        code, out = self._cli("new-card", str(self.project), "CARD-001", "--requirement", str(self.req))
        self.assertEqual(code, 0, out)
        events = read_flow(self.project / ".collab" / "cards" / "CARD-001")
        self.assertEqual(events[0]["kind"], "需求")
        self.assertEqual(events[0]["summary"], "加一个早上好问候")
        self.assertIn("细节", text_of(self.project / ".collab" / "cards" / "CARD-001", events[0]))

    def test_approve_records_the_decision(self):
        self._cli("new-card", str(self.project), "CARD-001", "--requirement", str(self.req))
        cdir = self.project / ".collab" / "cards" / "CARD-001"
        (cdir / "方案.md").write_text("# 方案\n两步", encoding="utf-8")
        (self.project / ".collab" / "配置.json").write_text('{"project_profile_confirmed": true}', encoding="utf-8")
        code, out = self._cli("approve", str(self.project), "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertEqual(read_flow(cdir)[-1]["kind"], "批准")


class LiveOutputRunnerTest(unittest.TestCase):
    def test_runner_writes_output_to_the_live_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "实时输出.log"
            code, out = watched_runner([sys.executable, "-c", "print('第一行'); print('第二行')"],
                                       cwd=tmp, timeout=30, poll=0.05, live_path=str(live))
            self.assertEqual(code, 0)
            self.assertIn("第二行", out)
            self.assertIn("第一行", live.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
