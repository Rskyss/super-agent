"""第四轮 54 复查退回的八个问题，按退回清单编号。"""

import fcntl
import io
import os
import shlex
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from textwrap import dedent
from threading import Event
from unittest import mock

from acceptance_helpers import ProjectCase, wait_until
from collab import cli
from collab.accept import accept_remaining
from collab.flow import read_flow, record, text_of
from collab.fullrun import FullRunner
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from desktop.clerk.tasks import launch


CARD = "CARD-001"
PASSED = Verdict(PASS, 0, "Ran 1 test in 0.100s\n\nOK\n")
PASSING_TEST = """\
import unittest

class Passing(unittest.TestCase):
    def test_passes(self):
        print("正常用例跑完了", flush=True)
"""


class FullCardReviewRegressions(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["COLLAB_PYTHON"] = sys.executable

    def blocked_card(self, card_id=CARD):
        cdir = self.make_card(card_id, state="需Claude介入", plan=True)
        record(cdir, "转人工", src="程序", dst="调度员", cause="复查不通过",
               summary="第 2 次复查不通过，需要调度员看原因", text="复查剩下的问题")
        return cdir, self.make_copy(card_id)

    def write_test(self, worktree, name, source):
        path = worktree / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(source), encoding="utf-8")

    def run_slow_same_name_files(self, packages):
        worktree = self.make_copy()
        starts = Path(self.tmp) / "用例开始记录"
        for package in packages:
            self.write_test(worktree, f"{package}/__init__.py", "")
            self.write_test(worktree, f"{package}/test_same.py", f"""\
                import time
                import unittest
                from pathlib import Path

                class Slow(unittest.TestCase):
                    def test_finishes(self):
                        with Path({str(starts)!r}).open("a", encoding="utf-8") as log:
                            print({package!r}, file=log)
                        time.sleep(2)
                        print("跑完 {package}/test_same.py", flush=True)
                """)
        # 各跑 2 秒，合起来超过 3.4 秒。只开一个并行位，验证换文件时重新计时。
        result = FullRunner(timeout=25, workers=1, file_timeout=3.4).run(worktree)
        return result, starts.read_text(encoding="utf-8").splitlines() if starts.exists() else []

    def resume_from_terminal(self, on_started=None):
        def pause_before_assigning_work(card, **kwargs):
            if on_started is not None:
                on_started.set()
            return card, None

        # 真走终端续跑和保存状态，只拦住后续派活，防止改代码干扰这轮结果。
        with mock.patch.object(cli, "run_card", side_effect=pause_before_assigning_work), \
                mock.patch.object(cli, "_remind_upgrades"), redirect_stdout(io.StringIO()):
            return cli.main(["run", self.project, CARD, "--resume-blocked", "--test-command", "true",
                             "--dev-tool", "codex", "--test-tool", "codex", "--review-tool", "none"])


    def test_02_resume_during_tests_invalidates_old_acceptance_result(self):
        _, worktree = self.blocked_card()
        self.write_test(worktree, "test_passes.py", PASSING_TEST)
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))

        def finish_tests_after_resume(_worktree):
            self.resume_from_terminal()
            return PASSED

        runner = mock.Mock()
        runner.run.side_effect = finish_tests_after_resume
        answer = accept_remaining(self.project, CARD, runner=runner)

        self.assertIn("开跑", self.flow_kinds())
        self.assertEqual(self.state(), "开发中", f"旧测试结果覆盖了续跑状态：{answer}")
        self.assertFalse(answer["ok"], answer)
        self.assertNotIn("接受", self.flow_kinds())

    def test_03_relative_python_path_is_used_in_the_copied_project(self):
        worktree = self.make_copy()
        self.write_test(worktree, "test_interpreter.py", """\
            import os
            import unittest

            class Interpreter(unittest.TestCase):
                def test_selected_python_is_used(self):
                    self.assertEqual(os.environ.get("FULLCARD_TEST_PYTHON"), "configured")
            """)
        caller = Path(self.tmp)
        python = caller / ".venv" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexport FULLCARD_TEST_PYTHON=configured\n"
                          f"exec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)

        previous = Path.cwd()
        try:
            os.chdir(caller)
            with mock.patch.dict(os.environ, {"COLLAB_PYTHON": "./.venv/bin/python3"}):
                os.environ.pop("FULLCARD_TEST_PYTHON", None)
                result = FullRunner(timeout=20).run(worktree)
        finally:
            os.chdir(previous)

        self.assertEqual(result.result, PASS, result.output)
        self.assertRegex(result.output, r"Ran 1 tests?\b")

    @staticmethod
    def worker_holds_lock(path):
        if not path.exists():
            return False
        with path.open("r+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False

    def test_04_external_stop_releases_test_process_and_leaves_a_reason(self):
        for index, sig in enumerate((signal.SIGTERM, signal.SIGKILL), start=1):
            with self.subTest(signal=sig.name):
                card_id = f"CARD-{index:03d}"
                cdir, worktree = self.blocked_card(card_id)
                lock = Path(self.tmp) / f"{card_id}-测试进程锁"
                release = Path(self.tmp) / f"{card_id}-放行"
                self.write_test(worktree, "test_waiting.py", f"""\
                    import fcntl
                    import time
                    import unittest
                    from pathlib import Path

                    class Waiting(unittest.TestCase):
                        def test_waits(self):
                            with Path({str(lock)!r}).open("w") as handle:
                                fcntl.flock(handle, fcntl.LOCK_EX)
                                release = Path({str(release)!r})
                                deadline = time.monotonic() + 25
                                while not release.exists() and time.monotonic() < deadline:
                                    time.sleep(0.05)
                    """)
                backend = self.collab / "后台" / "accept.json"
                with mock.patch.dict(os.environ, {"TMPDIR": self.tmp}):
                    pid = launch(self.project, "accept", card_id,
                                 ["accept-issues", self.project, card_id])
                try:
                    wait_until(lambda: self.worker_holds_lock(lock), 15, "测试进程没有启动")
                    os.kill(pid, sig)
                    wait_until(lambda: not backend.exists(), 15, "接受进程没有退出")

                    failure = next((e for e in reversed(read_flow(cdir)) if e["kind"] == "接受失败"), None)
                    reason = ((failure.get("summary") or "") + "\n" + (text_of(cdir, failure) or "")
                              if failure else "")
                    self.assertEqual(self.state(card_id), "需Claude介入")
                    self.assertIn("accept_issues", card_phase(self.project, card_id)["actions"])
                    self.assertEqual((self.worker_holds_lock(lock), bool(failure)), (False, True),
                                     f"测试应退出，交接应留原因。现有交接：{reason or '无'}")
                    self.assertRegex(reason, r"叫停|停止|终止|中断|取消|强杀|SIGTERM|SIGKILL")
                finally:
                    release.touch()
                    wait_until(lambda: not backend.exists(), 30, "清理时接受进程仍未退出")
                    wait_until(lambda: not self.worker_holds_lock(lock), 5, "清理时测试进程仍未退出")

    def test_05_timeout_handoff_keeps_progress_and_errors_from_the_run(self):
        cdir, worktree = self.blocked_card()
        self.write_test(worktree, "test_waiting.py", """\
            import sys
            import time
            import unittest

            class Waiting(unittest.TestCase):
                def test_runs_past_the_deadline(self):
                    print("进度：正在核对输入", flush=True)
                    print("报错：输入文件还没到", file=sys.stderr, flush=True)
                    time.sleep(25)
            """)
        self.write_test(worktree, "test_broken.py", """\
            import unittest

            class Broken(unittest.TestCase):
                def test_reports_a_failure(self):
                    self.fail("报错：另一份配置缺少必填项")
            """)

        answer = accept_remaining(self.project, CARD,
                                  runner=FullRunner(timeout=25, workers=2, file_timeout=2.5))

        self.assertFalse(answer["ok"], answer)
        self.assertEqual(self.state(), "需Claude介入")
        failure = read_flow(cdir)[-1]
        self.assertEqual(failure["kind"], "接受失败")
        self.assertIn("超时", failure["summary"])
        handoff = text_of(cdir, failure) or ""
        for expected in ("test_waiting.py", "test_broken.py", "进度：正在核对输入",
                         "报错：输入文件还没到", "报错：另一份配置缺少必填项"):
            with self.subTest(output=expected):
                self.assertIn(expected, handoff)


    def test_07_resume_after_final_check_cannot_be_overwritten_by_old_acceptance(self):
        _, worktree = self.blocked_card()
        self.write_test(worktree, "test_passes.py", PASSING_TEST)
        runner = mock.Mock()
        runner.run.return_value = PASSED
        checked = Event()
        allow_acceptance = Event()
        resumed = Event()
        transition = Card.transition

        def pause_before_pending_confirmation(card, to):
            if card.card_id == CARD and to == "待验收":
                checked.set()
                if not allow_acceptance.wait(15):
                    raise AssertionError("接受任务一直没收到放行信号")
            return transition(card, to)

        with mock.patch.object(Card, "transition", pause_before_pending_confirmation), \
                ThreadPoolExecutor(max_workers=2) as pool:
            accepting = pool.submit(accept_remaining, self.project, CARD, runner)
            try:
                self.assertTrue(checked.wait(10), "接受任务没有走到最后写入状态前")
                resuming = pool.submit(self.resume_from_terminal, resumed)
                # 续跑若被同一把锁挡住，就让接受先完成，再检查两边的实际先后。
                resumed_before_write = resumed.wait(3)
            finally:
                allow_acceptance.set()
            answer = accepting.result(timeout=15)
            resuming.result(timeout=15)

        kinds = self.flow_kinds()
        if resumed_before_write:
            self.assertFalse(answer["ok"], f"续跑已落盘，旧接受仍返回成功。流转记录：{kinds}")
            self.assertNotIn("接受", kinds)
        if "开跑" in kinds:
            self.assertEqual(self.state(), "开发中", f"旧接受覆盖了新施工。流转记录：{kinds}")
            if "接受" in kinds:
                self.assertLess(kinds.index("接受"), kinds.index("开跑"), kinds)
        else:
            self.assertTrue(answer["ok"], answer)
            self.assertEqual(self.state(), "待验收")

    def test_08_full_run_respects_parent_packages_load_tests_filter(self):
        worktree = self.make_copy()
        self.write_test(worktree, "test_normal.py", PASSING_TEST)
        self.write_test(worktree, "filtered/__init__.py", """\
            def load_tests(loader, tests, pattern):
                return tests
            """)
        self.write_test(worktree, "filtered/private/__init__.py", "")
        self.write_test(worktree, "filtered/private/test_excluded.py", """\
            import unittest

            class Excluded(unittest.TestCase):
                def test_must_not_run(self):
                    self.fail("项目已指定跳过这个子目录")
            """)

        result = FullRunner(timeout=20).run(worktree)

        self.assertEqual(result.result, PASS, result.output)
        self.assertIn("正常用例跑完了", result.output)
        self.assertNotIn("项目已指定跳过这个子目录", result.output)
