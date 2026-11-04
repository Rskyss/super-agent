import subprocess
import sys
import tempfile
import time
import unittest

from collab.agents import QuietTimeout, ask, watched_runner


class WatchedRunnerTest(unittest.TestCase):
    def test_finishes_normally_and_keeps_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = watched_runner([sys.executable, "-c", "print('hello')"], tmp,
                                       timeout=30, quiet_limit=10, poll=0.1)
            self.assertEqual(code, 0)
            self.assertIn("hello", out)

    def test_no_file_activity_is_stopped_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = time.time()
            with self.assertRaises(QuietTimeout):
                watched_runner([sys.executable, "-c", "import time; time.sleep(30)"], tmp,
                               timeout=60, quiet_limit=1, poll=0.1)
            self.assertLess(time.time() - started, 10)  # 远早于 30 秒的总时长

    def test_steady_file_activity_is_not_stopped(self):
        writer = ("import time, pathlib\n"
                  "for i in range(8):\n"
                  "    pathlib.Path(f'f{i}.txt').write_text(str(i))\n"
                  "    time.sleep(0.3)\n")
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = watched_runner([sys.executable, "-c", writer], tmp,
                                     timeout=30, quiet_limit=1, poll=0.1)
            self.assertEqual(code, 0)

    def test_overall_timeout_still_applies(self):
        writer = ("import time, pathlib\n"
                  "i = 0\n"
                  "while True:\n"
                  "    pathlib.Path('beat.txt').write_text(str(i)); i += 1\n"
                  "    time.sleep(0.2)\n")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(subprocess.TimeoutExpired):
                watched_runner([sys.executable, "-c", writer], tmp,
                               timeout=1.5, quiet_limit=10, poll=0.1)


class AskQuietTest(unittest.TestCase):
    def test_quiet_timeout_is_not_retried_and_explains_why(self):
        calls = []

        def runner(cmd, cwd, timeout):
            calls.append(cmd)
            raise QuietTimeout(900)

        with tempfile.TemporaryDirectory() as tmp:
            result = ask("cursor", "做事", tmp, runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(len(calls), 1)  # 卡住的不重试，避免白白再等一轮
        self.assertIn("没有任何文件变化", result.output)


class DispatchRecordsWhyTest(unittest.TestCase):
    def _run(self, failing_adapter):
        from collab.agents import AgentResult
        from collab.dispatch import run_card
        from collab.judge import PASS, Verdict
        from collab.state import Card
        from collab.workspace import init_collab
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collab = init_collab(tmp.name)
        card_dir = collab / "cards" / "CARD-001"
        card_dir.mkdir(parents=True)
        (card_dir / "需求卡.md").write_text("x", encoding="utf-8")

        def ask(adapter, prompt, cwd, **kw):
            if adapter == failing_adapter:
                return AgentResult(ok=False, output="看门人：超过 15 分钟没有任何文件变化，已停止", attempts=1)
            return AgentResult(ok=True, output="", attempts=1)

        card = run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=tmp.name,
                        test_command=["t"], dev_adapter="cursor", test_adapter="codex", ask_fn=ask,
                        judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"))[0]
        return card, (card_dir / "返工记录.md").read_text(encoding="utf-8")

    def test_stuck_developer_is_escalated_with_reason(self):
        card, log = self._run("cursor")
        self.assertEqual(card.state, "需Claude介入")
        self.assertIn("开发员（cursor）没有完成", log)
        self.assertIn("没有任何文件变化", log)

    def test_stuck_tester_is_escalated_with_reason(self):
        card, log = self._run("codex")
        self.assertEqual(card.state, "需Claude介入")
        self.assertIn("测试员（codex）没有完成", log)


if __name__ == "__main__":
    unittest.main()
