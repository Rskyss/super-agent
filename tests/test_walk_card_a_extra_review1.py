"""CARD-022（卡 A）第一轮复查退回问题的补充自动化测试。

逐条对应复查退回的 2 个问题（不改产品代码，交活时这两条应为失败，待开发员修复后转绿）：
1. 页面取数据出错时，走查工具报“走查在‘某某’这一步之前页面出了问题”，摘要与右栏漏掉步骤名；
2. 本卡测试先失败一轮再通过时，第一次实际走查存进第 1 次文件夹，但页面检查结果标题写成“第 2 次走查”。
"""
import unittest

from collab.dispatch import run_card
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask
from test_walk_card_a_acceptance import WALK_CMD, WalkRunCase, shots_dir_of


class WalkReviewReturn1Test(WalkRunCase):
    """第一轮复查退回的 2 条问题回归测试。"""

    def test_1_page_api_error_walk_summary_shows_step_name_in_panel(self):
        # 复查退回第 1 条：tests/walk_tool.py 在页面取数据出错时抛出带前置文字的 WalkError：
        # “走查在“某某”这一步之前页面出了问题：...”
        # 摘要与右栏“跑测试”一格应写出卡在哪一步，不能退化成“命令报错（详情在交接里）”。
        cases = (
            (
                "右栏打开卡片",
                "Traceback (most recent call last):\n"
                "  File \"tests/walk_x.py\", line 18, in <module>\n"
                "walk_tool.WalkError: 走查在“右栏打开卡片”这一步之前页面出了问题："
                "panel 向后台要数据出错：500 http://127.0.0.1:8765/api/card",
            ),
            (
                "点开交接",
                "Traceback (most recent call last):\n"
                "WalkError: 走查在“点开交接”这一步之前页面出了问题："
                "chat 向后台要数据出错：502 http://127.0.0.1:8765/api/clerk/card/open",
            ),
        )
        for step, output in cases:
            with self.subTest(step=step):
                self.setUp()
                # 连续两次同样走查报错触发原地打转停下转人工，直接检查右栏最终展示
                self.walk_results = [("fail", output), ("fail", output)]
                card = self.run_walk()
                self.assertEqual(card.state, "需Claude介入")
                want = f"走查没过：卡在“{step}”这一步"

                events = self.walk_events()
                self.assertTrue(events)
                self.assertEqual(events[-1].get("result"), FAIL)
                self.assertEqual(events[-1].get("summary"), want)

                station = self.check_station()
                self.assertEqual(station.get("state"), "fail")
                self.assertEqual(station.get("detail"), want)
                self.assertNotIn("127.0.0.1", station.get("detail", ""))
                self.assertNotIn("/api/", station.get("detail", ""))

                log = (self.cdir / "页面检查结果.md").read_text(encoding="utf-8")
                self.assertIn(want, log)
                self.assertNotIn("127.0.0.1", log)
                self.assertNotIn("/api/", log)

    def test_2_first_walk_after_test_rework_heading_matches_folder_number(self):
        # 复查退回第 2 条：本卡测试第 1 轮先失败返工一次，第 2 轮测试通过才第一次跑走查，
        # 截图文件夹是 走查截图/第1次，页面检查结果.md 标题也必须是“## 第 1 次走查”，不能写成“## 第 2 次走查”。
        test_results = [
            Verdict(FAIL, 1, "本卡测试第 1 轮失败"),
            Verdict(PASS, 0, "本卡测试第 2 轮通过"),
        ]
        self.walk_results = [("ok", [("右栏", "")])]
        orig_judge = self.judge

        def judge_test_fail_then_pass(command, cwd, timeout):
            if shots_dir_of(command) is None:
                return test_results.pop(0)
            return orig_judge(command, cwd, timeout)

        card, _ = run_card(
            Card("CARD-001", state="待派发"),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_test_fail_then_pass,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        self.assertEqual(len(self.walk_commands), 1)
        self.assertEqual(
            shots_dir_of(self.walk_commands[0]).resolve(),
            (self.cdir / "走查截图" / "第1次").resolve(),
        )
        events = self.walk_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("folder"), "走查截图/第1次")

        log = (self.cdir / "页面检查结果.md").read_text(encoding="utf-8")
        self.assertIn("## 第 1 次走查", log)
        self.assertNotIn("## 第 2 次走查", log)


if __name__ == "__main__":
    unittest.main()
