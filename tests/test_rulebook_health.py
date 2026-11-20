"""规则包体检（用户 2026-11-20 定）：规则搬进 agents/ 以后会常改，这里守住“不能删的要点”。

要点清单写在 agents/README.md 里（“不能删的要点”“不能再出现的说法”两张表）。
改规则只改 agents/ 里的文件，换说法时连清单一起改，不用改这份测试。
替换了原来 7 个测试里逐字查《规则.md》的那几条（test_rules_pstack 等），它们查的要点全部进了清单。
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from collab import rulebook

AGENTS = Path(__file__).resolve().parents[1] / "agents"


class Health(unittest.TestCase):
    def test_rulebook_is_healthy(self):
        self.assertEqual(rulebook.health(), [])

    def test_checklists_are_not_empty(self):
        # 防有人把清单整张删掉让体检“全绿”
        index = (AGENTS / "README.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(rulebook.table(index, "不能删的要点")), 50)
        self.assertGreaterEqual(len(rulebook.table(index, "不能再出现的说法")), 10)


class CatchesBreakage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, self.root)

    def edit(self, rel, old, new):
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_deleted_key_point_is_caught(self):
        self.edit("手册/复查分级.md", "一两秒空档", "一会儿")
        problems = rulebook.health(root=self.root)
        self.assertTrue(any("一两秒空档" in p for p in problems), problems)

    def test_missing_registered_file_is_caught(self):
        (self.root / "手册" / "测试怎么写.md").unlink()
        self.assertTrue(any("测试怎么写.md" in p for p in rulebook.health(root=self.root)))

    def test_forbidden_words_are_caught_even_with_spaces(self):
        self.edit("角色/写代码.md", "只按施工单", "按 A 模式处理，只按施工单")
        self.assertTrue(any("A模式" in p for p in rulebook.health(root=self.root)))

    def test_old_auto_revert_mode_words_are_caught(self):
        # Codex 复查第 1 轮第 2 条：原 test_rules_pstack 查过“A 程序自动撤销”，清单要一条不少
        self.edit("通用/四道关卡.md", "会被程序直接撤销", "按 A 程序自动撤销，会被程序直接撤销")
        self.assertTrue(any("A程序自动撤销" in p for p in rulebook.health(root=self.root)))

    def test_forbidden_words_in_messages_are_caught(self):
        # Codex 复查 T2～T6 第 1 条：交代文件也要查；例外只对登记的那几份、那一条说法
        self.edit("交代/复查/每轮复查.md", "不要修改任何文件。", "按 A 模式处理。不要修改任何文件。")
        self.assertTrue(any("每轮复查.md" in p and "A模式" in p for p in rulebook.health(root=self.root)))

    def test_exception_only_covers_listed_files(self):
        self.edit("交代/复查/每轮复查.md", "不要修改任何文件。", "调度员说过。不要修改任何文件。")
        self.assertTrue(any("每轮复查.md" in p and "调度员" in p for p in rulebook.health(root=self.root)))

    def test_unregistered_file_is_caught(self):
        (self.root / "角色" / "新角色.md").write_text("# 新\n", encoding="utf-8")
        self.assertTrue(any("新角色.md" in p for p in rulebook.health(root=self.root)))


if __name__ == "__main__":
    unittest.main()
