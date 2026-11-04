import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collab.archive import archive_card
from collab.cli import main
from collab.state import Card
from collab.workspace import init_collab


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        collab = init_collab(self.project)
        self.card_dir = collab / "cards" / "CARD-001"
        self.card_dir.mkdir(parents=True)
        Card("CARD-001", state="已合并", rework=1).save(self.card_dir / "状态.json")
        (self.card_dir / "需求卡.md").write_text("# 需求：加问候", encoding="utf-8")
        (self.card_dir / "方案.md").write_text("# 方案：两步", encoding="utf-8")
        (self.card_dir / "执行记录.md").write_text("- 开发 | cursor\n", encoding="utf-8")
        (self.card_dir / "测试结果.md").write_text(
            "\n## 第 1 轮判定\n结论：没过\n\n## 第 2 轮判定\n结论：过\n", encoding="utf-8")
        (self.card_dir / "复查结果.md").write_text(
            "\n## 复查（agy）\n结论：通过\n改动符合需求\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_land_in_feature_folder_with_familiar_names(self):
        target = archive_card(self.project, "CARD-001", "早上好问候")
        self.assertEqual(target, self.project / "docs" / "早上好问候")
        self.assertEqual((target / "需求共识.md").read_text(encoding="utf-8"), "# 需求：加问候")
        self.assertTrue((target / "方案.md").exists())
        self.assertTrue((target / "验收记录.md").exists())
        self.assertTrue((target / "过程记录" / "执行记录.md").exists())
        self.assertTrue((target / "过程记录" / "测试结果.md").exists())

    def test_acceptance_record_summarises_the_outcome(self):
        target = archive_card(self.project, "CARD-001", "早上好问候")
        text = (target / "验收记录.md").read_text(encoding="utf-8")
        self.assertIn("已合并", text)
        self.assertIn("返工 1 次", text)
        self.assertIn("测试：过", text)  # 取最后一轮的结论
        self.assertIn("独立复查：通过", text)

    def test_conclusion_is_extracted_even_from_raw_tool_output(self):
        (self.card_dir / "复查结果.md").write_text(
            '\n## 复查（agy）\n{"conversation_id":"x","response":"结论：不通过\\n\\n- 改错了"}\n',
            encoding="utf-8")
        target = archive_card(self.project, "CARD-001", "早上好问候")
        text = (target / "验收记录.md").read_text(encoding="utf-8")
        self.assertIn("独立复查：不通过", text)
        self.assertNotIn("conversation_id", text)

    def test_existing_files_are_never_overwritten(self):
        target = self.project / "docs" / "早上好问候"
        target.mkdir(parents=True)
        (target / "方案.md").write_text("旧方案", encoding="utf-8")
        archive_card(self.project, "CARD-001", "早上好问候")
        self.assertEqual((target / "方案.md").read_text(encoding="utf-8"), "旧方案")
        self.assertTrue((target / "方案（CARD-001）.md").exists())

    def test_feature_name_cannot_escape_docs_folder(self):
        for bad in ["../外面", "a/b", ""]:
            with self.assertRaises(ValueError):
                archive_card(self.project, "CARD-001", bad)

    def test_archive_is_logged_in_project_ledger(self):
        archive_card(self.project, "CARD-001", "早上好问候")
        ledger = (self.project / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn("docs/早上好问候/", ledger)

    def test_cli_archive_command(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = main(["archive", str(self.project), "CARD-001", "--feature", "早上好问候"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertTrue((self.project / "docs" / "早上好问候" / "验收记录.md").exists())

    def test_cli_archive_unknown_card_fails_cleanly(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = main(["archive", str(self.project), "CARD-404", "--feature", "x"])
        self.assertNotEqual(code, 0)
        self.assertIn("CARD-404", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
