"""CARD-028（卡 A：施工单缺节就不开工）第一轮复查退回问题的补充自动化测试。

逐条对应复查退回需改产品代码的 1 个问题（只写测试，不改产品代码；交活时本条应为失败，待开发员修复后转绿）：
1. 缺“允许改哪些文件”时，失败详情给出的补法漏了路径两边的反引号；
   用户照失败详情补上允许改的文件后点“重写方案”，依然会报缺。
"""
import json
import re
import unittest

from test_card_gap_a_acceptance import OrderGapBase, order_text


def _extract_allowed_line_example(hint_body: str):
    """从失败详情的补法说明里摘出教用户写的单条文件行写法，返回 (列表行模板, 是否带反引号)。"""
    quoted = re.search(r"-[ \t]+`([^`\s][^`\n]*)`", hint_body)
    if quoted:
        return "- `src/app.py`", True, quoted.group(1)
    plain = re.search(r'[“"`](-[ \t]+[^”"`\n]+)[”"`]', hint_body)
    if plain:
        return "- src/app.py", False, plain.group(1)
    return None, False, ""


class CardGapAReviewReturn1Test(OrderGapBase):
    """第一轮复查退回的 1 条产品问题回归测试。"""

    def test_1_allowed_files_hint_followed_by_user_allows_replan(self):
        # 1. 施工单缺“允许改哪些文件”：首次写方案应失败并给出补法详情
        self.write_order(order_text(allowed=False))
        cdir = self.card_by_order()
        fail = self.assert_blocked(cdir, "允许改哪些文件")
        full = self.full_text(cdir, fail)

        lines = [line for line in full.splitlines() if not line.startswith("施工单：")]
        hint_body = "\n".join(lines)
        self.assertIn("## 二、允许改哪些文件", hint_body, f"失败详情应给出允许改哪些文件的节标题：{full}")

        # 2. 按失败详情教的写法把“允许改哪些文件”补回施工单，再点“重写方案”
        filled_line, has_backticks, raw_example = _extract_allowed_line_example(hint_body)
        self.assertIsNotNone(
            filled_line,
            f"失败详情应给出单条文件的写法示例（如 - `路径`）：{full}",
        )
        from collab.workspace import PLACEHOLDER

        self.assertNotIn(PLACEHOLDER, raw_example, f"示例里不能带空白标记：{full}")

        repaired_allowed = f"## 二、允许改哪些文件\n\n{filled_line}\n\n"
        self.write_order(order_text(allowed=True, allowed_text=repaired_allowed))

        self.assertEqual(
            self.gaps(),
            [],
            f"照失败详情给的写法（{filled_line}）补上允许改的文件后，不应再报缺；当前失败详情：\n{full}",
        )
        self.assertTrue(
            has_backticks,
            f"允许改哪些文件的补法示例应照施工单模板用反引号包住路径：{full}",
        )
        self.assertEqual(self.plan(), 0, "照失败详情补好允许改的文件后，重写方案应直接成功")
        self.assertEqual(self.ai_calls(), 0, "补好后应照施工单直接生成，不调用 AI")
        self.assertTrue((cdir / "开工.json").exists(), "重写方案成功后应生成开工.json")
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("test_command"), "python3 -m unittest tests.test_app")
        req_text = (cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertIn("- src/app.py", req_text, "补上的允许改动路径应写入需求卡范围")


if __name__ == "__main__":
    unittest.main()
