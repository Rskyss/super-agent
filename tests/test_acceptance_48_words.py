"""48 号单：“归档”只留一个意思。卡片放进项目时存记录，说成“存入项目文档”。"""
from pathlib import Path

from acceptance_helpers import ROOT, ProjectCase
from collab.archive import archive_card
from collab.flow import read_flow


class Words(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="已合并", title="改按钮")
        self.target = archive_card(self.project, "CARD-001", "改按钮")

    def test_flow_record(self):
        words = [e.get("summary", "") for e in read_flow(self.cdir)]
        self.assertTrue(any("记录已存入项目文档" in w and "docs/改按钮" in w for w in words), words)
        self.assertFalse(any("归档" in w for w in words), words)

    def test_ledger(self):
        text = (Path(self.project) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn("记录已存入项目文档", text)
        self.assertNotIn("归档", text)

    def test_acceptance_record(self):
        text = (self.target / "验收记录.md").read_text(encoding="utf-8")
        self.assertIn("存入时间", text)
        self.assertNotIn("归档", text)

    def test_failure_note(self):
        text = (ROOT / "collab" / "merge.py").read_text(encoding="utf-8")
        self.assertIn("记录没能存入项目文档", text)
        self.assertNotIn("记录没能归档", text)

    def test_files_still_go_to_the_same_place(self):
        self.assertEqual(self.target, Path(self.project) / "docs" / "改按钮")
        self.assertTrue((self.target / "验收记录.md").is_file())

    def test_command_name_stays(self):
        self.assertIn('"archive"', (ROOT / "collab" / "cli.py").read_text(encoding="utf-8"))
