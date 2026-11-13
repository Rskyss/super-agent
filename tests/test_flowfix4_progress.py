"""流程修理第四轮 任务 6（53）：写代码那步的进度条“通过 x/y”，写测试那步“已写 N 项”。"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow, record
from collab.judge import PASS, Verdict
from collab.progress import ProgressJob, count_written, read, tally
from collab.state import Card

TEST_PY = ("import sys, unittest\nsys.path.insert(0, '.')\nimport money\n\nclass T(unittest.TestCase):\n"
           "    def test_add(self):\n        self.assertEqual(money.add(1, 2), 3)\n"
           "    def test_sub(self):\n        self.assertEqual(money.sub(3, 2), 1)\n"
           "    def test_mul(self):\n        self.assertEqual(money.mul(2, 2), 4)\n")


class Helpers(unittest.TestCase):
    def test_tally(self):
        self.assertEqual(tally("Ran 10 tests in 1s\n\nFAILED (failures=2, errors=1)\n"), (7, 10))
        self.assertEqual(tally("Ran 4 tests in 1s\n\nOK (skipped=1)\n"), (3, 4))
        self.assertEqual(tally("Ran 5 tests in 1s\n\nOK\n"), (5, 5))
        self.assertIsNone(tally("SyntaxError"))

    def test_count_written(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tests").mkdir()
            (Path(d) / "tests" / "test_a.py").write_text(TEST_PY, encoding="utf-8")
            self.assertEqual(count_written(d, ["tests/test_a.py", "tests/none.py"]), 3)

    def test_due(self):
        job = ProgressJob("/x", "/y", "开发", 1, [], gap=120)
        self.assertTrue(job.due(1000, 0), "第一次马上算")
        job.last_run = 1000
        self.assertFalse(job.due(1050, 1040), "不到 2 分钟不算")
        self.assertFalse(job.due(1200, 900), "没有新改动不算")
        self.assertTrue(job.due(1200, 1100))


class RealCompute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        self.cdir = Path(self.tmp.name) / "card"
        self.cdir.mkdir()
        (self.wt / "tests").mkdir(parents=True)
        (self.wt / "money.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.wt / "tests" / "test_money.py").write_text(TEST_PY, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_passed_in_copy(self):
        job = ProgressJob(self.cdir, self.wt, "开发", 7, ["tests/test_money.py"], gap=0, every=0.1)
        job.tick()
        self.assertEqual(read(self.cdir), dict(read(self.cdir), seq=7, passed=1, total=3))
        self.assertFalse((self.wt / ".git").exists(), "不碰写代码的那份")
        time.sleep(1.1)
        (self.wt / "money.py").write_text("def add(a, b):\n    return a + b\ndef sub(a, b):\n    return a - b\n",
                                          encoding="utf-8")
        job.tick()
        self.assertEqual(read(self.cdir)["passed"], 2)

    def test_stop_ends_thread(self):
        job = ProgressJob(self.cdir, self.wt, "开发", 1, ["tests/test_money.py"], gap=0, every=0.1).start()
        time.sleep(0.3)
        job.stop()
        self.assertFalse(job.thread.is_alive())

    def test_tester_counts(self):
        job = ProgressJob(self.cdir, self.wt, "写测试", 3, [])
        job.started = 0
        job.tick()
        self.assertEqual(read(self.cdir)["written"], 3)


class InFlow(unittest.TestCase):
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
        (self.worktree / "README.md").write_text("x\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(cmd, cwd=self.worktree, check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, progress):
        seen = []
        wt, cdir = self.worktree, self.cdir

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "money.py").write_text("def add(a, b):\n    return 0\n", encoding="utf-8")
                (wt / "tests" / "test_money.py").write_text(TEST_PY, encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            time.sleep(1.5)
            seen.append(read(cdir))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                 ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                 progress=progress)
        return seen

    def test_dev_progress_written_while_working(self):
        seen = self.go(True)
        dev = [e for e in read_flow(self.cdir) if e.get("kind") == "派活" and e.get("dst") == "开发员"][0]
        self.assertEqual(seen[0].get("seq"), dev["seq"])
        self.assertEqual(seen[0].get("total"), 3)

    def test_off_by_default(self):
        self.go(False)
        self.assertEqual(read(self.cdir), {})


class Shown(unittest.TestCase):
    def setUp(self):
        from collab.workspace import init_collab
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        self.cdir = init_collab(self.project) / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
        Card("CARD-001", state="开发中").save(self.cdir / "状态.json")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "qwen", "复查员": "codex"})

    def tearDown(self):
        self.tmp.cleanup()

    def dev(self):
        from desktop.view import card_view
        return [s for s in card_view(self.project)["stations"] if s["key"] == "dev"][0]

    def test_station_carries_progress_of_this_dispatch(self):
        from collab.progress import write
        sent = record(self.cdir, "派活", src="调度员", dst="开发员", tool="qwen", step="开发", round=1,
                      summary="写代码", live=True)
        write(self.cdir, {"seq": sent["seq"] - 1, "passed": 9, "total": 9})
        self.assertNotIn("progress", self.dev(), "上一次派活的数不算")
        write(self.cdir, {"seq": sent["seq"], "passed": 120, "total": 269})
        self.assertEqual(self.dev()["progress"], {"passed": 120, "total": 269})

    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_panel_text(self):
        script = ("const P=require('./desktop/static/panel.js');const a=P.progressText({passed:120,total:269});"
                  "if(a.text!=='通过 120/269'||a.pct!==45)process.exit(1);"
                  "if(P.progressText({written:37}).text!=='已写 37 项')process.exit(2);"
                  "if(P.progressText({computing:true}).pct!==0)process.exit(3);"
                  "if(P.progressText(null)!==null)process.exit(4);")
        done = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
