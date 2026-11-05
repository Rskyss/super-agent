"""打开文件夹即用：协作包不再复制进项目；接入只建 .collab/ 并加进不提交名单；项目说明放 docs/。"""
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import collab
from collab.archive import archive_card
from collab.cli import main
from collab.flow import record
from collab.install import find_old_package, migrate
from collab.state import Card
from collab.workspace import init_collab, profile_path

PACKAGE_DIR = Path(collab.__file__).resolve().parent


def _cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = main(list(argv))
    return code, buf.getvalue()


def _git_project(root: Path, name="proj") -> Path:
    project = root / name
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return project


class ConnectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = _git_project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_connect_adds_only_the_records_folder_and_ignores_it(self):
        init_collab(self.project)
        self.assertTrue((self.project / ".collab" / "任务板.md").exists())
        self.assertFalse((self.project / "collab").exists())  # 不复制协作包
        self.assertIn(".collab/", (self.project / ".gitignore").read_text(encoding="utf-8").splitlines())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.project,
                                capture_output=True, text=True).stdout
        self.assertNotIn(".collab", status)  # git 看不到协作记录

    def test_existing_ignore_list_is_kept_and_not_duplicated(self):
        (self.project / ".gitignore").write_text("node_modules/\n.env", encoding="utf-8")
        init_collab(self.project)
        init_collab(self.project)
        lines = (self.project / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[:2], ["node_modules/", ".env"])
        self.assertEqual(lines.count(".collab/"), 1)

    def test_other_spellings_of_the_rule_count_as_already_ignored(self):
        (self.project / ".gitignore").write_text("/.collab\n", encoding="utf-8")
        init_collab(self.project)
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8"), "/.collab\n")

    def test_folder_without_git_gets_no_ignore_file(self):
        plain = self.root / "plain"
        plain.mkdir()
        init_collab(plain)
        self.assertFalse((plain / ".gitignore").exists())

    def test_records_readme_points_to_the_real_package(self):
        init_collab(self.project)
        text = (self.project / ".collab" / "README.md").read_text(encoding="utf-8")
        self.assertIn(str(PACKAGE_DIR / "规则.md"), text)
        self.assertIn(str(PACKAGE_DIR / "README.md"), text)

    def test_new_projects_get_no_profile_template_in_records(self):
        init_collab(self.project)
        self.assertFalse((self.project / ".collab" / "项目档案.md").exists())


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = _git_project(Path(self.tmp.name))
        init_collab(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_lives_in_project_docs(self):
        self.assertEqual(profile_path(self.project), self.project / "docs" / "项目说明.md")

    def test_old_profile_location_still_works(self):
        old = self.project / ".collab" / "项目档案.md"
        old.write_text("- 怎么测试：true\n", encoding="utf-8")
        self.assertEqual(profile_path(self.project), old)
        (self.project / "docs").mkdir()
        new = self.project / "docs" / "项目说明.md"
        new.write_text("- 怎么测试：npm test\n", encoding="utf-8")
        self.assertEqual(profile_path(self.project), new)  # 新位置优先

    def test_confirm_needs_a_written_profile(self):
        code, out = _cli("confirm-profile", str(self.project))
        self.assertNotEqual(code, 0)
        self.assertIn("docs/项目说明.md", out)

    def test_confirm_reads_the_new_location(self):
        (self.project / "docs").mkdir()
        (self.project / "docs" / "项目说明.md").write_text("- 怎么测试：（待填）\n", encoding="utf-8")
        code, out = _cli("confirm-profile", str(self.project))
        self.assertNotEqual(code, 0)
        self.assertIn("空白", out)
        (self.project / "docs" / "项目说明.md").write_text("- 怎么测试：npm test\n", encoding="utf-8")
        code, out = _cli("confirm-profile", str(self.project))
        self.assertEqual(code, 0, out)


class InstallNoLongerCopiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = _git_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_connects_without_copying_the_package(self):
        code, out = _cli("install", str(self.project))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.project / "collab").exists())
        self.assertTrue((self.project / ".collab").is_dir())
        self.assertIn("不需要", out)

    def test_install_update_never_touches_records(self):
        _cli("install", str(self.project))
        card = self.project / ".collab" / "cards" / "CARD-001"
        card.mkdir(parents=True)
        (card / "需求卡.md").write_text("保留我", encoding="utf-8")
        _cli("install", str(self.project), "--update")
        self.assertEqual((card / "需求卡.md").read_text(encoding="utf-8"), "保留我")

    def test_missing_project_is_refused(self):
        code, out = _cli("install", str(Path(self.tmp.name) / "不存在"))
        self.assertNotEqual(code, 0)


class MigrateTest(unittest.TestCase):
    """旧装法的项目：项目里有一份协作包副本 collab/，项目档案在 .collab/ 里。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = _git_project(Path(self.tmp.name))
        old = self.project / "collab"
        old.mkdir()
        (old / "规则.md").write_text("旧规则", encoding="utf-8")
        (old / "cli.py").write_text("# 旧包", encoding="utf-8")
        records = self.project / ".collab"
        (records / "cards" / "CARD-002").mkdir(parents=True)
        (records / "cards" / "CARD-002" / "需求卡.md").write_text("旧卡片", encoding="utf-8")
        (records / "项目档案.md").write_text("- 怎么测试：npm test\n", encoding="utf-8")
        (records / "README.md").write_text("规则书在项目里的 collab/规则.md，开工前必须先读。\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_package_is_recognised(self):
        self.assertEqual(find_old_package(self.project), self.project / "collab")

    def test_unrelated_collab_folder_is_not_mistaken_for_the_package(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        (other / "collab").mkdir()
        (other / "collab" / "mine.txt").write_text("别人的", encoding="utf-8")
        self.assertIsNone(find_old_package(other))
        report = migrate(other, remove_old_package=True)
        self.assertFalse(report["removed"])
        self.assertTrue((other / "collab" / "mine.txt").exists())

    def test_migrate_keeps_old_package_unless_told_to_remove(self):
        report = migrate(self.project)
        self.assertTrue(report["old_package"])
        self.assertFalse(report["removed"])
        self.assertTrue((self.project / "collab" / "规则.md").exists())

    def test_migrate_moves_profile_ignores_records_and_keeps_every_card(self):
        report = migrate(self.project, remove_old_package=True)
        self.assertTrue(report["removed"])
        self.assertFalse((self.project / "collab").exists())
        self.assertEqual((self.project / "docs" / "项目说明.md").read_text(encoding="utf-8"), "- 怎么测试：npm test\n")
        self.assertTrue(report["profile_moved"])
        self.assertEqual((self.project / ".collab" / "cards" / "CARD-002" / "需求卡.md").read_text(encoding="utf-8"), "旧卡片")
        self.assertIn(".collab/", (self.project / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertIn(str(PACKAGE_DIR / "规则.md"), (self.project / ".collab" / "README.md").read_text(encoding="utf-8"))

    def test_existing_new_profile_is_never_overwritten(self):
        (self.project / "docs").mkdir()
        (self.project / "docs" / "项目说明.md").write_text("新的说明", encoding="utf-8")
        report = migrate(self.project)
        self.assertFalse(report["profile_moved"])
        self.assertEqual((self.project / "docs" / "项目说明.md").read_text(encoding="utf-8"), "新的说明")

    def test_cli_migrate_reports_in_plain_words(self):
        code, out = _cli("migrate", str(self.project))
        self.assertEqual(code, 0, out)
        self.assertIn("旧协作包", out)
        self.assertTrue((self.project / "collab").exists())
        code, out = _cli("migrate", str(self.project), "--remove-old-package")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.project / "collab").exists())


class ArchiveKeepsProcessLocalTest(unittest.TestCase):
    def test_full_handoff_texts_and_live_output_stay_in_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            collab_dir = init_collab(project)
            cdir = collab_dir / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            Card("CARD-001", state="已合并").save(cdir / "状态.json")
            (cdir / "执行记录.md").write_text("- 写测试 | codex\n", encoding="utf-8")
            record(cdir, "派活", src="调度员", dst="测试员", summary="写测试", text="原文", live=True)
            target = archive_card(project, "CARD-001", "问候")
            self.assertTrue((target / "过程记录" / "执行记录.md").exists())   # 摘要照常归档
            self.assertFalse((target / "过程记录" / "流转").exists())          # 全文和实时输出不进项目文档
            self.assertFalse((target / "过程记录" / "流转.jsonl").exists())
            self.assertTrue((cdir / "流转.jsonl").exists())                     # 仍留在本机记录里


if __name__ == "__main__":
    unittest.main()
