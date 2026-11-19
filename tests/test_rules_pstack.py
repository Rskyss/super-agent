"""《规则》加固（11-18 借鉴 pstack）：每个 AI 开工前必读的规则里，补上几条防“越改越乱”的话，去掉过时说法。

来由：pstack 的经验——下一个 AI 只看眼前的文件、照抄最近的例子；前人留下的防坑代码，后来的 AI 看不懂就删。
"""
import re
import unittest
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "collab" / "规则.md"


def text() -> str:
    return RULES.read_text(encoding="utf-8")


class NewRules(unittest.TestCase):
    def test_bug_test_reproduces_problem(self):
        t = text()
        self.assertIn("修问题时，测试要先重现用户看到的问题", t)
        self.assertIn("测试员交活时这条测试必须是不过的", t)

    def test_tester_and_developer_finish_lines_differ(self):
        # 测试员交的是还不过的测试，开发员交活才要求全过；写成一句“全部通过才算交活”，测试员会卡住（Codex 复查第 1 轮中 2）
        t = text()
        self.assertIn("测试员交的是能说明要求、此时还不过的测试", t)
        self.assertIn("开发员交活时，本卡测试必须全部通过", t)
        self.assertNotIn("本卡测试全部通过才算交活", t)

    def test_program_checks_not_overpromised(self):
        # 全部测试只认 Python 项目、页面检查要配置了才跑（collab/fullrun.py、dispatch.py；复查中 1）
        t = text()
        self.assertIn("项目支持时程序还会跑全部测试", t)
        self.assertIn("配置了页面检查时也会跑", t)
        self.assertIn("没跑到的检查，在回复里如实说明", t)

    def test_look_at_users_and_follow_existing_way(self):
        t = text()
        self.assertIn("还有谁在用", t)
        self.assertIn("照现有做法改，不另起一套", t)

    def test_revert_changes_that_did_not_help(self):
        self.assertIn("没帮上忙的改动要撤掉", text())

    def test_check_history_before_removing_odd_code(self):
        t = text()
        self.assertIn("看起来多余或奇怪的老代码", t)
        self.assertIn("git log", t, "要告诉 AI 用什么查来历")

    def test_guards_need_tests_and_notes_need_origin(self):
        t = text()
        self.assertIn("新加的防护要有测试守着", t)
        self.assertIn("写清为什么留、什么时候可以删", t)


class OutdatedWordsGone(unittest.TestCase):
    def test_no_old_product_name_or_dispatcher(self):
        t = text()
        self.assertNotIn("超级助手", t)
        self.assertNotIn("调度员", t)

    def test_no_scope_modes_that_do_not_exist(self):
        # 程序里没有 A/B/C 三种模式，越界一律撤销（collab/scope.py）；换种写法加回来也要抓到（复查中 3）
        # 只守“原版说法回来”（原句里的几个关键词，换标点也能抓到）。
        # 已知取舍（复查第 3 轮）：换个说法（分号、分行、近义词）加回 A/B/C，文字测试守不住；
        # 关键词再放宽会误伤“推送、合并、发版，都由用户决定”这类正确的话。改写由复查员把关。
        t = text()
        for pattern in (r"[ABC]\s*模式", r"模式\s*[ABC]", r"按这次需求设定的模式", r"A\s*程序自动撤销",
                        r"标出让用户定", r"允许并提醒"):
            self.assertIsNone(re.search(pattern, t), pattern)
        self.assertIn("会被程序直接撤销", t, "越界一律撤销，这句要在")

    def test_workers_not_asked_to_do_what_program_does(self):
        t = text()
        self.assertNotIn("在项目总台账里记一行", t, "台账由程序补写，干活的 AI 不写")
        self.assertNotIn("在真实页面上把用户会做的操作走一遍", t, "干活的 AI 在副本里做不到真实页面走查")


class KeptRules(unittest.TestCase):
    def test_existing_hard_lines_kept(self):
        t = text()
        for must in ("任务卡片", "允许改动的范围", "结论：通过", "汇报：",
                     "不要为了通过而修改或删除测试", "只改测试文件", "15 分钟"):
            self.assertIn(must, t)


if __name__ == "__main__":
    unittest.main()
