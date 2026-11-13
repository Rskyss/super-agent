"""流程修理第四轮 任务 3（59 后半）：复查的同时程序跑全量，改坏老功能自动退回写代码的。"""
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow
from collab.fullrun import STOPPED, culprits, failing_files, run_command, safe_env
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict
from collab.state import Card

FAIL_OUT = ("FAIL: test_a (test_old.T)\n----\nTraceback\n"
            "Ran 10 tests in 1s\n\nFAILED (failures=1)\n")


class FakeRunner:
    def __init__(self, runs, base=None, delay=0.0):
        self.runs = list(runs)
        self.base_result = base or {}
        self.delay = delay
        self.calls = 0
        self.stopped = 0

    def run(self, worktree, stop=None):
        self.calls += 1
        end = time.time() + self.delay
        while time.time() < end:
            if stop is not None and stop.is_set():
                self.stopped += 1
                return Verdict(STOPPED, None, "")
            time.sleep(0.01)
        return self.runs.pop(0) if self.runs else Verdict(PASS, 0, "Ran 10 tests\n\nOK\n")

    def base(self, worktree, files, stop=None):
        return {f: self.base_result.get(f, Verdict(PASS, 0, "OK")) for f in files}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("资料\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, runner, reviews=("结论：通过",), reviewer="codex"):
        reviews = list(reviews)
        calls = {"dev": [], "review": 0}
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"] += 1
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0) if reviews else "结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_new.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter=reviewer, full_runner=runner)
        return card, calls

    def fulls(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "全量"]


class FullInFlow(Base):
    def test_pass(self):
        runner = FakeRunner([Verdict(PASS, 0, "Ran 10 tests\n\nOK\n")])
        card, calls = self.go(runner)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(runner.calls, 1)
        self.assertTrue(any(e.get("result") == PASS for e in self.fulls()))

    def test_broken_old_test_goes_back_to_dev(self):
        runner = FakeRunner([Verdict(FAIL, 1, FAIL_OUT), Verdict(PASS, 0, "OK")])
        card, calls = self.go(runner)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["dev"]), 2, "退回写代码的再改一次")
        self.assertIn("tests/test_old.py", calls["dev"][1])
        self.assertIn("改之前", calls["dev"][1])
        self.assertEqual(calls["review"], 2, "改完再复查")

    def test_already_broken_before_is_not_blamed(self):
        runner = FakeRunner([Verdict(FAIL, 1, FAIL_OUT)], base={"tests/test_old.py": Verdict(FAIL, 1, FAIL_OUT)})
        card, calls = self.go(runner)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["dev"]), 1)
        self.assertIn("改之前就不过", self.fulls()[-1]["summary"])

    def test_more_broken_in_same_file_is_blamed(self):
        now = FAIL_OUT.replace("FAIL: test_a (test_old.T)", "FAIL: test_a (test_old.T)\nFAIL: test_b (test_old.T)")
        runner = FakeRunner([Verdict(FAIL, 1, now), Verdict(PASS, 0, "OK")],
                            base={"tests/test_old.py": Verdict(FAIL, 1, FAIL_OUT)})
        card, calls = self.go(runner)
        self.assertEqual(len(calls["dev"]), 2)

    def test_timeout_stops_card(self):
        card, _ = self.go(FakeRunner([Verdict(TIMEOUT, None, "")]))
        self.assertEqual(card.state, "需Claude介入")

    def test_unreadable_failure_stops_card(self):
        card, _ = self.go(FakeRunner([Verdict(FAIL, 1, "乱七八糟")]))
        self.assertEqual(card.state, "需Claude介入")

    def test_review_reject_stops_full_run(self):
        runner = FakeRunner([], delay=5)
        start = time.time()
        card, calls = self.go(runner, reviews=["结论：不通过\n1. 【中等】按钮没反应。", "结论：通过"])
        self.assertGreaterEqual(runner.stopped, 1, "复查不通过，这一轮全量不用跑完")
        self.assertEqual(card.state, "待验收")

    def test_no_reviewer_still_runs(self):
        runner = FakeRunner([Verdict(PASS, 0, "OK")])
        card, _ = self.go(runner, reviewer=None)
        self.assertEqual(runner.calls, 1)
        self.assertEqual(card.state, "待验收")

    def test_not_python_project_skips(self):
        for p in (self.worktree / "tests").iterdir():
            p.unlink()
        (self.worktree / "tests").rmdir()
        runner = FakeRunner([])

        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "spec").mkdir(exist_ok=True)
                (wt / "spec" / "a.spec.js").write_text("x", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex", full_runner=runner)
        self.assertEqual(runner.calls, 0)
        self.assertEqual(card.state, "待验收")
        self.assertTrue(any("跳过" in e.get("summary", "") for e in self.fulls()))

    def test_without_runner_nothing_changes(self):
        card, _ = self.go(None)
        self.assertEqual(self.fulls(), [])


class Helpers(unittest.TestCase):
    def test_failing_files(self):
        out = ("FAIL: test_a (test_old.T)\nERROR: test_b (sub.test_x.T)\n"
               "ERROR: test_broken (unittest.loader._FailedTest)\n")
        files = failing_files(out)
        self.assertEqual(set(files), {"tests/test_old.py", "tests/sub/test_x.py", "tests/test_broken.py"})

    def test_culprits(self):
        now = Verdict(FAIL, 1, "FAIL: test_a (test_old.T)\nFAIL: test_c (test_other.T)\n")
        base = {"tests/test_old.py": Verdict(FAIL, 1, "FAIL: test_a (test_old.T)\n"),
                "tests/test_other.py": Verdict(PASS, 0, "OK")}
        self.assertEqual(culprits(now, base), ["tests/test_other.py"])

    def test_safe_env_hides_real_tools(self):
        with tempfile.TemporaryDirectory() as d:
            env = safe_env(Path(d) / "bin")
            for tool in ("codex", "cursor-agent", "agy"):
                found = subprocess.run(["/bin/sh", "-c", f"command -v {tool}"], env=env,
                                       capture_output=True, text=True).stdout.strip()
                self.assertEqual(found, "", tool)
            self.assertTrue(subprocess.run(["/bin/sh", "-c", "command -v python3"], env=env,
                                           capture_output=True).returncode == 0)
            self.assertNotIn("SUPERAGENT_QWEN_KEY", env)

    def test_stop_kills_group(self):
        ev = threading.Event()
        threading.Timer(0.5, ev.set).start()
        start = time.time()
        v = run_command(["/bin/sh", "-c", "sleep 30 & sleep 30"], ".", dict(os.environ), 60, ev)
        self.assertEqual(v.result, STOPPED)
        self.assertLess(time.time() - start, 10)


if __name__ == "__main__":
    unittest.main()


class RealRunner(unittest.TestCase):
    """真的复制一份小项目跑全量、在改之前的样子上比（不用假替身）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        (self.wt / "tests").mkdir(parents=True)
        (self.wt / "money.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.wt / "tests" / "test_money.py").write_text(
            "import sys, unittest\nsys.path.insert(0, '.')\nimport money\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(money.add(1, 2), 3)\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(cmd, cwd=self.wt, check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_then_break(self):
        from collab.fullrun import FullRunner
        runner = FullRunner(timeout=120)
        self.assertEqual(runner.run(self.wt).result, PASS)
        (self.wt / "money.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        now = runner.run(self.wt)
        self.assertEqual(now.result, FAIL)
        self.assertEqual(set(failing_files(now.output)), {"tests/test_money.py"})
        base = runner.base(self.wt, ["tests/test_money.py"])
        self.assertEqual(base["tests/test_money.py"].result, PASS)
        self.assertEqual(culprits(now, base), ["tests/test_money.py"])
        listed = subprocess.run(["git", "worktree", "list"], cwd=self.wt, capture_output=True, text=True).stdout
        self.assertEqual(len(listed.strip().splitlines()), 1, "比完把临时的改之前副本收掉")


class FullView(unittest.TestCase):
    def setUp(self):
        from collab.workspace import init_collab
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        self.collab = init_collab(self.project)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
        Card("CARD-001", state="测试中").save(self.cdir / "状态.json")
        from collab.flow import record
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "qwen", "复查员": "codex"})
        record(self.cdir, "判定", src="程序", result=PASS, summary="第 1 轮：测试全部通过")
        self.record = record

    def tearDown(self):
        self.tmp.cleanup()

    def station(self, view):
        return [s for s in view["stations"] if s["key"] == "check"][0]

    def test_running_and_result(self):
        from desktop.view import card_view
        self.record(self.cdir, "全量", src="程序", phase="开始", summary="跑全部测试")
        self.record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：通过")
        view = card_view(self.project)
        self.assertEqual(self.station(view)["state"], "active")
        self.assertIn("全部测试", view["now"]["text"])
        self.record(self.cdir, "全量", src="程序", phase="结果", result=FAIL, summary="老测试坏了：test_old")
        self.assertEqual(self.station(card_view(self.project))["state"], "fail")
