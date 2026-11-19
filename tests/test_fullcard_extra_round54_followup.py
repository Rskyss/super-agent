"""第四轮 54 的 9 条退回问题，测试名按清单编号。"""

import fcntl
import os
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path
from textwrap import dedent
from unittest import mock

from acceptance_helpers import ProjectCase, wait_until
from collab.accept import accept_remaining
from collab.flow import read_flow, record, text_of
from collab.fullrun import FullRunner
from collab.judge import PASS, TIMEOUT
from collab.phase import card_phase
from desktop.clerk import tasks


CARD = "CARD-001"
PASSING_TEST = """\
import unittest

class Passing(unittest.TestCase):
    def test_finishes(self):
        print("正常用例跑完了", flush=True)
"""


class FullCardRound54Followup(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["COLLAB_PYTHON"] = sys.executable
        os.environ.pop("CARD010_SELECTED_PYTHON", None)

    def blocked_card(self):
        card = self.make_card(CARD, state="需Claude介入", plan=True)
        record(card, "转人工", src="程序", dst="调度员", cause="复查不通过",
               summary="第 2 次复查不通过", text="还有问题等你确认")
        return card, self.make_copy(CARD)

    @staticmethod
    def write_test(worktree, name, source):
        path = worktree / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(source), encoding="utf-8")

    @staticmethod
    def script(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def configured_python(self, path):
        self.script(path, 'export CARD010_SELECTED_PYTHON=1\n'
                          f'exec {shlex.quote(sys.executable)} "$@"\n')

    def latest_failure(self, card):
        return next((event for event in reversed(read_flow(card)) if event["kind"] == "接受失败"), None)

    def test_01_relative_python_is_used_when_selecting_the_suite(self):
        worktree = self.make_copy(CARD)
        self.write_test(worktree, "chosen/__init__.py", """\
            import os

            def load_tests(loader, tests, pattern):
                if os.environ.get("CARD010_SELECTED_PYTHON") == "1":
                    from .test_selected import Passing
                    tests.addTests(loader.loadTestsFromTestCase(Passing))
                return tests
            """)
        self.write_test(worktree, "chosen/test_selected.py", """\
            import os
            import unittest

            class Passing(unittest.TestCase):
                def test_finishes(self):
                    self.assertEqual(os.environ.get("CARD010_SELECTED_PYTHON"), "1")
                    print("正常用例跑完了", flush=True)
            """)
        caller = Path(self.tmp)
        self.configured_python(caller / ".venv/bin/python3")
        self.script(self.bin / "python3", f'exec {shlex.quote(sys.executable)} "$@"\n')

        previous = Path.cwd()
        try:
            os.chdir(caller)
            with mock.patch.dict(os.environ, {"COLLAB_PYTHON": "./.venv/bin/python3"}):
                result = FullRunner(timeout=20).run(worktree)
        finally:
            os.chdir(previous)

        self.assertEqual(result.result, PASS, result.output)
        self.assertIn("正常用例跑完了", result.output, "相对路径指定的 Python 应当从挑选用例时就生效")

    def waiting_worker(self, worktree):
        # 文件锁随进程退出由系统释放。信号文件在副本外，写入不会改变代码指纹。
        self.write_test(worktree, "test_waiting.py", f"""\
            import fcntl
            import os
            import time
            import unittest
            from pathlib import Path

            class Waiting(unittest.TestCase):
                def test_holds_lock_until_released(self):
                    root = Path({self.tmp!r})
                    label = os.environ["CARD010_ATTEMPT"]
                    with (root / (label + ".lock")).open("w") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX)
                        release = root / (label + ".release")
                        deadline = time.monotonic() + 35
                        while not release.exists() and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertTrue(release.exists(), "没有收到放行信号")
            """)

    def worker_alive(self, label):
        lock = Path(self.tmp) / (label + ".lock")
        if not lock.exists():
            return False
        with lock.open("r+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def launch_acceptance(self, label):
        env = {"TMPDIR": self.tmp, "CARD010_ATTEMPT": label}
        with mock.patch.dict(os.environ, env):
            pid = tasks.launch(self.project, "accept", CARD, ["accept-issues", self.project, CARD])
        self.addCleanup(self.finish_attempt, label, pid)
        return pid

    def release_worker(self, label):
        (Path(self.tmp) / (label + ".release")).touch()

    def finish_attempt(self, label, pid):
        self.release_worker(label)
        wait_until(lambda: not self.worker_alive(label) and not tasks.process_is_alive(pid), 12,
                   f"清理时 {label} 的接受进程或测试仍在运行")

    def pause_cleanup_after_forced_stop(self):
        # 只控制观察时机：接受进程已被回收，桌面线程还没收走它启动的测试。
        reaped = threading.Event()
        continue_cleanup = threading.Event()
        waiters = []
        popen = subprocess.Popen

        def start(cmd, *args, **kwargs):
            proc = popen(cmd, *args, **kwargs)
            if isinstance(cmd, list) and "accept-issues" in cmd:
                wait = proc.wait

                def pause_after_wait(*args, **kwargs):
                    code = wait(*args, **kwargs)
                    if code == -signal.SIGKILL:
                        waiters.append(threading.current_thread())
                        reaped.set()
                        continue_cleanup.wait(25)
                    return code

                proc.wait = pause_after_wait
            return proc

        with mock.patch.object(subprocess, "Popen", side_effect=start):
            pid = self.launch_acceptance("old")

        def finish_cleanup():
            self.release_worker("old")
            continue_cleanup.set()
            for waiter in waiters:
                waiter.join(10)
            wait_until(lambda: not self.worker_alive("old"), 8, "清理时旧测试仍在运行")

        self.addCleanup(finish_cleanup)
        wait_until(lambda: self.worker_alive("old"), 12, "旧一轮的测试没有启动")
        os.kill(pid, signal.SIGKILL)
        self.assertTrue(reaped.wait(8), "桌面没有回收到被强制叫停的接受进程")
        return continue_cleanup, waiters[0]

    def test_02_accept_button_waits_for_workers_and_stop_reason(self):
        card, worktree = self.blocked_card()
        self.waiting_worker(worktree)
        continue_cleanup, waiter = self.pause_cleanup_after_forced_stop()

        phase = card_phase(self.project, CARD)
        if "accept_issues" in phase["actions"]:
            self.assertEqual((self.worker_alive("old"), bool(self.latest_failure(card))), (False, True),
                             "按钮已经恢复，旧测试仍在跑，交接也还没有叫停原因")

        continue_cleanup.set()
        waiter.join(10)
        self.assertFalse(waiter.is_alive(), "桌面收尾一直没有完成")
        self.assertFalse(self.worker_alive("old"), "外部叫停后旧测试应该退出")
        failure = self.latest_failure(card)
        self.assertIsNotNone(failure, "交接里缺少接受失败记录")
        self.assertRegex(failure["summary"] + "\n" + (text_of(card, failure) or ""),
                         r"叫停|强杀|终止|中断|取消|SIGKILL")
        self.assertEqual(self.state(CARD), "需Claude介入")
        self.assertIn("accept_issues", card_phase(self.project, CARD)["actions"])

    def test_03_handoff_keeps_diagnostics_from_each_timed_out_file(self):
        card, worktree = self.blocked_card()
        for name, filler in (("first", 0), ("second", 8500)):
            self.write_test(worktree, f"test_{name}.py", f"""\
                import sys
                import time
                import unittest

                class Waiting(unittest.TestCase):
                    def test_reports_then_waits(self):
                        if {filler}:
                            print("x" * {filler}, flush=True)
                        print("{name} 进度：已经读完输入", flush=True)
                        print("{name} 报错：缺少必填值", file=sys.stderr, flush=True)
                        time.sleep(30)
                """)

        answer = accept_remaining(self.project, CARD, runner=FullRunner(timeout=20, workers=2, file_timeout=2.5))

        self.assertFalse(answer["ok"], answer)
        self.assertEqual(self.state(CARD), "需Claude介入")
        failure = self.latest_failure(card)
        self.assertIsNotNone(failure)
        self.assertIn("超时", failure["summary"])
        handoff = text_of(card, failure) or ""
        for name in ("first", "second"):
            self.assertIn(f"test_{name}.py", handoff)
            missing = [line for line in (f"{name} 进度：已经读完输入", f"{name} 报错：缺少必填值")
                       if line not in handoff]
            self.assertEqual(missing, [], "交接丢了这份超时测试的关键信息")

    def test_04_deselected_subdirectory_stays_skipped_if_discovery_stops(self):
        worktree = self.make_copy(CARD)
        outside = Path(self.tmp)
        interrupted = outside / "加载时退出了"
        excluded = outside / "跳过的文件被加载了"
        self.write_test(worktree, "test_abort.py", f"""\
            import os
            from pathlib import Path
            Path({str(interrupted)!r}).touch()
            os._exit(23)
            """)
        self.write_test(worktree, "filtered/__init__.py", """\
            def load_tests(loader, tests, pattern):
                return tests
            """)
        self.write_test(worktree, "filtered/private/__init__.py", "")
        self.write_test(worktree, "filtered/private/test_excluded.py", f"""\
            import unittest
            from pathlib import Path

            Path({str(excluded)!r}).touch()

            class Excluded(unittest.TestCase):
                def test_is_excluded(self):
                    self.fail("项目已经指定跳过这个测试")
            """)

        result = FullRunner(timeout=20, workers=2).run(worktree)

        self.assertTrue(interrupted.exists(), "另一个文件没有走到中途退出的加载代码")
        self.assertFalse(excluded.exists(), f"挑选中断后又加载了项目指定跳过的目录\n{result.output}")

    def test_05_package_load_tests_selects_one_method_from_a_file(self):
        worktree = self.make_copy(CARD)
        self.write_test(worktree, "filtered/__init__.py", """\
            def load_tests(loader, tests, pattern):
                from .test_choices import Choices
                tests.addTest(Choices("test_selected"))
                return tests
            """)
        self.write_test(worktree, "filtered/test_choices.py", """\
            import unittest

            class Choices(unittest.TestCase):
                def test_selected(self):
                    print("被选中的用例跑完了", flush=True)

                def test_excluded(self):
                    self.fail("没被选中的用例被跑了")
            """)

        result = FullRunner(timeout=20).run(worktree)

        self.assertEqual(result.result, PASS, result.output)
        self.assertIn("被选中的用例跑完了", result.output)
        self.assertRegex(result.output, r"Ran 1 test\b")

    def test_06_package_import_error_blocks_acceptance_and_is_recorded(self):
        card, worktree = self.blocked_card()
        self.write_test(worktree, "test_ok.py", PASSING_TEST)
        self.write_test(worktree, "shop/__init__.py", 'raise RuntimeError("商店测试包加载失败")\n')
        self.write_test(worktree, "shop/test_checkout.py", PASSING_TEST)

        answer = accept_remaining(self.project, CARD, runner=FullRunner(timeout=20))

        self.assertEqual((answer["ok"], self.state(CARD)), (False, "需Claude介入"),
                         "有测试包在加载时出错，卡片不能进入待确认")
        failure = self.latest_failure(card)
        self.assertIsNotNone(failure)
        self.assertIn("商店测试包加载失败", text_of(card, failure) or "")

    def test_07_discovery_uses_configured_python_and_hides_ambient_tools(self):
        worktree = self.make_copy(CARD)
        tool_used = Path(self.tmp) / "外部假工具被调用"
        interpreters = Path(self.tmp) / "加载测试时用的解释器"
        selected = Path(self.tmp) / "app-python"
        self.configured_python(selected)
        self.script(self.bin / "python3", f'exec {shlex.quote(sys.executable)} "$@"\n')
        self.script(self.bin / "card010-fixture-tool", f'touch {shlex.quote(str(tool_used))}\n')
        self.write_test(worktree, "test_load.py", f"""\
            import os
            import shutil
            import subprocess
            import unittest
            from pathlib import Path

            with Path({str(interpreters)!r}).open("a", encoding="utf-8") as handle:
                print(os.environ.get("CARD010_SELECTED_PYTHON", "外面的 Python"), file=handle)
            tool = shutil.which("card010-fixture-tool")
            if tool:
                subprocess.run([tool], check=True, timeout=5)

            class Loaded(unittest.TestCase):
                def test_finishes(self):
                    print("加载后跑完了", flush=True)
            """)

        with mock.patch.dict(os.environ, {"COLLAB_PYTHON": str(selected)}):
            result = FullRunner(timeout=20).run(worktree)

        self.assertEqual(result.result, PASS, result.output)
        self.assertIn("加载后跑完了", result.output)
        used = interpreters.read_text(encoding="utf-8").splitlines()
        self.assertEqual((set(used), tool_used.exists()), ({"1"}, False),
                         "加载测试时应使用指定 Python，也不该找到外部命令")

    def test_08_slow_import_does_not_use_up_the_other_files_time(self):
        worktree = self.make_copy(CARD)
        slow_started = Path(self.tmp) / "慢文件开始加载了"
        self.write_test(worktree, "test_fast.py", PASSING_TEST)
        self.write_test(worktree, "test_slow.py", f"""\
            import time
            import unittest
            from pathlib import Path

            Path({str(slow_started)!r}).touch()
            print("慢文件卡在加载", flush=True)
            time.sleep(6)

            class Slow(unittest.TestCase):
                def test_finishes(self):
                    pass
            """)

        result = FullRunner(timeout=4, workers=2, file_timeout=1.5).run(worktree)

        self.assertTrue(slow_started.exists(), "慢文件没有走到加载卡住的位置")
        self.assertEqual(result.result, TIMEOUT, result.output)
        self.assertRegex(result.output, r"test_slow\.py.*超时")
        self.assertRegex(result.output, r"test_fast\.py.*过", "正常文件应在慢文件超时的这一轮跑完\n" + result.output)

    def test_09_old_cleanup_does_not_kill_a_retry_of_the_same_card(self):
        _, worktree = self.blocked_card()
        self.waiting_worker(worktree)
        continue_cleanup, waiter = self.pause_cleanup_after_forced_stop()

        try:
            new_pid = self.launch_acceptance("retry")
        except tasks.TaskBusy:
            # 允许桌面先完成这张卡的收尾，再放行重试。
            continue_cleanup.set()
            waiter.join(10)
            self.assertFalse(waiter.is_alive(), "旧一轮迟迟没有收尾")
            new_pid = self.launch_acceptance("retry")
        wait_until(lambda: self.worker_alive("retry"), 12, "重试的测试没有启动")

        continue_cleanup.set()
        waiter.join(10)

        self.assertFalse(waiter.is_alive(), "旧一轮迟迟没有收尾")
        self.assertTrue(self.worker_alive("retry"), "旧一轮收尾杀掉了刚启动的重试测试")
        backend = tasks.running(self.project, "accept")
        self.assertIsNotNone(backend, "旧收尾清掉了新一轮接受任务的后台记录")
        self.assertEqual(backend["pid"], new_pid)
        self.release_worker("retry")
        wait_until(lambda: self.state(CARD) == "待验收", 12, "重试没有正常跑到待确认")
