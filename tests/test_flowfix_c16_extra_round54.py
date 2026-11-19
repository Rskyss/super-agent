"""C1.6 第四轮复查退回的六个场景。"""

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import text_of
from collab.judge import FAIL, PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from collab.tasks import revise_card
from test_flowfix4_converge import Base
from test_flowfix4_full import FakeRunner
from test_flowfix_c15_acceptance import item, review
from test_flowfix_c16_acceptance import checked, events, ledger_text, rare


class ReviewReturns(Base):
    def run_reviews(self, replies, *, runner=None, card=None, test_file="test_export.py"):
        replies = iter(replies)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            task = events(self.cdir, "派活")[-1]
            if task["dst"] == "复查员":
                answer = next(replies, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            else:
                if task["step"] == "写测试":
                    (self.worktree / "tests" / test_file).write_text("", encoding="utf-8")
                answer = "汇报：已交活"
            return AgentResult(ok=True, output="", attempts=1, final=answer)

        card, _ = run_card(
            card or Card(self.cdir.name, state="待派发"),
            self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=ask, judge_fn=lambda *a, **kw: Verdict(PASS, 0, "本卡测试通过"),
            reviewer_adapter="codex", full_runner=runner,
        )
        return card

    def review_prompts(self):
        return [
            text_of(self.cdir, task)
            for task in events(self.cdir, "派活")
            if task["dst"] == "复查员"
        ]

    def phase(self):
        return card_phase(str(self.collab.parent), self.cdir.name)

    def continue_editing(self):
        revise_card(self.collab.parent, self.cdir.name, "再看一下导出页的提示。")
        return Card.load(self.cdir / "状态.json")

    def test_skipped_full_run_cannot_clear_two_used_returns(self):
        pending = item(what="导出时没保留所选的排序")
        still_unfixed = checked("不通过", checks=[(1, "没改好")])
        runner = FakeRunner([Verdict(PASS, 0, "全部测试通过")])

        card = self.run_reviews(
            [review("不通过", [pending]), still_unfixed, still_unfixed],
            runner=runner, test_file="export.test.js",
        )

        self.assertEqual(len(events(self.cdir, "退回")), 2)
        self.assertTrue(any(e.get("phase") == "跳过" for e in events(self.cdir, "全量")))
        self.assertEqual(runner.calls, 0, "这个副本里没有能跑的 Python 测试")
        self.assertEqual(card.state, "需Claude介入", "配置了全部测试却没跑成，退满也要停下")
        self.assertIn("全部测试", self.phase()["detail"])

    # test_failed_rewrite_keeps_unconfirmed_severe_issue_for_next_review：C1.8 删除——它测的“整段原话退回后的下一轮”已不存在（重写仍看不懂直接停下转 Claude），登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节

    def test_same_issue_reclassified_as_bad_test_is_sent_to_tester(self):
        pending = item(what="导出后生成时间与测试预期不一致", where="tests/test_export.py:24")
        bad_test = dict(pending, 类型="测试写错")

        self.run_reviews([
            review("不通过", [pending]),
            checked("不通过", checks=[(1, "没改好")]),
            checked("不通过", [bad_test], [(1, "没改好")]),
            review("通过"),
        ])

        corrections = [
            task for task in events(self.cdir, "派活")
            if task["dst"] == "测试员" and task["step"] == "改测试"
        ]
        self.assertEqual(len(corrections), 1, "同一位置改判测试写错，退满仍要派一次改测试")
        prompt = text_of(self.cdir, corrections[0])
        self.assertIn(bad_test["现象"], prompt)
        self.assertIn(bad_test["位置"], prompt)
        self.assertNotIn(bad_test["现象"], ledger_text(self))

    def test_full_run_failure_keeps_unfixed_issues_and_records_new_deferrals(self):
        pending = item(what="导出时没保留所选的排序", where="app/export.py:31")
        minor = item(level="轻微", what="设置页的帮助图标偏小", where="app/settings.py:8")
        runner = FakeRunner([
            Verdict(PASS, 0, "全部测试通过"),
            Verdict(PASS, 0, "全部测试通过"),
            Verdict(FAIL, 1, "断言失败：已保存的文件没有打开\nFAILED (failures=1)\n"),
        ])

        card = self.run_reviews([
            review("不通过", [pending]),
            checked("不通过", checks=[(1, "没改好")]),
            checked("不通过", [minor], [(1, "没改好")]),
        ], runner=runner)

        self.assertEqual(len(events(self.cdir, "退回")), 2)
        self.assertEqual(card.state, "需Claude介入")
        phase = self.phase()
        self.assertIn("全部测试", phase["detail"])
        stop = events(self.cdir, "转人工")[-1]
        remaining = (stop.get("清单") or {}).get("问题", [])
        deferred_file = self.cdir / "以后再修.md"
        deferred = deferred_file.read_text(encoding="utf-8") if deferred_file.is_file() else ""
        self.assertEqual(
            {
                "转人工记录里的剩余问题": [(p["现象"], p["位置"]) for p in remaining],
                "页面列出没改好的问题": pending["现象"] in phase["detail"] + phase.get("issues", ""),
                "新小问题写进以后再修": minor["现象"] in deferred,
                "新小问题写进台账": minor["现象"] in ledger_text(self),
            },
            {
                "转人工记录里的剩余问题": [(pending["现象"], pending["位置"])],
                "页面列出没改好的问题": True,
                "新小问题写进以后再修": True,
                "新小问题写进台账": True,
            },
        )
        self.assertNotIn(pending["现象"], deferred)

    def test_nonexistent_check_numbers_cannot_turn_rejection_into_pass(self):
        pending = item(level="严重", what="重新打开应用后已保存的文档丢失")
        invalid = checked("不通过", checks=[(1, "改好了"), (2, "没改好")])
        self.run_reviews([
            review("不通过", [pending]),
            invalid,
            checked("不通过", checks=[(1, "没改好")]),
            review("通过"),
        ])
        later_round = [e["result"] for e in events(self.cdir, "复查结论")]

        before = len(later_round)
        self.run_reviews([
            checked("不通过", checks=[(1, "没改好")]),
            review("不通过", [pending]),
            review("通过"),
        ], card=self.continue_editing())
        first_round = [e["result"] for e in events(self.cdir, "复查结论")[before:]]

        self.assertEqual(
            {"上轮没有第 2 条": later_round, "首轮没有上轮条目": first_round},
            {"上轮没有第 2 条": ["退回", "退回", "通过"], "首轮没有上轮条目": ["退回", "通过"]},
            "编号对不上要重写，不能把无效的没改好当作复查通过",
        )

    def test_same_wording_at_two_places_counts_twice_and_becomes_history_after_revision(self):
        first = rare(level="轻微", what="帮助图标偏小", where="app/export.py:15")
        second = dict(first, 位置="app/settings.py:21")
        self.run_reviews([review("不通过", [first, second])])
        initial = self.phase()
        self.assertEqual(initial["label"], "等你确认")

        self.run_reviews([review("通过")], card=self.continue_editing())
        revised = self.phase()
        self.assertEqual(revised["label"], "等你确认")
        self.assertEqual(
            {
                "两处各算一件": "还剩 2 件小问题" in initial["detail"],
                "历史问题误报成开工前就有": "开工前就有" in revised["detail"],
                "历史问题计入本次剩余": "小问题" in revised["detail"],
            },
            {
                "两处各算一件": True,
                "历史问题误报成开工前就有": False,
                "历史问题计入本次剩余": False,
            },
            f"继续改前：{initial['detail']}；继续改后：{revised['detail']}",
        )
