"""走查那一行换个说法也要停在“等看页面”（11-25 train 全流程体验优化：三张施工单写“按走查步骤表检查”，一张都没认出来）。"""
import tempfile
import unittest
from pathlib import Path

from collab.planner import _order_look, _plan_from_order

ORDER = """# 施工单

## 一、要做什么（一句话）

改样式。

## 二、允许改哪些文件

- `lib/a.ts`

## 三、具体做法

### 1. 改

## 八、验收

- 验收命令：`npm test`
{extra}
"""


def look(extra):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "施工单.md").write_text(ORDER.format(extra=extra), encoding="utf-8")
        got = bool(_order_look(tmp, "施工单.md"))
        plan = _plan_from_order(tmp, "施工单.md")
    assert bool(plan["look_required"]) == got, "两条路要一致"
    return got


class WritesOtherWords(unittest.TestCase):
    def test_real_lines_from_train(self):
        for line in ("- 走查：放进项目前按下方走查步骤表在电脑端浏览器检查导师单轮复盘校准框与导师/学员终局荣誉台两处页面。",
                     "- 走查：放进项目前按下方走查步骤表在电脑端浏览器实测导师等待大厅中英文启动条的实时人数变化。",
                     "- **走查：**打开导师端点一遍",
                     "- **走查**：打开导师端点一遍"):
            self.assertTrue(look(line), line)

    def test_words_still_count(self):
        self.assertTrue(look("- 走查：不涉及（放进主项目前必须停下，打开页面对照原型看一遍）"))
        self.assertTrue(look("- 走查命令：`python3 w.py`\n- 走查：放进项目前要看页面"))

    def test_said_no(self):
        for line in ("- 走查：不涉及", "- 走查：不涉及（本单不改界面，只改跑卡流程后台）", "- 走查：不需要", "- 走查：无", "- 走查：无。",
                     "- 走查：没有", "- 走查：不用", "- 走查：", "- 走查:不涉及", "- **走查**：不涉及", ""):
            self.assertFalse(look(line), line)

    def test_no_followed_by_words(self):
        for line in ("- 走查：不需要手动走查", "- 走查：不涉及页面走查", "- 走查：不需要看页面", "- 走查：不用看页面",
                     "- 走查：无需人工走查", "- 走查：不涉及（本单不改界面，不用看页面）", "- 走查：没有页面改动"):
            self.assertFalse(look(line), line)

    def test_word_starting_with_no_is_not_a_no(self):
        for line in ("- 走查：无痕窗口打开大屏检查排名", "- 走查：不用登录，打开大屏检查排名", "- 走查：不需要登录打开大屏检查排名",
                     "- 走查：无需登录看页面"):
            self.assertTrue(look(line), line)

    def test_no_then_but(self):
        for line in ("- 走查：不涉及原型，但放进项目前要看页面", "- 走查：不需要对照原型但要看页面",
                     "- 走查：不涉及（无自动走查环境；放进主项目前打开页面看一遍）",
                     "- 走查：不需要对照原型，但放进项目前按下方走查步骤表在电脑端浏览器实测导师等待大厅",
                     "- 走查：不涉及原型但放进项目前在浏览器实测", "- 走查：无，不过合并前打开大厅点一遍"):
            self.assertTrue(look(line), line)

    def test_old_card_reads_the_same(self):
        import json
        from collab.look import look_info
        for extra, want in (("- 走查：按步骤表在浏览器实测", True), ("- 走查：不需要手动走查", False)):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "docs").mkdir()
                (Path(tmp) / "docs" / "施工单-x.md").write_text(ORDER.format(extra=extra), encoding="utf-8")
                cdir = Path(tmp) / ".collab" / "cards" / "CARD-001"
                cdir.mkdir(parents=True)
                (cdir / "需求卡.md").write_text("按施工单做：docs/施工单-x.md\n", encoding="utf-8")
                (cdir / "开工.json").write_text(json.dumps({"test_command": "true"}), encoding="utf-8")
                self.assertEqual(look_info(cdir)["required"], want, extra)

    def test_own_project_automatic_walk_unchanged(self):
        self.assertFalse(look("- 走查命令：`python3 tests/walk_x.py`\n- 走查步骤（走查脚本照这张表截图，复查员照这张表对照）："))
        self.assertFalse(look("- 走查命令：`python3 tests/walk_x.py`\n- 走查：脚本跑一遍，截图给复查员"))
        self.assertFalse(look("- 走查步骤（走查脚本照这张表截图）：\n"))

    def test_half_width_colon(self):
        self.assertTrue(look("- 走查:打开导师端点一遍"))


if __name__ == "__main__":
    unittest.main()
