"""流程修理第四轮 Codex 复查第 3 轮挑出、Claude 最终验收时修的问题。"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _review_return_prompt, _triage, _unparsed_serious
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict

MIXED = ("结论：不通过\n1. 【轻微】【这次改的】提示字太小。\n"
         "- 【严重】【这次改的】重启后全部数据丢失。")


class Parsing(unittest.TestCase):
    def test_bullet_after_numbered_is_incomplete(self):
        from collab.flow import _numbered_items
        items = _numbered_items(MIXED, whole=True)
        self.assertTrue(_unparsed_serious(MIXED, items))

    def test_return_prompt_keeps_whole_text_when_incomplete(self):
        text = "结论：不通过\n【严重】【这次改的】重启后所有数据丢失。\n1. 【轻微】【这次改的】字小。"
        prompt = _review_return_prompt(text, "卡", "", "")
        self.assertIn("数据丢失", prompt)


class Flow(unittest.TestCase):
    def setUp(self):
        from test_flowfix4_converge import Base

        class T(Base):
            def runTest(self):
                pass
        self.t = T()
        self.t.setUp()

    def tearDown(self):
        self.t.tearDown()

    def test_mixed_list_not_closed(self):
        r1 = "结论：不通过\n1. 【中等】断网时一直重问。"
        card, calls = self.t.go([r1, MIXED, "结论：通过"])
        self.assertEqual(len(calls["dev"]), 3, "严重问题要退回")
        self.assertIn("数据丢失", calls["dev"][2])


class DraftGate(unittest.TestCase):
    def test_words_in_body_do_not_block_draft(self):
        from test_flowfix4_draft import Base

        class T(Base):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            rej = ["结论：不通过\n1. 【中等】程序把正常测试误报成测试本身写错了。"] + \
                  ["结论：不通过\n1. 【中等】【这次改的】程序把正常测试误报成测试本身写错了，第 %d 次。" % i for i in (2, 3)]
            _, seen = t.go(rej)
            self.assertEqual(len(seen["draft"]), 1)
        finally:
            t.tearDown()


class FullMore(unittest.TestCase):
    def go(self, now, base):
        from test_flowfix4_full import Base, FakeRunner

        class T(Base):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            card, calls = t.go(FakeRunner([now], base=base))
            return card, calls
        finally:
            t.tearDown()

    def test_base_timeout_goes_to_person(self):
        out = "FAIL: test_a (test_old.T)\n\nFAILED (failures=1)\n"
        card, calls = self.go(Verdict(FAIL, 1, out), {"tests/test_old.py": Verdict(TIMEOUT, None, "")})
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(calls["dev"]), 1, "不算写代码的错，不退回")

    def test_import_failure_goes_to_person(self):
        out = "ERROR: test_old (unittest.loader._FailedTest)\n\nFAILED (errors=1)\n"
        card, _ = self.go(Verdict(FAIL, 1, out), {"tests/test_old.py": Verdict(FAIL, 1, out)})
        self.assertEqual(card.state, "需Claude介入")


class Cleanup(unittest.TestCase):
    def test_new_session_grandchild_cleaned(self):
        import sys
        from collab.fullrun import run_command
        with tempfile.TemporaryDirectory() as d:
            mark = Path(d) / "pid"
            code = ("import subprocess, sys\n"
                    "p = subprocess.Popen(['sleep', '30'], start_new_session=True)\n"
                    f"open({str(mark)!r}, 'w').write(str(p.pid))\n"
                    "import time; time.sleep(2)\n")   # 测试跑着的时候起的工具
            v = run_command([sys.executable, "-c", code], d, dict(os.environ), 20)
            self.assertEqual(v.result, PASS)
            time.sleep(0.3)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(mark.read_text()), 0)

    def test_no_new_commands_after_exit(self):
        import subprocess
        from collab import fullrun
        fullrun._EXITING["on"] = True
        try:
            with self.assertRaises(subprocess.CalledProcessError):
                fullrun._quick(["true"])
        finally:
            fullrun._EXITING["on"] = False


class LaterWords(unittest.TestCase):
    def test_unplain_item_gets_placeholder(self):
        from collab.dispatch import _later_words
        words = _later_words(["【轻微】【这次改的】提示文字太小。", "【轻微】【原来就有的】reply_to 字段为空。"])
        self.assertEqual(len(words), 2)


class TesterCount(unittest.TestCase):
    def test_existing_tests_not_counted(self):
        from collab.progress import ProgressJob, read
        with tempfile.TemporaryDirectory() as d:
            wt, cdir = Path(d) / "wt", Path(d) / "card"
            (wt / "tests").mkdir(parents=True)
            cdir.mkdir()
            (wt / "tests" / "test_old.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
            job = ProgressJob(cdir, wt, "写测试", 1, [])
            job._tester_files()
            job.tick()
            self.assertEqual(read(cdir)["written"], 0)
            (wt / "tests" / "test_new.py").write_text("def test_b():\n    pass\n", encoding="utf-8")
            job.tick()
            self.assertEqual(read(cdir)["written"], 1)


if __name__ == "__main__":
    unittest.main()
