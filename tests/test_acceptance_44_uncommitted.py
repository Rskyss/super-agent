"""44 号单：开工前提醒没提交的文件。施工从最后一次提交开始，这些文件它看不到。"""
from pathlib import Path

from acceptance_helpers import STATIC, ServerCase
from collab.dispatch import uncommitted_count


class Count(ServerCase):
    def test_clean(self):
        self.assertEqual(uncommitted_count(self.project), 0)

    def test_counts_changed_and_new_but_not_collab(self):
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        Path(self.project, "新文件.py").write_text("y\n", encoding="utf-8")
        self.make_card()
        self.assertEqual(uncommitted_count(self.project), 2)

    def test_not_a_git_project(self):
        self.assertEqual(uncommitted_count(self.tmp), 0)

    def test_service(self):
        Path(self.project, "新文件.py").write_text("y\n", encoding="utf-8")
        status, answer = self.get("/api/clerk/uncommitted", project=self.project, token="口令")
        self.assertEqual((status, answer), (200, {"count": 1, "files": ["新文件.py"]}))
        self.assertEqual(self.get("/api/clerk/uncommitted", project=self.project)[0], 403)

    def test_panel_asks_before_starting(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("/api/clerk/uncommitted", js)
        # C2：浏览器自带弹窗换成三按钮小框，说法在 PanelCore.startDialogText 里
        self.assertIn("施工会看不到它们", js)
        self.assertIn('act === "approve"', js)
