"""第三批四（⑳）：“继续改”比原施工单大很多时（新加的要改文件超过原范围一半，或改了验收标准），确认前提醒“像是重新设计”。

依据 docs/外部项目试跑修复/第三批/方案.md 第四节。只提醒，不拦；用户仍确认就记一笔。"""
import json
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import STATIC


def make_card(root: Path, allowed):
    cdir = root / ".collab" / "cards" / "CARD-001"
    cdir.mkdir(parents=True)
    text = "# 卡\n\n## 要做什么\n改\n\n## 允许改动的范围\n" + "".join(f"- {a}\n" for a in allowed)
    (cdir / "需求卡.md").write_text(text, encoding="utf-8")
    return cdir


class Note(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_card(self.root, ["lib/a.ts", "lib/b.ts", "app/page.tsx", "tests/a.test.ts"])

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, files=(), changes=None):
        from collab.tasks import redesign_note
        return redesign_note(self.root, "CARD-001", list(files), changes)

    def test_small_change_no_note(self):
        self.assertEqual(self.note(["lib/a.ts", "lib/c.ts"]), "", "新加 1 个，不到一半")

    def test_many_new_files(self):
        got = self.note(["lib/c.ts", "lib/d.ts", "lib/e.ts"])
        self.assertIn("像是重新设计", got)
        self.assertIn("新加 3 个文件", got)

    def test_criteria_changed(self):
        got = self.note([], [{"action": "add", "text": "终局榜要能导出"}])
        self.assertIn("像是重新设计", got)
        self.assertIn("改了验收标准", got)

    def test_empty_criteria_changes_no_note(self):
        self.assertEqual(self.note([], []), "")


class PageWarns(unittest.TestCase):
    def test_panel_confirms_redesign(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("res.d.redesign", js)
        self.assertIn('postCard("prepare_revise", words, cardId, files)', js)


if __name__ == "__main__":
    unittest.main()


class ClerkPath(unittest.TestCase):
    """第三批复查第 1 轮 11：行政确认框也提醒，按钮和行政都在 apply_revise 里记一笔。"""

    def test_proposal_event_carries_note(self):
        from desktop.clerk.tasks import event_from_proposal
        ev = event_from_proposal({"id": "x", "kind": "revise", "card": "CARD-001", "text": "改", "state": "open",
                                  "redesign": "像是重新设计"})
        self.assertEqual(ev.get("redesign"), "像是重新设计")

    def test_chat_page_shows_note(self):
        js = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("b.redesign", js)

    def test_note_recorded_once_in_apply(self):
        src = (Path(__file__).resolve().parent.parent / "desktop" / "clerk" / "tasks.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("_note_redesign(project, card_id, files, criteria_changes)"), 3, "定义一处、批准方案前和施工中各调用一处")
        server = (Path(__file__).resolve().parent.parent / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("redesign_warned", server, "只在一处记，别记两遍")


class Round2NoteOrder(unittest.TestCase):
    def test_note_before_scope_written(self):
        src = (Path(__file__).resolve().parent.parent / "desktop" / "clerk" / "tasks.py").read_text(encoding="utf-8")
        body = src.split("def apply_revise(", 1)[1]
        note_at = body.index("_note_redesign(project, card_id, files, criteria_changes)")
        revise_at = body.index("revise_card(project, card_id, words, files=files,", body.index("sync_copy(project, card_id)"))
        self.assertLess(note_at, revise_at, "要在新文件写进允许范围之前算提醒")
        self.assertIn("_note_redesign(project, card_id, None, criteria_changes)", body, "排队的修改也记")
