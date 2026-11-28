"""第一期 1-9 测试提速（11-14，卡 71 写代码 15 分钟、测试近 2 小时）：
1. 全部测试按文件分开、几个同时跑（每个文件单独一个进程，互不干扰）；汇总照旧认得出哪几项没过。
2. 有全部测试兜底时，不再先跑一遍“相关老测试”挡着复查开工（两者重复）。
3. 全部测试里冒出的“改坏”，先在现在的代码上单独重跑那个文件：单独跑能过（只在一起跑时坏）就不退回写代码的，停下交人看。"""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from collab.fullrun import FullRunner, failing_files, summary_count
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card

from test_flowfix4_full import FAIL_OUT, Base, FakeRunner


def test_file(name, body):
    return ("import sys, os, time, unittest\nsys.path.insert(0, '.')\n\nclass T(unittest.TestCase):\n" + body)


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        (self.wt / "tests").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, body):
        (self.wt / "tests" / name).write_text(test_file(name, body), encoding="utf-8")

    def commit(self):
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(cmd, cwd=self.wt, check=True, capture_output=True)


class ParallelFullRun(ProjectCase):
    def test_files_run_side_by_side(self):
        for i in range(4):
            self.put(f"test_slow{i}.py", "    def test_wait(self):\n        time.sleep(1.5)\n")
        self.commit()
        t0 = time.time()
        now = FullRunner(timeout=120, workers=4).run(self.wt)
        took = time.time() - t0
        self.assertEqual(now.result, PASS, now.output)
        self.assertLess(took, 5.5, "4 个各 1.5 秒的文件同时跑，不该花一个个跑的 6 秒以上")
        self.assertIn("Ran 4 tests", now.output)

    def test_failures_still_named_and_counted(self):
        self.put("test_good.py", "    def test_ok(self):\n        pass\n    def test_ok2(self):\n        pass\n")
        self.put("test_bad.py", "    def test_no(self):\n        self.assertEqual(1, 2)\n")
        self.put("test_worse.py", "    def test_err(self):\n        raise RuntimeError('x')\n"
                                  "    def test_skip(self):\n        self.skipTest('s')\n")
        self.commit()
        now = FullRunner(timeout=120, workers=3).run(self.wt)
        self.assertEqual(now.result, FAIL)
        self.assertEqual(set(failing_files(now.output, self.wt)), {"tests/test_bad.py", "tests/test_worse.py"})
        self.assertEqual(summary_count(now.output), 2, "各文件的失败数要加起来，不能只认第一个文件的汇总")
        self.assertIn("Ran 5 tests", now.output)

    def test_each_file_in_its_own_process(self):
        # 一个文件改了全局状态，另一个文件不受影响（卡 71：一起跑时前面测试留下的东西把后面的测试弄坏）
        self.put("test_a_dirty.py", "    def test_set(self):\n        os.environ['LEFT_BEHIND'] = '1'\n")
        self.put("test_b_clean.py", "    def test_clean(self):\n        self.assertNotIn('LEFT_BEHIND', os.environ)\n")
        self.commit()
        self.assertEqual(FullRunner(timeout=120, workers=1).run(self.wt).result, PASS)

    def test_default_runs_several_at_once(self):
        cpus = os.cpu_count() or 2
        self.assertEqual(FullRunner().workers, max(1, min(6, cpus // 2)))
        if cpus >= 4:
            self.assertGreater(FullRunner().workers, 1)

    def test_stop_ends_quickly_and_starts_nothing_new(self):
        import threading
        from collab.fullrun import STOPPED
        marks = Path(self.tmp.name) / "marks"
        marks.mkdir()
        for i in range(5):
            self.put(f"test_long{i}.py", f"    def test_wait(self):\n        open({str(marks)!r} + '/{i}', 'w').close()\n"
                                         "        time.sleep(30)\n")
        self.commit()
        stop = threading.Event()
        threading.Timer(1.5, stop.set).start()
        t0 = time.time()
        v = FullRunner(timeout=120, workers=2).run(self.wt, stop)
        self.assertLess(time.time() - t0, 15, "叫停要及时收掉同时跑的几个")
        self.assertEqual(v.result, STOPPED)
        time.sleep(1)
        self.assertLessEqual(len(list(marks.iterdir())), 2, "叫停后不再起新的测试文件")

    def test_app_quitting_starts_nothing_new(self):
        # 桌面退出时 kill_all_groups 先清场、之后才叫停：这段空档里也不许再起新的测试文件（复查第 1 轮）
        import threading
        from collab import fullrun
        from collab.fullrun import STOPPED
        marks = Path(self.tmp.name) / "marks"
        marks.mkdir()
        for i in range(5):
            self.put(f"test_long{i}.py", f"    def test_wait(self):\n        open({str(marks)!r} + '/{i}', 'w').close()\n"
                                         "        time.sleep(30)\n")
        self.commit()
        threading.Timer(1.5, fullrun.kill_all_groups).start()
        try:
            v = FullRunner(timeout=120, workers=2).run(self.wt)
        finally:
            fullrun._EXITING["on"] = False
        self.assertEqual(v.result, STOPPED)
        time.sleep(1)
        self.assertLessEqual(len(list(marks.iterdir())), 2)

    def test_file_that_dies_without_summary_is_not_hidden(self):
        from collab.judge import ENV_ERROR
        for code in (3, 0):   # 退出码 0 也一样：没汇总就是没跑完
            with self.subTest(code=code):
                self.tearDown(); self.setUp()
                self.put("test_old_fail.py", "    def test_no(self):\n        self.assertEqual(1, 2)\n")
                self.put("test_dies.py", f"    def test_die(self):\n        os._exit({code})\n")
                self.commit()
                v = FullRunner(timeout=120, workers=2).run(self.wt)
                self.assertEqual(v.result, ENV_ERROR, "没跑完的文件不能被别处的失败盖过去、也不能当成过")
                self.assertIn("test_dies", v.output)
                alone = FullRunner(timeout=120).alone(self.wt, ["tests/test_dies.py"])
                self.assertEqual(alone["tests/test_dies.py"].result, ENV_ERROR, "单独重跑没跑完不等于单独也坏")

    def test_file_without_tests_counts_as_pass(self):
        self.put("test_ok.py", "    def test_ok(self):\n        pass\n")
        (self.wt / "tests" / "test_empty.py").write_text("# 还没写测试\n", encoding="utf-8")
        self.commit()
        self.assertEqual(FullRunner(timeout=120, workers=2).run(self.wt).result, PASS)

    def test_no_tests_ran_on_new_python_counts_as_pass(self):
        from collab.fullrun import _finished
        out = "\n" + "-" * 70 + "\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n"   # Python 3.12 起的写法，退出码 5
        self.assertEqual(_finished(Verdict(FAIL, 5, out)).result, PASS)
        # 测试里自己跑了个空 suite（先打出 NO TESTS RAN），后面真失败：只认最后的汇总，不许放行（复查第 3 轮）
        inner = out + "F\n" + "=" * 70 + "\nFAIL: test_x (test_y.T)\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n"
        self.assertEqual(_finished(Verdict(FAIL, 1, inner)).result, FAIL)
        from collab.judge import ENV_ERROR
        self.assertNotEqual(_finished(Verdict(FAIL, 1, out)).result, PASS, "NO TESTS RAN 但退出码不是 5，不认")
        self.assertEqual(_finished(Verdict(PASS, 0, out)).result, ENV_ERROR, "退出码 0 的 NO TESTS RAN 也不认（复查第 4 轮）")
        from collab.fullrun import _merge as merge0
        self.assertEqual(merge0([("t.py", Verdict(PASS, 0, out))], 1.0).result, ENV_ERROR)
        self.assertEqual(_finished(Verdict(FAIL, 5, inner)).result, FAIL, "只认最后的汇总")
        from collab.fullrun import _merge
        nested = "Ran 2 tests in 0.001s\n\nOK\n" + inner   # 测试里先跑了个 2 项的小 suite
        merged = _merge([("test_y.py", Verdict(FAIL, 1, nested))], 1.0)
        self.assertEqual(merged.result, FAIL)
        self.assertIn("Ran 1 tests", merged.output, "每个文件只算最后的总汇总，不把里面小 suite 的也加上")

    def test_starts_nothing_once_quitting(self):
        from collab import fullrun
        from collab.fullrun import STOPPED, run_command
        real = subprocess.Popen
        started = []

        def popen_then_quit(*a, **k):   # 正好在起测试进程的这一刻开始清场（收进程时查 ps 的不算）
            p = real(*a, **k)
            if list(a[0])[:1] == ["sleep"]:
                started.append(p)
                fullrun._EXITING["on"] = True
            return p
        try:
            with mock.patch("collab.fullrun.subprocess.Popen", side_effect=popen_then_quit):
                v = run_command(["sleep", "30"], self.wt, dict(os.environ), 60)
            self.assertEqual(v.result, STOPPED)
            time.sleep(0.5)
            self.assertIsNotNone(started[0].poll(), "登记后发现在清场，自己把刚起的进程收掉")
            v2 = run_command(["sleep", "30"], self.wt, dict(os.environ), 60)
            self.assertEqual(v2.result, STOPPED)
            self.assertEqual(len(started), 1, "清场开始后不再起新进程")
        finally:
            fullrun._EXITING["on"] = False

    def test_same_name_in_two_folders_runs_once(self):
        for sub, body in (("a", "    def test_no(self):\n        self.assertEqual(1, 2)\n"),
                          ("b", "    def test_ok(self):\n        pass\n")):
            (self.wt / "tests" / sub).mkdir()
            (self.wt / "tests" / sub / "__init__.py").write_text("", encoding="utf-8")
            (self.wt / "tests" / sub / "test_dup.py").write_text(test_file("test_dup.py", body), encoding="utf-8")
        (self.wt / "tests" / "__init__.py").write_text("", encoding="utf-8")
        self.commit()
        v = FullRunner(timeout=120, workers=2).run(self.wt)
        self.assertIn("Ran 2 tests", v.output)
        self.assertEqual(summary_count(v.output), 1)

    def test_alone(self):
        self.put("test_ok.py", "    def test_ok(self):\n        pass\n")
        self.commit()
        alone = FullRunner(timeout=120).alone(self.wt, ["tests/test_ok.py"])
        self.assertEqual(alone["tests/test_ok.py"].result, PASS)


class OldTestsStepSkipped(Base):
    def test_no_related_old_tests_when_full_run_covers_it(self):
        (self.worktree / "tests" / "test_old.py").write_text("# 全量会跑到的老测试\n", encoding="utf-8")
        with mock.patch("collab.dispatch._related_old_tests", return_value=["tests/test_old.py"]):
            card, _ = self.go(FakeRunner([]))
        from collab.flow import read_flow
        self.assertFalse([e for e in read_flow(self.cdir) if e.get("kind") == "老测试"])
        self.assertEqual(card.state, "待验收")

    def test_old_tests_the_full_run_cannot_find_still_run(self):
        (self.worktree / "tests" / "test_x.py").write_text("# 顶层有测试\n", encoding="utf-8")
        (self.worktree / "tests" / "integration").mkdir()   # 没有 __init__.py：全量找不到
        (self.worktree / "tests" / "integration" / "test_old.py").write_text("#\n", encoding="utf-8")
        with mock.patch("collab.dispatch._related_old_tests", return_value=["tests/integration/test_old.py"]):
            self.go(FakeRunner([]))
        from collab.flow import read_flow
        olds = [e for e in read_flow(self.cdir) if e.get("kind") == "老测试"]
        self.assertTrue(olds and "test_old" in olds[0]["summary"])

    def test_still_runs_without_full_run(self):
        with mock.patch("collab.dispatch._related_old_tests", return_value=["tests/test_old.py"]) as related:
            self.go(None)
        self.assertTrue(related.called)


class AloneRunner(FakeRunner):
    def __init__(self, runs, base=None, alone=None):
        super().__init__(runs, base)
        self.alone_result = alone or {}

    def alone(self, worktree, files, stop=None):
        return {f: self.alone_result.get(f, Verdict(FAIL, 1, FAIL_OUT)) for f in files}


class OnlyBrokenTogether(Base):
    def setUp(self):
        super().setUp()
        (self.worktree / "tests" / "test_x.py").write_text("# Python 项目\n", encoding="utf-8")

    def test_passes_alone_stops_for_a_person_instead_of_bouncing(self):
        # v0.96 B2：单独跑能过时先整套再跑一遍，第二遍还不过才停下（第二遍过了放行，见 test_v096_fullrun_same_batch）
        runner = AloneRunner([Verdict(FAIL, 1, FAIL_OUT), Verdict(FAIL, 1, FAIL_OUT)],
                             alone={"tests/test_old.py": Verdict(PASS, 0, "Ran 1 test\n\nOK\n")})
        card, calls = self.go(runner)
        self.assertEqual(len(calls["dev"]), 1, "不退回写代码的")
        self.assertEqual(card.state, "需Claude介入")
        from collab.flow import read_flow
        said = " ".join(e.get("summary", "") for e in read_flow(self.cdir) if e.get("kind") == "全量")
        self.assertIn("单独跑能过", said)

    def test_no_answer_from_alone_rerun_goes_to_a_person(self):
        from collab.judge import ENV_ERROR, TIMEOUT
        for result in (Verdict(TIMEOUT, None, ""), Verdict(ENV_ERROR, None, ""), None):
            with self.subTest(result=result):
                self.tearDown(); self.setUp()
                alone = {} if result is None else {"tests/test_old.py": result}
                runner = AloneRunner([Verdict(FAIL, 1, FAIL_OUT)], alone=alone)
                runner.alone = (lambda wt, files, stop=None, a=alone: dict(a))
                card, calls = self.go(runner)
                self.assertEqual(len(calls["dev"]), 1, "没得出结论不退回写代码的")
                self.assertEqual(card.state, "需Claude介入")

    def test_mixed_only_the_really_broken_goes_back(self):
        two = ("FAIL: test_a (test_old.T)\n----\nTraceback\nFAIL: test_b (test_other.T)\n----\nTraceback\n"
               "Ran 10 tests in 1s\n\nFAILED (failures=2)\n")
        runner = AloneRunner([Verdict(FAIL, 1, two), Verdict(PASS, 0, "Ran 10 tests\n\nOK\n")],
                             alone={"tests/test_old.py": Verdict(PASS, 0, "Ran 1 test\n\nOK\n"),
                                    "tests/test_other.py": Verdict(FAIL, 1, FAIL_OUT)})
        card, calls = self.go(runner)
        self.assertEqual(len(calls["dev"]), 2)
        self.assertIn("test_other", calls["dev"][1])
        self.assertNotIn("tests/test_old.py：", calls["dev"][1])

    def test_fails_alone_too_goes_back_to_dev(self):
        runner = AloneRunner([Verdict(FAIL, 1, FAIL_OUT), Verdict(PASS, 0, "Ran 10 tests\n\nOK\n")])
        card, calls = self.go(runner)
        self.assertEqual(len(calls["dev"]), 2, "单独跑也坏，照旧退回写代码的")


if __name__ == "__main__":
    unittest.main()
