import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collab.dispatch as dispatch
from collab.judge import FAIL, TIMEOUT, judge
from collab.progress import ProgressJob


class RedListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cdir = Path(self.tmp.name) / "CARD-001"
        self.cdir.mkdir()

    def write_worktree(self, tests):
        worktree = Path(self.tmp.name) / "wt"
        (worktree / "tests").mkdir(parents=True)
        for name, body in tests.items():
            (worktree / "tests" / name).write_text(body, encoding="utf-8")
        return worktree

    def test_missing_later_red_name_cannot_show_full_progress(self):
        worktree = self.write_worktree({
            "test_a.py": (
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self): pass\n"
            ),
            "test_b.py": (
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_b(self): self.fail('第二段失败')\n"
            ),
        })
        (self.cdir / "开工前没过的测试.json").write_text(
            json.dumps({"tests": ["test_a.T.test_a"]}, ensure_ascii=False), encoding="utf-8")
        command = (
            'python3 -m unittest discover -s tests -p "test_a.py" && '
            'python3 -m unittest discover -s tests -p "test_b.py"'
        )

        got = ProgressJob(cdir=self.cdir, worktree=worktree, step="开发", seq=1,
                          test_files=["tests/test_a.py", "tests/test_b.py"],
                          test_command=command).compute()

        self.assertIn("测试没过", got.get("error", ""))
        self.assertNotIn("total", got)

    def test_timeout_after_failure_does_not_write_partial_red_list(self):
        worktree = self.write_worktree({
            "test_a.py": (
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self): self.fail('前面失败')\n"
            ),
            "test_slow.py": (
                "import time\n"
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_slow(self): time.sleep(2)\n"
            ),
        })
        command = (
            'python3 -m unittest discover -s tests -p "test_a.py" && '
            'python3 -m unittest discover -s tests -p "test_slow.py"'
        )

        with patch.object(dispatch, "_TEST_TIMEOUT", 1):
            verdict = dispatch._red_check_one(
                self.cdir, "写测试", ["/bin/bash", "-o", "pipefail", "-c", command],
                judge, worktree,
            )

        self.assertEqual(verdict.result, TIMEOUT)
        self.assertFalse((self.cdir / "开工前没过的测试.json").exists())
