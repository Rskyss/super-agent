"""流程修理第四轮 Codex 复查第 2 轮挑出的问题。"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collab.agents import _build
from collab.dispatch import _places, _triage, _unparsed_serious
from collab.fullrun import STOPPED, applicable, copy_tree, failing_files, summary_count
from collab.judge import FAIL, Verdict
from collab.progress import tally


class QwenReadOnly(unittest.TestCase):
    def test_qwen_draft_is_read_only(self):
        cmd = _build("qwen", "起草", {"qwen": "qwen4-max-preview", "codex_effort": "xhigh",
                                       "codex_sandbox": "read-only"})
        self.assertIn("read-only", cmd)
        self.assertNotIn("workspace-write", cmd)


class TriageTags(unittest.TestCase):
    def test_only_leading_tags_count(self):
        item = "【中等】【这次改的】复查把新问题标成【原来就有的】就直接放过；正文还提到【轻微】。"
        back, later = _triage([item])
        self.assertEqual(back, [item])

    def test_unnumbered_serious_blocks_close(self):
        text = "结论：不通过\n【严重】【这次改的】重启后所有数据丢失。\n1. 【轻微】【这次改的】字小。"
        self.assertTrue(_unparsed_serious(text, ["【轻微】【这次改的】字小。"]))
        self.assertFalse(_unparsed_serious("结论：不通过\n1. 【轻微】字小。", ["【轻微】字小。"]))


class Places(unittest.TestCase):
    def test_link_target_and_full_path(self):
        a = _places(["x [index.js:10](/w/apps/admin/src/index.js:10)"], "/w")
        b = _places(["y [index.js:90](/w/apps/shop/src/index.js:90)"], "/w")
        self.assertEqual(a, {("apps/admin/src/index.js", "10")})
        self.assertFalse({p for p, _ in a} & {p for p, _ in b})


class FullRunMore(unittest.TestCase):
    def test_unexpected_success_counts(self):
        self.assertEqual(summary_count("FAILED (failures=1, unexpected successes=1)"), 2)

    def test_nested_only_project_applies(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tests" / "pkg").mkdir(parents=True)
            (Path(d) / "tests" / "pkg" / "test_old.py").write_text("", encoding="utf-8")
            self.assertTrue(applicable(d))

    def test_new_format_without_root(self):
        files = failing_files("FAIL: test_a (test_money.T.test_a)\nFAIL: test_b (test_money.T)\n")
        self.assertEqual(set(files), {"tests/test_money.py"})

    def test_copy_can_be_stopped(self):
        ev = threading.Event()
        ev.set()
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "src").mkdir()
            (Path(d) / "src" / "a.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(subprocess.CalledProcessError):
                copy_tree(Path(d) / "src", Path(d) / "dst", ev)

    def test_unexpected_success_goes_to_person(self):
        from test_flowfix4_full import Base, FakeRunner
        out = "FAIL: test_a (test_old.T)\n\nFAILED (failures=1, unexpected successes=1)\n"

        class T(Base):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            card, _ = t.go(FakeRunner([Verdict(FAIL, 1, out)], base={"tests/test_old.py": Verdict(FAIL, 1, out)}))
            self.assertEqual(card.state, "需Claude介入")
        finally:
            t.tearDown()


class ProgressMore(unittest.TestCase):
    def test_subtests_count_method_once(self):
        out = ("FAIL: test_b (t.T) (x=1)\nFAIL: test_b (t.T) (x=2)\n"
               "Ran 2 tests in 0.1s\n\nFAILED (failures=2)\n")
        self.assertEqual(tally(out), (1, 2))

    def test_import_error_reported(self):
        from collab import progress
        from collab.judge import PASS
        with tempfile.TemporaryDirectory() as d:
            wt, cdir = Path(d) / "wt", Path(d) / "card"
            (wt / "tests").mkdir(parents=True)
            cdir.mkdir()
            (wt / "tests" / "test_a.py").write_text("import nothing_here\n", encoding="utf-8")
            job = progress.ProgressJob(cdir, wt, "开发", 1, ["tests/test_a.py"], gap=0)
            job.tick()
            self.assertIn("跑不起来", progress.read(cdir).get("error", ""))

    def test_tester_files_outside_tests(self):
        from collab import progress
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pkg").mkdir()
            (Path(d) / "pkg" / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
            job = progress.ProgressJob(d, d, "写测试", 1, [])
            job.started = 0
            self.assertIn("pkg/test_x.py", job._tester_files())


class CheckMore(unittest.TestCase):
    def test_ls_count_must_be_real(self):
        from collab import check
        from collab.agents import AgentResult

        def ask(tool, prompt, folder, expect_file=None, **kw):
            n = int((Path(folder) / check.QUESTION_FILE).read_text())
            (Path(folder) / expect_file).write_text(f"{n + 1}\n999999\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1)
        with patch.object(check, "ask", ask), patch.object(check.shutil, "which", lambda c: "/bin/" + c), \
                patch.object(check, "tool_version", lambda t: ""):
            self.assertFalse(check.check_tool("codex", {})["ok"])

    def test_version_query_cleans_children(self):
        from collab import check
        with tempfile.TemporaryDirectory() as d:
            mark = Path(d) / "pid"
            fake = Path(d) / "codex"
            fake.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {mark}\necho codex 1\n", encoding="utf-8")
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": f"{d}:/usr/bin:/bin"}):
                self.assertEqual(check.tool_version("codex"), "codex 1")
            time.sleep(0.3)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(mark.read_text()), 0)

    def test_blocks_usage_warn(self):
        from collab.blocks_check import _usage_warn
        r = {"ok": True, "reason": "通了 · 3 秒"}
        _usage_warn(r, "tokens used\n451,861\n")
        self.assertEqual(r.get("warn"), "usage")

    def test_run_reminder_uses_actual_tools(self):
        from collab import cli
        seen = {}
        with patch("collab.check.version_changes", lambda p, team=None: seen.setdefault("team", team) and []):
            cli._remind_upgrades("/x", "CARD-001", {"test": "codex", "dev": "agy", "review": "codex"})
        self.assertEqual(seen["team"]["developer"], "agy")


if __name__ == "__main__":
    unittest.main()
