import tempfile
import unittest
from pathlib import Path

from collab.state import Card
from collab.workspace import init_collab, render_board


class WorkspaceTest(unittest.TestCase):
    def test_init_creates_collab_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = init_collab(tmp)
            for name in ["README.md", "项目档案.md", "任务板.md", "备份记录.md"]:
                self.assertTrue((collab / name).exists(), name)
            self.assertTrue((collab / "cards").is_dir())

    def test_init_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = init_collab(tmp)
            (collab / "项目档案.md").write_text("我的档案")
            init_collab(tmp)
            self.assertEqual((collab / "项目档案.md").read_text(), "我的档案")

    def test_board_reflects_card_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = init_collab(tmp)
            for cid, state, rework in [("CARD-001", "开发中", 1), ("CARD-002", "已合并", 0)]:
                card_dir = collab / "cards" / cid
                card_dir.mkdir(parents=True)
                Card(cid, state=state, rework=rework).save(card_dir / "状态.json")
            render_board(collab)
            board = (collab / "任务板.md").read_text()
            self.assertIn("CARD-001", board)
            self.assertIn("开发中", board)
            self.assertIn("返工 1", board)
            self.assertIn("已合并", board)


if __name__ == "__main__":
    unittest.main()
