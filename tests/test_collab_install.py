"""v0.2 起“安装”不再复制协作包，相关测试见 test_collab_connect.py；这里只保留额度记录找会话的测试。"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from collab.install import InstallError, install_into
from collab.usage import find_latest_session


class InstallStillRefusesMissingProjectTest(unittest.TestCase):
    def test_missing_project_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InstallError):
                install_into(Path(tmp) / "不存在")


class FindLatestSessionTest(unittest.TestCase):
    def test_picks_most_recent_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "proj-a").mkdir()
            (base / "proj-b").mkdir()
            old = base / "proj-a" / "old.jsonl"
            new = base / "proj-b" / "new.jsonl"
            old.write_text("{}\n")
            new.write_text("{}\n")
            past = time.time() - 3600
            os.utime(old, (past, past))
            self.assertEqual(find_latest_session(base), new)

    def test_no_sessions_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_latest_session(Path(tmp)))
            self.assertIsNone(find_latest_session(Path(tmp) / "不存在"))


if __name__ == "__main__":
    unittest.main()
