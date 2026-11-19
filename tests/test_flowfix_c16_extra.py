"""C1.6 补测：用假派活和假测试结果走一遍复查流程。"""

import json
import unittest

from collab import dispatch
from collab.agents import AgentResult
from collab.flow import text_of
from collab.judge import ENV_ERROR, PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from test_flowfix4_converge import Base
from test_flowfix4_full import FakeRunner
from test_flowfix_c15_acceptance import item, review
from test_flowfix_c16_acceptance import checked, events, ledger_text, rare


class ReviewListCompatibility(unittest.TestCase):
    def test_legacy_item_can_share_a_list_with_explicit_frequency(self):
        data, errors = dispatch.read_review_list(review("不通过", [item(), rare()]))

        self.assertEqual(errors, [])
        self.assertEqual(len(data["问题"]), 2)
        self.assertEqual(data["问题"][1]["碰上"], "极巧合")

    def test_numbered_checks_must_be_a_list_of_objects(self):
        for malformed in ({}, None, [1], [{"编号": 1}]):
            with self.subTest(malformed=malformed):
                data = {"结论": "不通过", "问题": [item()], "上轮核对": malformed}
                block = json.dumps(data, ensure_ascii=False)
                reply = f"结论：不通过\n\n复查清单：\n```json\n{block}\n```\n"
                _, errors = dispatch.read_review_list(reply)
                self.assertTrue(errors, "写了上轮核对就要给出逐条答复")


class ReviewFlow(Base):
    def run_reviews(self, replies, runner=None):
        replies = iter(replies)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            task = events(self.cdir, "派活")[-1]
            if task["dst"] == "复查员":
                answer = next(replies, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            else:
                if task["step"] == "写测试":
                    (self.worktree / "tests" / "test_a.py").write_text("# 假测试\n", encoding="utf-8")
                answer = "汇报：已交活"
            return AgentResult(ok=True, output="", attempts=1, final=answer)

        card, _ = dispatch.run_card(
            Card(self.cdir.name, state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=ask, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
            reviewer_adapter="codex", full_runner=runner,
        )
        return card

    def tasks_for(self, role):
        return [task for task in events(self.cdir, "派活") if task["dst"] == role]

    def later_text(self):
        path = self.cdir / "以后再修.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def phase(self):
        return card_phase(str(self.collab.parent), self.cdir.name)

    def test_invalid_frequency_goes_through_format_rewrite(self):
        rare_issue = rare(level="轻微", what="两步之间恰好断开时提示不对")
        invalid = dict(rare_issue, 碰上="偶尔")

        card = self.run_reviews([review("不通过", [invalid]), review("不通过", [rare_issue]), review("通过")])

        reviews = self.tasks_for("复查员")
        self.assertEqual(len(reviews), 2, "无效的碰上只需重写一次，修正后不用再复查")
        first, rewrite = reviews
        self.assertIn("只读复查", text_of(self.cdir, first))
        self.assertIn("按固定格式重写", text_of(self.cdir, rewrite))
        self.assertEqual(len(events(self.cdir, "退回")), 0)
        self.assertEqual(card.state, "待验收")
        self.assertIn(rare_issue["现象"], self.later_text())

    def test_first_round_returns_uncommon_but_counts_only_new_deferred_issue(self):
        old = item(source="原来就有的", what="原来的关闭按钮颜色偏浅")
        rare_issue = rare(level="轻微", what="两步之间恰好断开时提示不对")
        uncommon = dict(item(level="轻微", what="离线时上传按钮一直亮着"), 碰上="少见")
        legacy = item(level="轻微", what="下载成功后没有提示")

        card = self.run_reviews([review("不通过", [old, rare_issue, uncommon, legacy]), review("通过")])

        self.assertEqual(card.state, "待验收")
        _, back = self.tasks_for("开发员")
        prompt = text_of(self.cdir, back)
        self.assertIn(uncommon["现象"], prompt)
        self.assertIn(legacy["现象"], prompt, "旧清单没写碰上，按常见退回")
        for issue in (old, rare_issue):
            self.assertNotIn(issue["现象"], prompt)
            self.assertIn(issue["现象"], self.later_text())
            self.assertIn(issue["现象"], ledger_text(self))
        detail = self.phase()["detail"]
        for words in ("还剩 1 件小问题", "以后再修", "台账"):
            self.assertIn(words, detail)

    def test_only_preexisting_issue_keeps_original_acceptance_explanation(self):
        old = item(source="原来就有的", what="原来的关闭按钮颜色偏浅")

        card = self.run_reviews([review("不通过", [old])])

        self.assertEqual(card.state, "待验收")
        self.assertIn("开工前就有的问题，已记进台账", self.phase()["detail"])

    def test_pass_with_a_preexisting_issue_does_not_excuse_a_missing_answer(self):
        pending = item(what="保存后草稿丢失")
        old = item(source="原来就有的", what="原来的关闭按钮颜色偏浅")
        self.run_reviews([review("不通过", [pending]), review("通过", [old]), review("通过")])

        self.assertEqual([e["result"] for e in events(self.cdir, "复查结论")], ["退回", "退回", "通过"])
        _, _, back = self.tasks_for("开发员")
        prompt = text_of(self.cdir, back)
        self.assertIn(pending["现象"], prompt)
        self.assertIn(pending["位置"], prompt)
        self.assertNotIn(old["现象"], prompt)
        self.assertEqual(self.later_text().count(old["现象"]), 1)
        self.assertEqual(ledger_text(self).count(old["现象"]), 1)

    def test_old_issue_inside_a_returned_review_is_not_entered_in_the_ledger(self):
        initial = item(what="保存按钮点了没有反应")
        covered = item(source="原来就有的", what="历史列表的关闭按钮颜色偏浅",
                       where="app/history.py:17")
        unrelated = item(source="原来就有的", what="帮助页的标题挤在一起", where="app/help.py:8")
        whole = item(level="严重", what=f"切换设备时已保存的草稿会丢失\n{covered['现象']}",
                     where="（原意见没写位置）")
        self.run_reviews([
            review("不通过", [initial]),
            checked("不通过", [covered, whole, unrelated], [(1, "改好了")]),
            review("通过"),
        ])

        self.assertIn(covered["现象"], text_of(self.cdir, self.tasks_for("开发员")[-1]))
        for recorded in (self.later_text(), ledger_text(self)):
            self.assertNotIn(covered["现象"], recorded)
            self.assertEqual(recorded.count(unrelated["现象"]), 1)

    def test_number_one_after_renumbering_is_the_issue_shown_on_manual_stop(self):
        fixed = item(what="保存后草稿丢失", where="app/save.py:12")
        pending = item(level="严重", what="切换设备时已保存的文档会丢失", where="app/sync.py:45")
        second_minor = item(level="轻微", what="导出按钮文字偏左", where="app/export.py:7")
        third_minor = item(level="轻微", what="设置页的帮助图标偏小", where="app/settings.py:8")
        new_severe = item(level="严重", what="改过密码后再也登不进去", where="app/login.py:51")

        card = self.run_reviews([
            review("不通过", [fixed, pending]),
            checked("不通过", [second_minor], [(1, "改好了")]),
            checked("不通过", [third_minor, new_severe], [(1, "没改好")]),
        ])

        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(events(self.cdir, "退回")), 2)
        third = text_of(self.cdir, self.tasks_for("复查员")[2])
        self.assertIn(f"上轮退回的问题（按编号）：\n1. {pending['现象']}\n\n", third)
        stop, = events(self.cdir, "转人工")
        problems = (stop.get("清单") or {}).get("问题", [])
        self.assertCountEqual(
            [(p["现象"], p["位置"], p["级别"]) for p in problems],
            [(p["现象"], p["位置"], "严重") for p in (pending, new_severe)],
        )
        issues = self.phase()["issues"]
        for problem in (pending, new_severe):
            self.assertIn(f"{problem['现象']}（严重）", issues)
        self.assertNotIn(third_minor["现象"], issues)

    def test_missing_full_result_blocks_acceptance_after_two_returns(self):
        pending = item(what="保存后草稿丢失")
        runner = FakeRunner([Verdict(PASS, 0, "ok"), Verdict(PASS, 0, "ok"), None])
        card = self.run_reviews([
            review("不通过", [pending]),
            checked("不通过", [item(what="导出按钮文字偏左")], [(1, "没改好")]),
            checked("不通过", [item(what="设置页的帮助图标偏小")], [(1, "没改好")]),
        ], runner)

        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(runner.calls, 3)
        self.assertEqual(len(events(self.cdir, "退回")), 2)
        self.assertNotIn(pending["现象"], self.later_text())
        self.assertNotIn(pending["现象"], ledger_text(self))
        results = [e for e in events(self.cdir, "全量") if e.get("phase") == "结果"]
        self.assertEqual(len(results), 1, "退满那轮要等全部测试给出结果")
        result, = results
        self.assertEqual(result["result"], ENV_ERROR)
        stop, = events(self.cdir, "转人工")
        self.assertIn("全部测试", stop["summary"])
        self.assertGreater(stop["seq"], result["seq"])

    def test_first_test_error_after_two_returns_has_one_correction_opportunity(self):
        pending = item(what="保存后草稿丢失")
        wrong = item(kind="测试写错", level="轻微", what="导出测试把列名写反了", where="tests/test_export.py:24")
        card = self.run_reviews([
            review("不通过", [pending]),
            checked("不通过", [item(what="导出按钮文字偏左")], [(1, "没改好")]),
            checked("不通过", [wrong], [(1, "改好了")]),
            checked("不通过", [], [(1, "没改好")]),
            review("不通过", [wrong]),  # 旧流程要求重写时也能继续，以便检查多派了几次。
        ])

        self.assertEqual(card.state, "需Claude介入")
        reviews = self.tasks_for("复查员")
        self.assertEqual(len(reviews), 4, "给一次改测试机会，之后只多一轮复查")
        _, _, third, fourth = reviews
        corrections = [task for task in self.tasks_for("测试员") if task["step"] == "改测试"]
        self.assertEqual(len(corrections), 1, "测试仍旧写错时转人工")
        correction, = corrections
        self.assertLess(third["seq"], correction["seq"])
        self.assertLess(correction["seq"], fourth["seq"])
        prompt = text_of(self.cdir, correction)
        self.assertIn(wrong["现象"], prompt)
        self.assertIn(wrong["位置"], prompt)
        self.assertNotIn(pending["现象"], prompt)
        stop, = events(self.cdir, "转人工")
        self.assertGreater(stop["seq"], fourth["seq"])
        self.assertNotIn(wrong["现象"], self.later_text())


if __name__ == "__main__":
    unittest.main()
