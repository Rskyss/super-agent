import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import collab
from collab.cli import main
from collab.install import InstallError, install_into
from collab.usage import find_latest_session

PACKAGE_DIR = Path(collab.__file__).resolve().parent


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_copies_package_and_creates_workspace(self):
        target = install_into(self.project)
        self.assertEqual(target, (self.project / "collab").resolve())  # macOS 的 /var 实为 /private/var
        self.assertTrue((target / "cli.py").exists())
        self.assertTrue((target / "规则.md").exists())
        self.assertTrue((self.project / ".collab" / "任务板.md").exists())
        self.assertEqual(list(target.rglob("__pycache__")), [])

    def test_second_install_needs_explicit_update(self):
        install_into(self.project)
        with self.assertRaises(InstallError):
            install_into(self.project)
        (self.project / "collab" / "cli.py").write_text("被改坏了", encoding="utf-8")
        install_into(self.project, update=True)
        self.assertNotEqual((self.project / "collab" / "cli.py").read_text(encoding="utf-8"), "被改坏了")

    def test_update_never_touches_collab_records(self):
        install_into(self.project)
        card = self.project / ".collab" / "cards" / "CARD-001"
        card.mkdir(parents=True)
        (card / "需求卡.md").write_text("保留我", encoding="utf-8")
        install_into(self.project, update=True)
        self.assertEqual((card / "需求卡.md").read_text(encoding="utf-8"), "保留我")

    def test_unrelated_collab_folder_is_not_overwritten(self):
        other = self.project / "collab"
        other.mkdir()
        (other / "mine.txt").write_text("别人的东西", encoding="utf-8")
        with self.assertRaises(InstallError):
            install_into(self.project, update=True)
        self.assertEqual((other / "mine.txt").read_text(encoding="utf-8"), "别人的东西")

    def test_cannot_install_into_its_own_home(self):
        with self.assertRaises(InstallError):
            install_into(PACKAGE_DIR.parent)

    def test_missing_project_is_refused(self):
        with self.assertRaises(InstallError):
            install_into(self.project / "不存在")

    def test_cli_install_command(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = main(["install", str(self.project)])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertTrue((self.project / "collab" / "规则.md").exists())


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
