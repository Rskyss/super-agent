"""v0.96 C2：终端里开的卡，窗口里也能停、能接着跑（docs/跑卡不停半路/方案.md 第 6 条）。

11-27 train 卡 16：Gemini 在终端里 collab run 开卡，窗口没登记（后台 run.json 还是前一天的卡），
用户想停卡时窗口说“这张卡不是从这里开工的，请到终端里停”；停下后点“接着跑”又报“还没有施工用的测试命令”（没有开工.json）。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.judge import PASS, Verdict
from review_fmt import listed_ask
from test_collab_cli import _confirm_profile_and_plan, _make_repo, _run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))


class TerminalRun(unittest.TestCase):
    def setUp(self):
        self._sandbox = os.environ.pop("CODEX_SANDBOX", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "demo"
        _make_repo(self.project)
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("加 hello\n\n## 允许改动的范围\n- hello.txt\n", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)
        self.backend = self.project / ".collab" / "后台" / "run.json"
        self.start = self.project / ".collab" / "cards" / "CARD-001" / "开工.json"

    def tearDown(self):
        self.tmp.cleanup()
        if self._sandbox is not None:
            os.environ["CODEX_SANDBOX"] = self._sandbox

    def test_terminal_run_registers_while_running_and_cleans_up(self):
        seen = []

        def fake_ask(adapter, prompt, cwd, **kw):
            rec = json.loads(self.backend.read_text(encoding="utf-8")) if self.backend.exists() else None
            seen.append(rec)
            (Path(cwd) / "hello.txt").write_text("hi")
            return AgentResult(ok=True, output="结论：通过", attempts=1)

        code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"],
                         ask_fn=listed_ask(fake_ask), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"))
        self.assertEqual(code, 0, out)
        self.assertTrue(seen and seen[0], "跑的时候后台要有登记，窗口才停得了")
        self.assertEqual(seen[0]["card"], "CARD-001")
        self.assertEqual(seen[0]["pid"], os.getpid())
        self.assertFalse(self.backend.exists(), "跑完删掉自己写的那份登记")
        self.assertEqual(json.loads(self.start.read_text(encoding="utf-8"))["test_command"], "true",
                         "没有开工记录就补记这次的测试命令，窗口点“接着跑”读得到")

    def test_does_not_overwrite_a_live_window_record(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            from collab.phase import process_start
            self.backend.parent.mkdir(parents=True, exist_ok=True)
            mine = {"card": "CARD-009", "pid": other.pid, "time": time.time(), "started": process_start(other.pid)}
            self.backend.write_text(json.dumps(mine), encoding="utf-8")
            code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"],
                             ask_fn=listed_ask(lambda *a, **k: AgentResult(ok=True, output="结论：通过", attempts=1)),
                             judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"))
            self.assertEqual(json.loads(self.backend.read_text(encoding="utf-8"))["card"], "CARD-009",
                             "别的进程还活着的登记不能盖掉、也不能删")
            self.assertNotEqual(code, 0, "已有一张卡在施工：不开工（复查 C 严重 2）")
            self.assertIn("CARD-009", out)
        finally:
            other.kill()
            other.wait()

    def test_terminal_command_differs_from_start_record_updates_it(self):
        """复查 C 严重 3：开工记录里是命令 A、终端用命令 B 开的卡，窗口“接着跑”要用 B。"""
        self.start.write_text(json.dumps({"test_command": "false", "walk_command": "w"}), encoding="utf-8")
        _run(["run", str(self.project), "CARD-001", "--test-command", "true"],
             ask_fn=listed_ask(lambda *a, **k: AgentResult(ok=True, output="结论：通过", attempts=1)),
             judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"))
        data = json.loads(self.start.read_text(encoding="utf-8"))
        self.assertEqual(data["test_command"], "true")
        self.assertEqual(data["test_command_was"], "false")
        self.assertEqual(data["walk_command"], "w", "别的开工记录照留")

    def test_judge_process_group_is_collected_on_stop(self):
        """复查 C 严重 1：判定测试自己开进程组，停工时要一并收掉（登记进 fullrun._GROUPS）。"""
        from collab import fullrun
        from collab.judge import judge
        seen = []
        orig = fullrun._GROUPS

        class Spy(set):
            def add(self, item):
                seen.append(item)
                super().add(item)

        fullrun._GROUPS = Spy()
        try:
            judge(["/bin/sh", "-c", "true"], cwd=str(self.project), timeout=10)
        finally:
            fullrun._GROUPS = orig
        self.assertTrue(seen, "跑判定时登记了进程组")

    def test_window_can_stop_a_terminal_started_card(self):
        """真起一个终端进程（不是进程组长），按终端登记的样子写登记，窗口的停卡能把它停掉。"""
        from clerk import tasks as clerk_tasks
        from collab.phase import process_is_alive, process_start
        # 终端里起的进程不是窗口的子进程：用后台再起一层，让它不归测试进程收（否则停掉后成了僵尸，看着还“活着”）
        out = subprocess.run(["/bin/sh", "-c", f"'{sys.executable}' -c 'import time; time.sleep(60)' "
                                               ">/dev/null 2>&1 & echo $!"], capture_output=True, text=True)
        pid = int(out.stdout.strip())
        try:
            started = process_start(pid)
            self.backend.parent.mkdir(parents=True, exist_ok=True)
            self.backend.write_text(json.dumps({"card": "CARD-001", "pid": pid, "time": time.time(),
                                                "started": started, "from": "终端"}), encoding="utf-8")
            clerk_tasks.stop_construction(str(self.project), "CARD-001")
            self.assertFalse(process_is_alive(pid, started))
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
