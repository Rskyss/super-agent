"""全量卡验收测试（Claude 写；**施工方不许改**，补充测试另写 test_fullcard_extra*.py）。依据 docs/跑卡流程调整/方案-全量卡.md、施工单-全量卡.md。"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from collab.flow import read_flow, record
from collab.judge import PASS
from test_acceptance_11_fixes import Case
from acceptance_helpers import wait_until

REVIEW_STOP = "复查员（codex）复查不通过（已自动退回 2 次）：1. 还剩一句少见说法"


def make_tree(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="fullcard-"))
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


OK_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
BAD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertTrue(False)\n"


def slow_test(seconds):
    return ("import time, unittest\nclass T(unittest.TestCase):\n"
            f"    def test_slow(self):\n        time.sleep({seconds})\n")


class PythonChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_collab_python_wins(self):
        from collab.fullrun import app_python, safe_env
        fake = self.tmp / "py"
        os.symlink(sys.executable, fake)
        with mock.patch.dict(os.environ, {"COLLAB_PYTHON": str(fake)}):
            self.assertEqual(app_python(), str(fake))
            safe_env(self.tmp / "bin")
        self.assertEqual(os.readlink(self.tmp / "bin" / "python3"), str(fake))

    def test_never_raises_and_returns_something_runnable(self):
        from collab.fullrun import app_python
        with mock.patch.dict(os.environ, {"COLLAB_PYTHON": ""}):
            path = app_python()
        self.assertTrue(path and os.access(path, os.X_OK), path)


class PerFileTimeout(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"COLLAB_PYTHON": sys.executable})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_one_slow_file_does_not_stop_the_others(self):
        from collab.fullrun import FullRunner, TIMEOUT
        wt = make_tree({"tests/test_slow.py": slow_test(30), "tests/test_ok.py": OK_TEST})
        self.addCleanup(shutil.rmtree, wt, True)
        t0 = time.time()
        v = FullRunner(timeout=120, file_timeout=2).run(wt)
        self.assertLess(time.time() - t0, 25, "单个文件超时只停它自己，不该等到整轮上限")
        self.assertEqual(v.result, TIMEOUT)
        self.assertIn("test_slow.py", v.output)
        self.assertIn("test_ok.py", v.output, "其余文件照跑，结果也列出来")

    def test_default_file_limit_is_five_minutes(self):
        from collab.fullrun import FILE_TIMEOUT, FullRunner
        self.assertEqual(FILE_TIMEOUT, 300)
        self.assertEqual(FullRunner().file_timeout, 300)


class StaleWhenCodeChanges(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"COLLAB_PYTHON": sys.executable})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_change_during_run_voids_the_result(self):
        from collab.fullrun import FullRunner
        from collab.judge import STALE
        wt = make_tree({"tests/test_wait.py": slow_test(3), "app.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, wt, True)

        def change():
            time.sleep(1)
            (wt / "app.py").write_text("x = 2\n", encoding="utf-8")

        threading.Thread(target=change).start()
        v = FullRunner(timeout=60).run(wt)
        self.assertEqual(v.result, STALE)
        self.assertNotEqual(v.result, PASS)
        self.assertIn("代码变了", v.output)

    def test_no_change_still_passes(self):
        from collab.fullrun import FullRunner
        wt = make_tree({"tests/test_ok.py": OK_TEST, "app.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, wt, True)
        self.assertEqual(FullRunner(timeout=60).run(wt).result, PASS)


class AcceptRemaining(Case):
    """点“接受剩下的问题”：后台先跑全部测试，全过才进待确认。"""

    def setUp(self):
        super().setUp()
        os.environ["COLLAB_PYTHON"] = sys.executable   # ProjectCase 收尾会还原环境变量

    def blocked(self, test_body=OK_TEST, reason=REVIEW_STOP):
        cdir = self.make_card(state="需Claude介入", plan=True)
        cause = "复查不通过" if reason == REVIEW_STOP else None
        record(cdir, "转人工", src="程序", dst="调度员", summary=reason, cause=cause, text=reason)
        wt = self.make_copy()
        (wt / "tests").mkdir(exist_ok=True)
        (wt / "tests" / "test_card.py").write_text(test_body, encoding="utf-8")
        return cdir

    def test_function_passes_then_moves_card(self):
        from collab.accept import accept_remaining
        cdir = self.blocked()
        answer = accept_remaining(self.project, "CARD-001")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.state(), "待验收")
        kinds = [e.get("kind") for e in read_flow(cdir)]
        self.assertIn("接受", kinds)
        self.assertEqual(kinds[-1], "结束")

    def test_function_failing_tests_keep_card_stopped(self):
        from collab.accept import accept_remaining
        cdir = self.blocked(BAD_TEST)
        answer = accept_remaining(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertEqual(self.state(), "需Claude介入")
        failed = [e for e in read_flow(cdir) if e.get("kind") == "接受失败"]
        self.assertTrue(failed and failed[-1].get("summary"))
        self.assertIn("全部测试", failed[-1]["summary"])

    def test_function_refuses_cards_not_stopped_by_review(self):
        from collab.accept import accept_remaining
        self.blocked(reason="返工 3 次后测试仍没过，需要调度员处理")
        answer = accept_remaining(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertEqual(self.state(), "需Claude介入")

    def test_button_runs_in_background_and_shows_running(self):
        from collab.phase import card_phase
        cdir = self.blocked(slow_test(3) + OK_TEST.replace("class T", "class U"))
        status, answer = self.act("accept_issues")
        self.assertEqual(status, 200, answer)
        self.assertEqual(self.state(), "需Claude介入", "点了不立刻进待确认，先跑全部测试")
        wait_until(lambda: card_phase(self.project, "CARD-001").get("code") == "accepting", 10, "没显示正在跑全部测试")
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["label"], "正在跑全部测试")
        self.assertEqual(phase["actions"], [])
        wait_until(lambda: self.state() == "待验收", 60, "全部测试过了却没进待确认")
        self.assertIn("接受", [e.get("kind") for e in read_flow(cdir)])


if __name__ == "__main__":
    unittest.main()
