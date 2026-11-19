"""跑卡流程调整第二批 · 卡 C1.6（复查收口）验收测试，Claude 先写（施工前应为红）。

口径见 docs/跑卡流程调整/方案-C1.6.md（用户 11-18 批准；Q1、Q2 都选 A）：
1 复查清单每条加“碰上”：常见 / 少见 / 极巧合；写了就只能是这三个，没写按“常见”算
2 第 1 轮：这次改的照旧都退回；标“极巧合”且不严重、不是测试写错的，记以后再修
3 第 2 轮起：退回时程序给每个问题编号；第 2 轮交代列出编号，复查员在清单里加“上轮核对”按编号答“改好了 / 没改好”。
  只退回 答“没改好”的（漏答的按没改好算）、新冒出的严重的、测试写错的；其余新冒出的记以后再修和台账，不退回
  （按编号核对是用户 11-18 采纳另一窗口意见：不靠比位置、比文字，复查员换个说法也漏不掉）
4 退回用完：剩下的都不严重、也不是测试写错 → 先等这一轮全部测试跑完（和复查通过时一样），全过才自动进待验收，
  记以后再修，验收说明写“还剩 N 件小问题”；全部测试改坏老功能 / 没跑成 → 不自动放行，停下交把关的人；还有严重的 → 停下
  （第 4 条的“先等全量”是用户 11-18 依 pstack 评估补的：不能新开一条不跑完全量就进待验收的路）
5 规则里复查员那一行写上“碰上”
"""
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import FAIL, PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from test_flowfix_c15_acceptance import Flow, item, review
from test_flowfix_c1_extra import unittest_output

ROOT = Path(__file__).resolve().parents[1]


def checked(verdict, items=(), checks=(), prose="复查过程：看了改动。"):
    """带“上轮核对”的复查回复：checks 是 [(编号, "改好了"/"没改好"), ...]。"""
    import json
    data = {"结论": verdict, "上轮核对": [{"编号": n, "结果": r} for n, r in checks], "问题": list(items)}
    block = json.dumps(data, ensure_ascii=False, indent=1)
    return f"结论：{verdict}\n\n{prose}\n\n复查清单：\n```json\n{block}\n```\n"


def rare(**kw):
    p = item(**kw)
    p["碰上"] = "极巧合"
    return p


class ReadList(unittest.TestCase):
    def test_hit_field_values(self):
        from collab.dispatch import read_review_list
        for value in ("常见", "少见", "极巧合"):
            p = item()
            p["碰上"] = value
            _, errors = read_review_list(review("不通过", [p]))
            self.assertEqual(errors, [], value)
        bad = item()
        bad["碰上"] = "偶尔"
        _, errors = read_review_list(review("不通过", [bad]))
        self.assertTrue(errors, "“碰上”只能是常见 / 少见 / 极巧合")
        _, errors = read_review_list(review("不通过", [item()]))
        self.assertEqual(errors, [], "没写“碰上”不算不合格，按常见算")

    def test_check_back_values(self):
        from collab.dispatch import read_review_list
        _, errors = read_review_list(checked("不通过", [item()], [(1, "改好了"), (2, "没改好")]))
        self.assertEqual(errors, [])
        _, errors = read_review_list(checked("不通过", [item()], [(1, "差不多")]))
        self.assertTrue(errors, "上轮核对只能答改好了 / 没改好")
        _, errors = read_review_list(checked("不通过", [item()], [("一", "改好了")]))
        self.assertTrue(errors, "编号要是数字")
        _, errors = read_review_list(checked("不通过", [], [(1, "没改好")]))
        self.assertEqual(errors, [], "只有上轮没改好、没有新问题：合格")
        _, errors = read_review_list(checked("不通过", [], [(1, "改好了")]))
        self.assertTrue(errors, "不通过却既没有新问题、也没有没改好的：不合格")


def events(cdir, kind):
    return [e for e in read_flow(cdir) if e.get("kind") == kind]


def ledger_text(flow):
    path = flow.collab.parent / "docs" / "开发进度跟踪.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class Prompt(Flow):
    def test_review_prompt_asks_for_hit_and_regressions_are_severe(self):
        _, calls = self.run_flow([review("通过")])
        p = self.of(calls, "review")[0][1]
        for word in ("碰上", "常见", "少见", "极巧合"):
            self.assertIn(word, p)
        self.assertRegex(p, r"改坏[^。]*严重", "改坏原来能用的功能一律标严重（方案：改坏老功能必退）")

    def test_later_round_lists_last_returned_items(self):
        a = item(what="保存后草稿丢失", where="app/save.py:12")
        b = item(what="列表排序反了", where="app/list.py:40")
        _, calls = self.run_flow([review("不通过", [a, b]), review("通过")])
        p = self.of(calls, "review")[1][1]
        self.assertIn("上轮退回的问题（按编号）：\n1. 保存后草稿丢失\n2. 列表排序反了\n", p,
                      "一字不差的标题行 + 每行一条“N. 现象”（老测试的转换工具靠它认）")
        for word in ("上轮核对", "改好了", "没改好", "碰上"):
            self.assertIn(word, p, "第 2 轮起照样要填碰上，并按编号逐条答")

    def test_third_round_lists_only_last_actual_returns(self):
        a = item(what="保存后草稿丢失", where="app/save.py:12")
        b = item(what="列表排序反了", where="app/list.py:40")
        sev = item(level="严重", what="重启后数据全丢", where="collab/store.py:40")
        r2 = checked("不通过", [sev], [(1, "改好了"), (2, "没改好")])
        _, calls = self.run_flow([review("不通过", [a, b]), r2, review("通过")])
        p = self.of(calls, "review")[2][1]
        block = p.split("上轮退回的问题（按编号）：\n", 1)[1].split("\n\n", 1)[0]
        lines = block.splitlines()
        self.assertEqual([ln.split(". ", 1)[0] for ln in lines], ["1", "2"], "重新从 1 编号")
        self.assertEqual(sorted(ln.split(". ", 1)[1] for ln in lines), ["列表排序反了", "重启后数据全丢"],
                         "第 3 轮只列第 2 轮实际退回的：没改好的旧问题 + 新冒出的严重问题（先后不限）")


class FirstRound(Flow):
    def test_rare_not_severe_is_deferred(self):
        card, calls = self.run_flow([review("不通过", [rare(what="恰好在两步之间被打断时提示错")])])
        self.assertEqual(card.state, "待验收", "第 1 轮只有极巧合的小问题：不退回")
        self.assertEqual(len(self.of(calls, "dev")), 1)
        self.assertIn("恰好在两步之间被打断时提示错", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))
        self.assertIn("恰好在两步之间被打断时提示错", ledger_text(self), "记以后再修也要写台账")

    def test_rare_but_severe_still_returned(self):
        card, calls = self.run_flow([review("不通过", [rare(level="严重", what="打断后数据全丢")]), review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 2, "严重的哪怕极巧合也退回")

    def test_rare_but_test_wrong_still_returned(self):
        tw = rare(kind="测试写错", what="测试把按钮颜色写反了", where="tests/test_a.py:5")
        _, calls = self.run_flow([review("不通过", [tw]), review("通过")])
        self.assertTrue([c for c in calls[2:] if c[0] == "test" and "测试把按钮颜色写反了" in c[1]],
                        "测试写错的哪怕极巧合也派写测试的改")

    def test_common_this_time_still_returned(self):
        card, calls = self.run_flow([review("不通过", [item(level="轻微", what="提示字太小")]), review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 2, "第 1 轮这次改的照旧都退回（C1 口径不变）")


class LaterRounds(Flow):
    R1 = item(what="保存后草稿丢失", where="app/save.py:12")
    R2 = item(what="列表排序反了", where="app/list.py:40")

    def test_fixed_and_new_medium_is_deferred(self):
        new = item(what="导出按钮文字偏左", where="app/export.py:7")
        r2 = checked("不通过", [new], [(1, "改好了")])
        card, calls = self.run_flow([review("不通过", [self.R1]), r2])
        self.assertEqual(card.state, "待验收", "上轮的改好了；新冒出的中等问题记以后再修，不退回")
        self.assertEqual(len(self.of(calls, "dev")), 2)
        self.assertIn("导出按钮文字偏左", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))
        self.assertIn("导出按钮文字偏左", ledger_text(self), "也写台账")

    def test_out_of_order_and_partial_answers(self):
        # 两条上轮问题：答案乱序给，第 2 条改好了，第 1 条没答——第 1 条按没改好退回，第 2 条不退回
        new = item(what="导出按钮文字偏左", where="app/export.py:7")
        r2 = checked("不通过", [new], [(2, "改好了")])
        card, calls = self.run_flow([review("不通过", [self.R1, self.R2]), r2, review("通过")])
        back = self.of(calls, "dev")[2][1]
        self.assertIn("保存后草稿丢失", back, "漏答的按没改好退回")
        self.assertIn("app/save.py:12", back, "退回时带上原问题的位置")
        self.assertNotIn("列表排序反了", back, "答了改好了的不再退回")

    def test_answered_not_fixed_is_returned_with_original(self):
        r2 = checked("不通过", [], [(2, "没改好"), (1, "改好了")], prose="排序还是反的。")
        card, calls = self.run_flow([review("不通过", [self.R1, self.R2]), r2, review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 3, "按编号答没改好：退回")
        back = self.of(calls, "dev")[2][1]
        self.assertIn("列表排序反了", back)
        self.assertIn("app/list.py:40", back)
        self.assertNotIn("保存后草稿丢失", back)

    def test_unanswered_counts_as_not_fixed_and_new_medium_not_returned(self):
        r2 = review("不通过", [item(what="导出按钮文字偏左", where="app/export.py:7")])
        card, calls = self.run_flow([review("不通过", [self.R1]), r2, review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 3, "没按编号回答：上轮的按没改好算，退回")
        back = self.of(calls, "dev")[2][1]
        self.assertIn("保存后草稿丢失", back)
        self.assertNotIn("导出按钮文字偏左", back, "新冒出的中等问题不跟着退回，记以后再修")
        self.assertIn("导出按钮文字偏左", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))

    def test_new_severe_in_round2_is_returned(self):
        sev = item(level="严重", what="重启后数据全丢", where="collab/store.py:40")
        card, calls = self.run_flow([review("不通过", [self.R1]), checked("不通过", [sev], [(1, "改好了")]),
                                     review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 3)
        self.assertIn("重启后数据全丢", self.of(calls, "dev")[2][1])

    def test_new_test_wrong_in_round2_is_returned(self):
        tw = item(kind="测试写错", what="测试把按钮颜色写反了", where="tests/test_a.py:5")
        _, calls = self.run_flow([review("不通过", [self.R1]), checked("不通过", [tw], [(1, "改好了")]),
                                  review("通过")])
        second_review = [i for i, c in enumerate(calls) if c[0] == "review"][1]
        fixes = [c for c in calls[second_review + 1:] if c[0] == "test" and "测试把按钮颜色写反了" in c[1]]
        self.assertTrue(fixes, "第 2 轮复查之后要派写测试的改那条写错的测试")


class AfterReturnsUsed(Flow):
    R1 = item(what="保存后草稿丢失", where="app/save.py:12")

    def test_only_minor_left_goes_to_acceptance(self):
        card, calls = self.run_flow([review("不通过", [self.R1])] * 3)
        self.assertEqual(card.state, "待验收", "退回用完、剩下的不严重：自动进待验收")
        self.assertEqual(len(self.of(calls, "dev")), 3, "首轮加两次退回，不再派第四次")
        self.assertFalse(events(self.cdir, "转人工"))
        self.assertIn("保存后草稿丢失", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))
        self.assertIn("保存后草稿丢失", ledger_text(self), "也写台账")
        last = events(self.cdir, "复查结论")[-1]
        self.assertEqual(last.get("result"), "通过")
        self.assertIn("还剩 1 件小问题", last.get("summary", ""))
        detail = card_phase(str(self.collab.parent), "CARD-001")["detail"]
        self.assertIn("还剩 1 件小问题", detail)
        self.assertIn("以后再修", detail)

    def test_stop_lists_unfixed_from_check_back(self):
        sev = item(level="严重", what="重启后数据全丢", where="collab/store.py:40")
        card, _ = self.run_flow([review("不通过", [sev]), checked("不通过", [], [(1, "没改好")]),
                                 checked("不通过", [], [(1, "没改好")])])
        self.assertEqual(card.state, "需Claude介入")
        stop = events(self.cdir, "转人工")[-1]
        self.assertEqual([p.get("现象") for p in (stop.get("清单") or {}).get("问题", [])], ["重启后数据全丢"],
                         "转人工记录里的清单是这一轮实际要退回的全部（含按编号答没改好的上轮问题）")
        phase = card_phase(str(self.collab.parent), "CARD-001")
        self.assertIn("重启后数据全丢（严重）", phase.get("issues", ""))
        self.assertIn("建议", stop.get("say", ""), "停下时给一句建议（推荐按钮）")

    def test_severe_left_still_stops(self):
        sev = item(level="严重", what="重启后数据全丢", where="collab/store.py:40")
        card, _ = self.run_flow([review("不通过", [sev])] * 3)
        self.assertEqual(card.state, "需Claude介入", "还有严重的：停下")


class AutoPassWaitsForFullRun(Flow):
    """自动进待验收这条路也要先等这一轮的全部测试跑完（用户 11-18 补）。"""
    R1 = item(what="保存后草稿丢失", where="app/save.py:12")

    def run_with_full(self, reviews, runner):
        reviews = list(reviews)
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt or "只改测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="汇报：写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex", full_runner=runner)
        return card

    def round_runner(self, last_verdict):
        """前两轮复查不通过，全量会被叫停；第 3 轮（退满那轮）才给结果，其余轮如果被用来放行就会露馅。"""
        test = self

        class Runner:
            def __init__(self):
                self.calls = 0

            def run(self, worktree, stop):
                self.calls += 1
                if self.calls < 3:
                    stop.wait(5)   # 前两轮：等着被叫停（要改代码）
                    return Verdict(FAIL, 1, "前两轮的结果不能拿来放行")
                return last_verdict

            def base(self, worktree, files, stop):
                return {name: Verdict(PASS, 0, "ok") for name in files}
        return Runner()

    def test_auto_pass_waits_for_this_rounds_full_run(self):
        runner = self.round_runner(Verdict(PASS, 0, "ok"))
        card = self.run_with_full([review("不通过", [self.R1])] * 3, runner)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(runner.calls, 3, "退满那一轮的全部测试要真跑，不能叫停")
        flow = read_flow(self.cdir)
        third = [e["seq"] for e in flow if e.get("kind") == "回复" and e.get("src") == "复查员"][2]
        passed = [e["seq"] for e in flow if e.get("kind") == "全量" and e.get("phase") == "结果"
                  and e.get("result") == PASS]
        done = [e["seq"] for e in flow if e.get("kind") == "结束" and e.get("state") == "待验收"]
        self.assertTrue(passed and passed[-1] > third, "放行用的是第 3 轮复查之后这一轮的全量结果")
        self.assertTrue(done and done[-1] > passed[-1], "全量结果出来之后才进待验收")

    def test_broken_old_feature_blocks_auto_pass(self):
        (self.worktree / "tests" / "test_old.py").write_text("# 老测试\n", encoding="utf-8")
        output, _ = unittest_output()
        card = self.run_with_full([review("不通过", [self.R1])] * 3, self.round_runner(Verdict(FAIL, 1, output)))
        self.assertEqual(card.state, "需Claude介入", "全部测试显示改坏了老功能：不自动放行，停下交把关的人")
        self.assertIn("全部测试", events(self.cdir, "转人工")[-1].get("summary", ""), "停下原因写清是全部测试的问题")

    def test_timeout_blocks_auto_pass(self):
        from collab.judge import TIMEOUT
        card = self.run_with_full([review("不通过", [self.R1])] * 3,
                                  self.round_runner(Verdict(TIMEOUT, None, "")))
        self.assertEqual(card.state, "需Claude介入", "全部测试超时：不自动放行")
        self.assertIn("全部测试", events(self.cdir, "转人工")[-1].get("summary", ""))


class Rules(unittest.TestCase):
    def test_rules_mention_hit_and_numbered_check(self):
        text = (ROOT / "collab" / "规则.md").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "**复查员**" in ln)
        for word in ("碰上", "常见", "少见", "极巧合", "编号", "改好了", "没改好"):
            self.assertIn(word, line)


if __name__ == "__main__":
    unittest.main()
