"""49 号单：放弃卡片时一起删掉改好的版本。和放进项目时是同一个做法，只写一处。"""
import subprocess
from pathlib import Path

from acceptance_helpers import ROOT, STATIC, ServerCase
from collab.flow import read_flow


class Discard(ServerCase):
    def discard(self):
        return self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "discard"})

    def card(self):
        return self.make_card(state="待验收", plan=True)

    def test_copy_is_removed_and_project_stays(self):
        self.card()
        copy = self.make_copy()
        (copy / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
        self.assertEqual(self.discard()[0], 200)
        self.assertEqual(self.state(), "已放弃")
        self.assertFalse(copy.exists())
        self.assertEqual((Path(self.project) / "src" / "app.py").read_text(encoding="utf-8"), "x = 1\n")
        listed = subprocess.run(["git", "worktree", "list"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertNotIn("CARD-001", listed)

    def test_no_copy_is_fine(self):
        self.make_card(state="待对齐", plan=True)
        self.assertEqual(self.discard()[0], 200)
        self.assertEqual(self.state(), "已放弃")
        self.assertFalse(any("没能删掉" in e.get("summary", "") for e in read_flow(self.collab / "cards" / "CARD-001")))

    def test_failed_removal_is_noted_and_card_still_discarded(self):
        cdir = self.card()
        folder = Path(self.project).parent / (Path(self.project).name + "-worktrees") / "CARD-001"
        folder.mkdir(parents=True)   # 不是真副本，删不掉
        status, answer = self.discard()
        self.assertEqual((status, answer), (200, {"ok": True}))
        self.assertEqual(self.state(), "已放弃")
        self.assertTrue(any("改好的版本没能删掉" in e.get("summary", "") for e in read_flow(cdir)))

    def test_refused_discard_keeps_the_copy(self):
        self.make_card(state="已合并")
        copy = self.make_copy()
        self.assertNotEqual(self.discard()[0], 200)
        self.assertTrue(copy.exists())

    def test_confirm_says_so(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("这次的改动不会进入项目，你的项目不会变。改好的版本也会删除。", js)

    def test_one_way_of_removing(self):
        merge = (ROOT / "collab" / "merge.py").read_text(encoding="utf-8")
        self.assertEqual(merge.count('"worktree", "remove"'), 1, "放进项目和放弃用同一个做法")
        self.assertNotIn('"worktree", "remove"', (ROOT / "desktop" / "server.py").read_text(encoding="utf-8"))
