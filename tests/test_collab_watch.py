import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from collab.state import Card
from collab.watch import snapshot


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "demo"
        self.project.mkdir()
        self.collab = self.project / ".collab"
        (self.collab / "cards" / "CARD-001").mkdir(parents=True)
        Card("CARD-001", state="开发中").save(self.collab / "cards" / "CARD-001" / "状态.json")
        self.start = datetime(2026, 11, 4, 10, 0, 0)
        (self.collab / "备份记录.md").write_text(
            f"| {self.start:%Y-%m-%d %H:%M} | pre-CARD-001 | CARD-001 |\n", encoding="utf-8")
        self.worktree = self.project.parent / "demo-worktrees" / "CARD-001"
        self.worktree.mkdir(parents=True)
        (self.worktree / "greet.py").write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_shows_state_elapsed_and_last_activity(self):
        now = self.start + timedelta(minutes=3)
        text = snapshot(self.project, "CARD-001", now=now)
        self.assertIn("开发中", text)
        self.assertIn("已用时 3 分", text)
        self.assertIn("最近动静", text)

    def test_warns_when_nothing_changed_for_long(self):
        future = datetime.now() + timedelta(minutes=20)  # 远远超过 10 分钟没动静
        text = snapshot(self.project, "CARD-001", now=future)
        self.assertIn("可能卡住", text)

    def test_no_warning_when_recent_activity(self):
        text = snapshot(self.project, "CARD-001", now=datetime.now() + timedelta(seconds=30))
        self.assertNotIn("可能卡住", text)

    def test_shows_latest_step_log_lines(self):
        (self.collab / "cards" / "CARD-001" / "执行记录.md").write_text(
            "- 10:01 | 写测试 | 执行者：codex | 副本累计改动：tests/test_greet.py\n", encoding="utf-8")
        text = snapshot(self.project, "CARD-001", now=self.start + timedelta(minutes=2))
        self.assertIn("写测试", text)


if __name__ == "__main__":
    unittest.main()
