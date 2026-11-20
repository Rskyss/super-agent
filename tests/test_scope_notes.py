"""允许改动的范围里，文件名后面跟着括号备注时，备注不算文件名的一部分（CARD-020 教训，2026-11-20）。

CARD-020 的“粘给行政的话”写了“panel.html（只加一行）”，行政照抄进卡片，程序把
“desktop/static/panel.html（只新增一行）”整串当成文件名，写代码的加的那一行每轮都被当越界撤掉，白跑 3 轮。
"""
import unittest

from collab.scope import out_of_scope, read_allowed

CARD = """# 卡

## 允许改动的范围

- desktop/static/panel.js
- desktop/static/panel.html（只新增一行）
- tests/test_x_extra*.py（仅新建）
- tests/x_extra*.js (must be called from Python)
- docs/说明（第二版）/a.md
"""


class NotesAfterPaths(unittest.TestCase):
    def setUp(self):
        self.allowed = read_allowed(CARD)

    def test_list_is_kept_as_written(self):
        # 范围原样保留（不改写卡片内容），只在比对时认得备注
        self.assertIn("desktop/static/panel.html（只新增一行）", self.allowed)

    def test_files_with_notes_are_in_scope(self):
        changed = ["desktop/static/panel.html", "tests/test_x_extra_a.py", "tests/x_extra_b.js"]
        self.assertEqual(out_of_scope(changed, self.allowed), [])

    def test_brackets_inside_a_path_are_kept(self):
        # 括号在路径中间（文件夹名里）不是备注，不能截掉
        self.assertIn("docs/说明（第二版）/a.md", self.allowed)

    def test_folder_whose_name_ends_with_brackets_is_kept(self):
        # Codex 复查：项目里真有“docs/右栏显示小修（施工单）”这种以括号结尾的文件夹，不能当备注截掉
        for folder in ("docs/右栏显示小修（施工单）", "docs/v1.0（施工单）"):
            allowed = read_allowed(f"## 允许改动的范围\n\n- {folder}\n")
            inside, other = folder + "/方案.md", folder.split("（")[0] + "/方案.md"
            self.assertEqual(out_of_scope([inside], allowed), [], folder)
            self.assertEqual(out_of_scope([other], allowed), [other], "不能因此放行另一个文件夹：" + folder)

    def test_adding_scope_twice_does_not_duplicate(self):
        from collab.tasks import _add_scope
        body = CARD
        for _ in range(2):
            body = _add_scope(body, ["desktop/static/panel.html（只新增一行）", "docs/右栏显示小修（施工单）"])
        self.assertEqual(body.count("panel.html"), 1, body)
        self.assertEqual(body.count("右栏显示小修（施工单）"), 1, body)

    def test_other_files_still_out_of_scope(self):
        self.assertEqual(out_of_scope(["desktop/view.py"], self.allowed), ["desktop/view.py"])


if __name__ == "__main__":
    unittest.main()
