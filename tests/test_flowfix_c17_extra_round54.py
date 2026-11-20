"""补充 C1.7 复查清单的边界情况和写方案提醒的位置。"""

import json
import tempfile
import unittest
from pathlib import Path

from collab.dispatch import _raw_list_problems, read_review_list
from collab.rulebook import rules_for
from collab.planner import _build_prompt


def _problem(level, source, kind):
    return {
        "级别": level,
        "来源": source,
        "类型": kind,
        "现象": "  【保存】按钮点了没反应  ",
        "位置": " app/editor.py:12 ",
        "碰上": "常见",
        "证据": {"方式": "推演", "对照": "  app/editor.py:12 【保存】未触发  "},
    }


def _reply(*problems):
    body = json.dumps({"结论": "不通过", "问题": problems}, ensure_ascii=False)
    return f"结论：不通过\n\n复查清单：\n```json\n{body}\n```\n"


class ReviewListNormalization(unittest.TestCase):
    def test_formal_reader_removes_inner_and_outer_spaces_only_from_classifications(self):
        problem = _problem("  【 【严重】 】  ", " 【 原来就有的 】 ", " 【 测试写错 】 ")

        data, errors = read_review_list(_reply(problem))

        self.assertEqual(errors, [])
        self.assertEqual(data["问题"], [dict(problem, 级别="严重", 来源="原来就有的", 类型="测试写错")])

    def test_fallback_normalizes_each_problem_even_if_a_required_field_is_missing(self):
        incomplete = _problem(" 【 严重 】 ", " 【 原来就有的 】 ", " 【 测试写错 】 ")
        del incomplete["位置"]
        complete = _problem("  轻微  ", "  这次改的  ", "  功能问题  ")
        reply = _reply(incomplete, complete)

        _, errors = read_review_list(reply)
        self.assertTrue(any("位置" in error for error in errors), errors)
        problems, _ = _raw_list_problems(reply)

        self.assertEqual(problems, [
            dict(incomplete, 级别="严重", 来源="原来就有的", 类型="测试写错"),
            dict(complete, 级别="轻微", 来源="这次改的", 类型="功能问题"),
        ])

    def test_formal_reader_rejects_each_unknown_classification_after_normalizing(self):
        problem = _problem(" 【 很严重 】 ", " 【 不清楚 】 ", " 【 其他问题 】 ")

        data, errors = read_review_list(_reply(problem))

        for field in ("级别", "来源", "类型"):
            with self.subTest(field=field):
                self.assertTrue(any(field in error for error in errors), errors)
        self.assertEqual(data["问题"], [dict(problem, 级别="很严重", 来源="不清楚", 类型="其他问题")])

    def test_both_readers_leave_non_string_classifications_as_they_are(self):
        problem = _problem(3, ["【这次改的】"], {"值": "【测试写错】"})
        reply = _reply(problem)

        data, errors = read_review_list(reply)
        for field in ("级别", "来源", "类型"):
            with self.subTest(field=field):
                self.assertTrue(any(field in error for error in errors), errors)
        self.assertEqual(data["问题"], [problem])
        problems, _ = _raw_list_problems(reply)
        self.assertEqual(problems, [problem])


class PlannerReminderPlacement(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as directory:
            self.prompt = _build_prompt(Path(directory), "# 改保存按钮\n", False)
        self.lines = self.prompt.splitlines()

    def test_prompt_opens_with_the_same_rules_reminder_as_other_roles(self):
        self.assertTrue(self.prompt.startswith(rules_for("写方案")))  # 11-20 起开头是规则包里这个角色的规则

    def test_card_only_reminder_immediately_follows_test_command_description(self):
        command_line = self.lines.index("- test_command：一条测试命令字符串。")
        self.assertEqual(
            self.lines[command_line + 1],
            "方案步骤里不要让施工方跑全部测试，全部测试由程序在复查时跑；test_command 只写本卡的测试。",
        )
