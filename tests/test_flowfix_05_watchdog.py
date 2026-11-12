"""流程修理第 8 条（试跑记录 ㉓、⑲ 第一步）。

㉓ 68b 复查第 5 轮：复查员只读不改文件，跑了 15 分钟被看门人当成“卡住”掐掉，结论没写出来。
⑲ Cursor 卡住 20 多分钟，没人告诉用户；要等用户自己发现。
"""
import sys
import tempfile
import unittest
from pathlib import Path

from collab.agents import QuietTimeout, watched_runner

TALKER = "import time,sys\nfor i in range(30):\n    print('思考中', i, flush=True)\n    time.sleep(0.1)\n"
SILENT = "import time\ntime.sleep(3)\n"


class OutputCountsAsActivity(unittest.TestCase):
    def test_growing_output_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.log"
            code, out = watched_runner([sys.executable, "-c", TALKER], tmp, timeout=30, quiet_limit=1,
                                       poll=0.1, live_path=str(live))
            self.assertEqual(code, 0)
            self.assertIn("思考中 29", out)

    def test_growing_output_without_live_file_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = watched_runner([sys.executable, "-c", TALKER], tmp, timeout=30, quiet_limit=1, poll=0.1)
            self.assertEqual(code, 0)

    def test_silent_and_idle_still_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(QuietTimeout):
                watched_runner([sys.executable, "-c", "import time; time.sleep(30)"], tmp, timeout=60,
                               quiet_limit=1, poll=0.1)


class QuietReminders(unittest.TestCase):
    def test_reminder_at_each_mark_once(self):
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            watched_runner([sys.executable, "-c", SILENT], tmp, timeout=30, quiet_limit=20, poll=0.1,
                           on_quiet=said.append, remind_at=(1, 2))
        self.assertEqual(len(said), 2, said)
        self.assertGreaterEqual(said[0], 1)
        self.assertGreaterEqual(said[1], 2)

    def test_reminder_error_does_not_stop_the_worker(self):
        def boom(_):
            raise RuntimeError("提醒坏了")
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = watched_runner([sys.executable, "-c", SILENT], tmp, timeout=30, quiet_limit=20, poll=0.1,
                                     on_quiet=boom, remind_at=(1,))
            self.assertEqual(code, 0)


class ReminderReachesRecords(unittest.TestCase):
    def test_dispatch_records_reminder_for_the_card(self):
        from collab.agents import AgentResult
        from collab.dispatch import run_card
        from collab.flow import read_flow
        from collab.judge import PASS, Verdict
        from collab.state import Card
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wt = root / "wt"
            wt.mkdir()
            cdir = root / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")

            def ask_fn(adapter, prompt, cwd, on_quiet=None, **kw):
                if "编写自动化测试" not in prompt and "只读复查" not in prompt and on_quiet:
                    on_quiet(300)
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过" if "只读复查" in prompt else "好了")
            run_card(Card("CARD-001", state="待派发"), root / ".collab", wt, "true", "cursor", "codex", ask_fn=ask_fn,
                     judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
            tips = [e for e in read_flow(cdir) if e.get("kind") == "提醒"]
            self.assertEqual(len(tips), 1)
            self.assertIn("开发员（cursor）已经 5 分钟没有动静", tips[0]["summary"])
            self.assertIn("停下", tips[0]["summary"])


from test_acceptance_22_card_chat import ChatCase  # noqa: E402


class ClerkSaysIt(ChatCase):
    def test_reminder_and_auto_return_are_said_in_the_card_chat(self):
        from collab.flow import record
        from desktop.clerk.tasks import TaskWatcher
        cdir = self.building()
        cid = self.open()
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（cursor）已经 5 分钟没有动静")
        record(cdir, "退回", src="程序", dst="开发员", summary="复查不通过，自动退回写代码的（第 1 次，最多 2 次）")
        TaskWatcher(self.desk).tick(self.project)
        said = self.words(cid)
        self.assertIn("开发员（cursor）已经 5 分钟没有动静", said)
        self.assertIn("复查不通过，自动退回写代码的（第 1 次，最多 2 次）", said)


if __name__ == "__main__":
    unittest.main()
