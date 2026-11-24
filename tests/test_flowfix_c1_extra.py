"""C1 补充测试：只补边界，不改验收测试、老测试和产品代码。

使用真实派活流程、流转文件、台账和右栏数据；外部 AI 与测试进程用可控替身，
不会启动浏览器、真实 AI 或项目全量测试。
"""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, _triage, run_card
from collab.flow import read_flow, record, text_of
from collab.judge import FAIL, PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from desktop.view import card_view
from review_fmt import listed_ask  # C1.5：老测试的随手复查意见补上固定清单（tests/review_fmt.py）


ROOT = Path(__file__).resolve().parents[1]
CURRENT = "结论：不通过\n1. 【轻微】【这次改的】断网后提示一直不消失。"
MODERATE = "结论：不通过\n1. 【中等】【这次改的】断网后一直无法重试。"
OLD = "结论：不通过\n1. 【中等】【原来就有的】切换对话时会卡住。"
PASS_REVIEW = "结论：通过"
BASE_TEST = "import unittest\n\nclass Guard(unittest.TestCase):\n    def test_base(self):\n        self.assertTrue(True)\n"


def result(text="汇报：完成", ok=True):
    return AgentResult(ok=ok, output="" if ok else text, attempts=1, final=text)


class FlowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.collab = self.project / ".collab"
        self.cdir = self.collab / "cards" / "CARD-003"
        self.cdir.mkdir(parents=True)
        self.worktree = self.project / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 测试卡\n## 要做什么\n调整复查规矩\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("已有交接资料\n", encoding="utf-8")
        self.calls = []

    def flow(self, reviews=(PASS_REVIEW,), *, tester=None, developer=None,
             judge=None, role_models=None, card=None, **options):
        reviews = iter(reviews)
        test_count = dev_count = 0

        def ask(adapter, prompt, cwd, **kw):
            nonlocal test_count, dev_count
            if "只读复查" in prompt:
                who = "review"
            elif adapter == "codex":
                who = "test"
            else:
                who = "dev"
            self.calls.append({"who": who, "prompt": prompt, "models": copy.deepcopy(kw.get("models"))})
            if who == "review":
                answer = next(reviews, PASS_REVIEW)
                return result(answer) if isinstance(answer, str) else answer
            if who == "test":
                test_count += 1
                if test_count == 1:
                    (self.worktree / "tests" / "test_base.py").write_text(BASE_TEST, encoding="utf-8")
                elif tester:
                    answer = tester(prompt)
                    if answer is not None:
                        return answer
                return result()
            dev_count += 1
            if developer:
                developer(dev_count, prompt)
            return result()

        return run_card(
            card or Card("CARD-003", state="待派发"), self.collab, self.worktree,
            "true", "qwen", "codex", ask_fn=listed_ask(ask),
            judge_fn=judge or (lambda *a, **k: Verdict(PASS, 0, "ok")),
            reviewer_adapter="codex", role_models=role_models, **options,
        )

    def calls_for(self, who):
        return [c for c in self.calls if c["who"] == who]

    def events(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind]

    def station(self):
        view = card_view(str(self.project), "CARD-003")
        return next(s for s in view["stations"] if s["key"] == "rev")

    def init_git(self):
        # 只在临时小项目里建基线，用来真实验证恢复行为，不动工作仓库。
        (self.worktree / "README.md").write_text("临时测试项目\n", encoding="utf-8")
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=测试", "-c", "user.email=test@example.invalid", "commit", "-qm", "测试基线"]):
            subprocess.run(["git", *args], cwd=self.worktree, check=True, capture_output=True)

    def require_guard(self):
        guards = self.calls_for("test")[1:]
        self.assertTrue(guards, "普通问题退回时，必须先派写测试的补测")
        return guards


class TriageEdges(unittest.TestCase):
    def test_only_leading_old_tag_can_defer_and_test_problems_always_return(self):
        cases = [
            ("【轻微】没有标来源。", True),
            ("【轻微】【这次改的】提示太小。", True),
            ("【严重】【原来就有的】结果丢了。", True),
            ("【测试的问题】【轻微】【原来就有的】断言与需求不符。", True),
            ("【原来就有的】【轻微】【测试的问题】测试漏掉一种情况。", True),
            ("【轻微】正文里引用【原来就有的】不算分类。", True),
            ("【中等】正文里引用【原来就有的】不算分类。", True),
            ("【原来就有的】没写严重程度的旧问题。", False),
            ("【轻微】【原来就有的】正文提到【严重】这个标签。", False),
            ("【中等】【原来就有的】正文提到【测试的问题】这个标签。", False),
        ]
        for item, goes_back in cases:
            with self.subTest(item=item):
                self.assertEqual(_triage([item]), ([item], []) if goes_back else ([], [item]))

    def test_empty_review_items_do_not_invent_issues(self):
        self.assertEqual(_triage([]), ([], []))


class ReviewConclusions(FlowCase):
    def test_direct_pass_records_program_conclusion_linked_to_reply(self):
        card, _ = self.flow()
        self.assertEqual(card.state, "待验收")
        replies = [e for e in self.events("回复") if e["src"] == "复查员"]
        conclusions = self.events("复查结论")
        self.assertEqual(len(conclusions), 1, "直接通过也要落一条复查结论")
        conclusion = conclusions[0]
        self.assertEqual((conclusion["src"], conclusion["result"], conclusion["reply"], conclusion["later"]),
                         ("程序", "通过", replies[0]["seq"], 0))
        self.assertEqual(conclusion["summary"], "复查通过")
        self.assertGreater(conclusion["seq"], replies[0]["seq"])

    def test_mixed_then_old_only_records_each_round_with_its_own_counts(self):
        mixed = CURRENT + "\n2. 【中等】【原来就有的】旧页面偶尔闪一下。"
        card, _ = self.flow([mixed, OLD])
        self.assertEqual(card.state, "待验收")
        replies = [e for e in self.events("回复") if e["src"] == "复查员"]
        conclusions = self.events("复查结论")
        self.assertEqual(len(conclusions), 2, "每轮有效复查各记一次最终结论")
        self.assertEqual([(e["result"], e["reply"], e["later"]) for e in conclusions],
                         [("退回", replies[0]["seq"], 1), ("通过", replies[1]["seq"], 1)])
        self.assertEqual([e["summary"] for e in conclusions],
                         ["复查退回 1 件", "复查通过（另有 1 件开工前就有的问题，已记进台账）"])
        self.assertLess(conclusions[0]["seq"], replies[1]["seq"])
        fix = self.calls_for("dev")[1]["prompt"]
        self.assertIn("断网后提示", fix)
        self.assertNotIn("旧页面偶尔闪", fix, "延期的旧问题不混进本次修复交代")

    def test_unparsed_serious_issue_cannot_be_hidden_by_old_only_closure(self):
        review = OLD + "\n【严重】【这次改的】保存后结果全丢。"
        self.flow([review, PASS_REVIEW])
        self.assertEqual(len(self.calls_for("dev")), 2)
        self.assertIn("保存后结果全丢", self.calls_for("dev")[1]["prompt"])
        self.assertEqual(self.events("以后再修"), [], "没解析完整时全部退回，不能自动延期")

    def test_rejection_without_numbered_items_still_returns_to_developer(self):
        self.flow(["结论：不通过\n按钮点了没有反应。", PASS_REVIEW])
        self.assertEqual(len(self.calls_for("dev")), 2)
        self.assertIn("按钮点了没有反应", self.calls_for("dev")[1]["prompt"])

    def test_unclear_reply_does_not_create_a_passing_conclusion(self):
        card, _ = self.flow(["还没有检查完。"])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(self.events("复查结论"), [])

    def test_failed_reviewer_cannot_pass_even_if_reply_says_pass(self):
        card, _ = self.flow([result(PASS_REVIEW, ok=False)])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(self.events("复查结论"), [])

    def test_review_return_limit_stays_two(self):
        severe = MODERATE.replace("【中等】", "【严重】")  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的
        card, _ = self.flow([severe, severe, severe])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.calls_for("review")), 3)
        self.assertEqual(len(self.calls_for("dev")), 3, "首轮开发加两次退回，不能再派第四次")
        self.assertEqual(len(self.events("退回")), 2)


class LaterLedger(FlowCase):
    def test_first_round_old_only_closes_without_extra_development_or_guard(self):
        card, _ = self.flow([OLD])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.calls_for("review")), 1, "首轮只有旧问题就直接放行")
        self.assertEqual(len(self.calls_for("dev")), 1)
        self.assertEqual(len(self.calls_for("test")), 1, "延期旧问题不用再派补测")

    def test_old_issues_append_plain_words_to_project_ledger(self):
        docs = self.project / "docs"
        docs.mkdir()
        ledger = docs / "开发进度跟踪.md"
        ledger.write_text("# 原来的台账\n- 这条旧记录必须保留\n", encoding="utf-8")
        review = (OLD + "位置：[old.py:9](/tmp/old.py:9)\n"
                  "2. 【轻微】【原来就有的】旧提示有错字。位置：[old.py:12](/tmp/old.py:12)")
        card, _ = self.flow([review])
        self.assertEqual(card.state, "待验收")
        saved = ledger.read_text(encoding="utf-8")
        self.assertTrue(saved.startswith("# 原来的台账\n- 这条旧记录必须保留\n"))
        self.assertIn("CARD-003：复查发现开工前就有的问题 2 件，这次不改：切换对话时会卡住；旧提示有错字", saved)
        self.assertNotIn("old.py", saved, "台账只放整理过的大白话")
        self.assertFalse((self.worktree / "docs" / "开发进度跟踪.md").exists(), "台账应写进卡片所属项目")

    def test_ledger_write_error_is_recorded_and_does_not_stop_card(self):
        (self.project / "docs").write_text("故意占住目录位置，让台账写入失败", encoding="utf-8")
        card, _ = self.flow([OLD])
        self.assertEqual(card.state, "待验收", "写台账失败不能卡住跑卡")
        failures = [e for e in read_flow(self.cdir)
                    if "台账" in e.get("summary", "") and
                    any(w in e.get("summary", "") for w in ("失败", "没", "不能"))]
        self.assertTrue(failures, "台账写不成也要留下一条流转说明")
        self.assertTrue(self.events("以后再修"))
        self.assertIn("切换对话", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))

    def test_acceptance_detail_keeps_later_list_without_popup_wording(self):
        Card("CARD-003", state="待验收").save(self.cdir / "状态.json")
        record(self.cdir, "以后再修", src="程序", items=["切换对话时会卡住", "旧提示有错字"])
        record(self.cdir, "结束", src="程序", state="待验收", summary="做完了")
        phase = card_phase(str(self.project), "CARD-003")
        self.assertEqual(phase["later"], ["切换对话时会卡住", "旧提示有错字"])
        self.assertEqual(phase["detail"], "另有 2 件开工前就有的问题，已记进台账，这次不改")

    def test_accept_popup_entry_and_all_references_are_removed(self):
        # 施工单要求彻底删除旧入口，因此这里专门守住“不能新旧并存”。
        source = (ROOT / "desktop" / "static" / "panel.js").read_text(encoding="utf-8")
        self.assertEqual(source.count("acceptConfirm"), 0, "旧弹窗的定义、调用和导出都应删掉")


class ReviewStation(FlowCase):
    def setUp(self):
        super().setUp()
        Card("CARD-003", state="待验收").save(self.cdir / "状态.json")

    def reply(self, text=OLD):
        return record(self.cdir, "回复", src="复查员", ok=True, summary=text.splitlines()[0], text=text)

    def conclusion(self, reply, result_text="通过"):
        return record(self.cdir, "复查结论", src="程序", reply=reply, result=result_text, later=1,
                      summary="复查通过（另有 1 件开工前就有的问题，已记进台账）" if result_text == "通过" else "复查退回 1 件")

    def test_matching_conclusion_overrides_raw_rejection(self):
        reply = self.reply()
        conclusion = self.conclusion(reply["seq"])
        station = self.station()
        self.assertEqual(station["state"], "done")
        self.assertEqual(station["detail"], conclusion["summary"])

    def test_late_conclusion_for_previous_reply_cannot_override_latest_rejection(self):
        first = self.reply()
        self.conclusion(first["seq"])
        self.reply(CURRENT)
        self.conclusion(first["seq"])
        self.assertEqual(self.station()["state"], "fail", "旧回复的迟到结论不能给新一轮盖绿勾")

    def test_conclusion_before_its_reply_cannot_override_it(self):
        self.conclusion(2)
        reply = self.reply()
        self.assertEqual(reply["seq"], 2, "特意构造序号相同、时序不对的记录")
        self.assertEqual(self.station()["state"], "fail")

    def test_matching_rejection_keeps_failure_display(self):
        reply = self.reply(CURRENT)
        self.conclusion(reply["seq"], "退回")
        self.assertEqual(self.station()["state"], "fail")

    def test_legacy_card_without_conclusion_falls_back_to_reply(self):
        self.reply(CURRENT)
        self.assertEqual(self.station()["state"], "fail")
        self.reply(PASS_REVIEW)
        self.assertEqual(self.station()["state"], "done")


class GuardBeforeFix(FlowCase):
    def test_each_returned_issue_is_handed_to_tester_before_developer(self):
        review = CURRENT + "\n2. 【中等】【这次改的】重试按钮一直不能点。"
        self.flow([review, PASS_REVIEW])
        guard = self.require_guard()[0]
        prompt = guard["prompt"]
        for word in ("编写自动化测试", "断网后提示", "重试按钮", "测试文件", "产品代码"):
            self.assertIn(word, prompt)
        self.assertRegex(prompt, r"各|每条|每一条", "要针对退回的每一条分别补测")
        self.assertRegex(prompt, r"失败|不过|不通过", "补测应能抓住当前问题")
        self.assertEqual([c["who"] for c in self.calls], ["test", "dev", "review", "test", "dev", "review"])
        fix = self.calls_for("dev")[1]["prompt"]
        self.assertIn("同类", fix)
        self.assertRegex(fix, r"补.{0,8}测试|测试.{0,8}守住")
        self.assertIn("本卡测试", fix)

    def test_supplement_cannot_change_create_delete_or_restore_product_files(self):
        for name in ("changed.py", "untouched.py", "deleted.py"):
            (self.worktree / name).write_text("基线\n", encoding="utf-8")
        self.init_git()
        observations = []

        def developer(number, prompt):
            if number == 1:
                (self.worktree / "changed.py").write_text("开发员已有成果\n", encoding="utf-8")
                (self.worktree / "deleted.py").unlink()
            else:
                observations.append({name: (self.worktree / name).read_text(encoding="utf-8")
                                     if (self.worktree / name).exists() else None
                                     for name in ("changed.py", "untouched.py", "deleted.py", "new_product.py")})

        def tester(prompt):
            (self.worktree / "changed.py").write_text("测试员越界覆盖\n", encoding="utf-8")
            (self.worktree / "untouched.py").unlink()
            (self.worktree / "deleted.py").write_text("测试员越界复活\n", encoding="utf-8")
            (self.worktree / "new_product.py").write_text("测试员新建的产品代码\n", encoding="utf-8")
            (self.worktree / "tests" / "test_guard.py").write_text("# 补测成果\n", encoding="utf-8")

        self.flow([CURRENT, PASS_REVIEW], tester=tester, developer=developer)
        self.require_guard()
        self.assertEqual(observations, [{"changed.py": "开发员已有成果\n", "untouched.py": "基线\n",
                                         "deleted.py": None, "new_product.py": None}])
        self.assertEqual((self.worktree / "tests" / "test_guard.py").read_text(encoding="utf-8"), "# 补测成果\n")

    def test_successful_guard_is_protected_from_developer_changes(self):
        self.init_git()

        def tester(prompt):
            (self.worktree / "tests" / "test_guard.py").write_text("# 必须保留的补测\n", encoding="utf-8")

        def developer(number, prompt):
            if number == 2:
                (self.worktree / "tests" / "test_guard.py").write_text("# 越界改测试\n", encoding="utf-8")

        self.flow([CURRENT, PASS_REVIEW], tester=tester, developer=developer)
        self.require_guard()
        self.assertEqual((self.worktree / "tests" / "test_guard.py").read_text(encoding="utf-8"), "# 必须保留的补测\n")
        listed = json.loads((self.cdir / "测试文件.json").read_text(encoding="utf-8"))
        self.assertIn("tests/test_guard.py", listed)

    def check_failed_guard(self, why):
        self.init_git()
        observations = []

        def tester(prompt):
            (self.worktree / "tests" / "test_base.py").write_text("# 没交成的覆盖\n", encoding="utf-8")
            (self.worktree / "tests" / "test_half.py").write_text("# 半成品\n", encoding="utf-8")
            return result(why, ok=False)

        def developer(number, prompt):
            if number == 2:
                observations.append(((self.worktree / "tests" / "test_base.py").read_text(encoding="utf-8"),
                                     (self.worktree / "tests" / "test_half.py").exists()))

        card, _ = self.flow([CURRENT, PASS_REVIEW], tester=tester, developer=developer)
        self.require_guard()
        self.assertEqual(card.state, "待验收", "补测没交成，仍然继续派开发修问题")
        self.assertEqual(observations, [(BASE_TEST, False)], "派开发前先清理半成品并恢复原测试")
        self.assertTrue(any("补测试没成功" in e.get("summary", "") for e in read_flow(self.cdir)))

    def test_failed_guard_cleans_partial_files_and_continues(self):
        self.check_failed_guard("补测进程失败")

    def test_timed_out_guard_cleans_partial_files_and_continues(self):
        self.check_failed_guard("超时：补测没有完成")

    def test_test_problem_still_uses_tester_correction_branch(self):
        review = "结论：不通过\n1. 【测试的问题】【轻微】【原来就有的】测试要求和施工单不一致。"

        def tester(prompt):
            (self.worktree / "tests" / "test_base.py").write_text("# 已纠正测试\n", encoding="utf-8")

        self.init_git()
        card, _ = self.flow([review, PASS_REVIEW], tester=tester)
        self.assertEqual(card.state, "待验收")
        self.require_guard()
        self.assertIn("测试本身写错", self.calls_for("test")[1]["prompt"])
        self.assertEqual((self.worktree / "tests" / "test_base.py").read_text(encoding="utf-8"), "# 已纠正测试\n")

    def check_mixed_test_and_product_problems(self, review):
        self.flow([review, PASS_REVIEW])
        guards = self.require_guard()
        corrections = [c for c in guards if "测试本身写错" in c["prompt"]]
        supplements = [c for c in guards if "编写自动化测试" in c["prompt"]]
        self.assertEqual(len(corrections), 1, "测试写错必须单独派纠正测试，不能混进补测试交代")
        self.assertEqual(len(supplements), 1, "功能问题必须单独派补测试，不能只纠正错误测试")
        self.assertEqual(len(guards), 2, "两类问题各派一次，不能合并成一份交代")
        self.assertIn("测试把旧规则写反", corrections[0]["prompt"])
        self.assertNotIn("断网后一直无法重试", corrections[0]["prompt"])
        self.assertIn("断网后一直无法重试", supplements[0]["prompt"])
        self.assertNotIn("测试把旧规则写反", supplements[0]["prompt"])
        for guard in guards:
            self.assertNotIn("旧提示有错字", guard["prompt"], "延期旧问题不属于本轮补测")
        second_dev = [i for i, c in enumerate(self.calls) if c["who"] == "dev"][1]
        guard_positions = [i for i, c in enumerate(self.calls) if c in guards]
        self.assertTrue(all(i < second_dev for i in guard_positions), "补测和纠正测试都应在修代码前完成")

    def test_mixed_test_and_product_problems_both_reach_tester_before_fix(self):
        review = (MODERATE + "\n2. 【测试的问题】【轻微】【原来就有的】测试把旧规则写反了。\n"
                  "3. 【轻微】【原来就有的】旧提示有错字。")
        self.check_mixed_test_and_product_problems(review)

    def test_numbered_product_followed_by_bullet_test_problem_dispatches_both_jobs(self):
        review = MODERATE + "\n- 【测试的问题】【轻微】【原来就有的】测试把旧规则写反了。"
        self.check_mixed_test_and_product_problems(review)

    def test_bullet_test_problem_after_blank_line_still_gets_correction(self):
        review = MODERATE + "\n\n- 【测试的问题】【轻微】【原来就有的】测试把旧规则写反了。"
        self.check_mixed_test_and_product_problems(review)

    def test_numbered_test_followed_by_bullet_product_problem_dispatches_both_jobs(self):
        review = ("结论：不通过\n1. 【测试的问题】【轻微】【原来就有的】测试把旧规则写反了。\n"
                  "- 【中等】【这次改的】断网后一直无法重试。")
        self.check_mixed_test_and_product_problems(review)


class EffortEdges(FlowCase):
    def check_effort(self, original, expected):
        reviewer = {"codex": "gpt-6.1-sol"}
        if original is not None:
            reviewer["codex_effort"] = original
        roles = {"复查员": reviewer, "测试员": {"codex_effort": "ultra"}, "开发员": {"codex_effort": "max"}}
        before = copy.deepcopy(roles)
        self.flow([MODERATE, MODERATE, PASS_REVIEW], role_models=roles)
        self.assertEqual(roles, before, "降档不能改掉原配置，第三轮也不能累计降档")
        reviews = self.calls_for("review")
        self.assertEqual([c["models"].get("codex_effort") for c in reviews], [original, expected, expected])
        self.assertTrue(all(c["models"]["codex_effort"] == "ultra" for c in self.calls_for("test")))
        self.assertTrue(all(c["models"]["codex_effort"] == "max" for c in self.calls_for("dev")))

    def test_tags_apply_every_round_but_narrow_scope_starts_only_after_first(self):
        self.flow([MODERATE, MODERATE, PASS_REVIEW])
        reviews = self.calls_for("review")
        self.assertEqual(len(reviews), 3)
        scope_note = "这是退回修改后的复查：只看上一次复查以后改动的地方和它直接牵连的地方"
        self.assertNotIn(scope_note, reviews[0]["prompt"])
        for call in reviews:
            self.assertIn("【这次改的】", call["prompt"])
            self.assertIn("【原来就有的】", call["prompt"])
            self.assertNotIn("这是改过以后的复查：", call["prompt"])
        for call in reviews[1:]:
            self.assertIn(scope_note, call["prompt"])

    def test_low_stays_low(self):
        self.check_effort("low", "low")

    def test_medium_becomes_low(self):
        self.check_effort("medium", "low")

    def test_high_becomes_medium(self):
        self.check_effort("high", "medium")

    def test_xhigh_becomes_high_without_accumulating(self):
        self.check_effort("xhigh", "high")

    def test_max_becomes_xhigh(self):
        self.check_effort("max", "xhigh")

    def test_ultra_becomes_max(self):
        self.check_effort("ultra", "max")

    def test_missing_effort_is_not_added(self):
        self.check_effort(None, None)

    def test_unknown_effort_is_kept(self):
        self.check_effort("future-effort", "future-effort")

    def test_station_shows_effort_actually_used_for_latest_dispatch(self):
        record(self.cdir, "开跑", src="程序", models={"复查员": "gpt-6.1-sol（xhigh）"},
               tools={"测试员": "codex", "开发员": "qwen", "复查员": "codex"})
        self.flow([CURRENT, PASS_REVIEW], role_models={"复查员": {"codex": "gpt-6.1-sol", "codex_effort": "xhigh"}})
        self.assertEqual(self.calls_for("review")[-1]["models"]["codex_effort"], "high")
        label = self.station()["model"]
        self.assertIn("high", label)
        self.assertNotIn("xhigh", label, "右栏不能继续显示开跑时的旧强度")


def unittest_output():
    tail_fail = ['  File "tests/test_old.py", line 12, in test_first', '    self.assertEqual(value, 2)',
                 '  File "helpers/check.py", line 9, in check', '    raise AssertionError("wrong")',
                 'AssertionError: first failure', 'expected 2, got 1']
    tail_error = ['  File "tests/test_old.py", line 23, in test_second', '    load_value()',
                  '  File "helpers/load.py", line 4, in load_value', '    raise ValueError("bad")',
                  'ValueError: second failure', 'invalid stored value']
    blocks = []
    for title, tail in (("FAIL: test_first (test_old.T)", tail_fail), ("ERROR: test_second (test_old.T)", tail_error)):
        blocks.append("\n".join(["=" * 70, title, "-" * 70, "Traceback (most recent call last):",
                                  '  File "omitted_dependency.py", line 1, in unrelated',
                                  "    omitted_dependency_call()", *tail, ""]))
    output = ("开头无关日志\n" + "\n".join(blocks) + "-" * 70 +
              "\nRan 8 tests in 0.01s\n\nFAILED (failures=1, errors=1)\n")
    return output, tail_fail + tail_error


class FailureHandover(FlowCase):
    def failed_handover(self, output, *, resume=False):
        card = None
        if resume:
            record(self.cdir, "回复", src="测试员", ok=True, summary="测试已交活")
            record(self.cdir, "判定", src="程序", result=FAIL, round=1, text=output, summary="测试没过")
            (self.cdir / "测试结果.md").write_text(output, encoding="utf-8")
            card = Card("CARD-003", state="开发中", rework=1)
        # 接着跑时程序先重跑一次本卡测试（外部项目试跑 ⑭，2026-11-23）：这里让它仍没过，写代码的才会拿到报错交接
        verdicts = iter([Verdict(FAIL, 1, output), Verdict(PASS, 0, "ok")])
        final, _ = self.flow(judge=lambda *a, **k: next(verdicts), card=card)
        self.assertEqual(final.state, "待验收")
        prompt = self.calls_for("dev")[0 if resume else 1]["prompt"]
        self.assertIn(output, (self.cdir / "测试结果.md").read_text(encoding="utf-8"))
        self.assertTrue(any(output == text_of(self.cdir, e) for e in self.events("判定")), "流转仍保存完整输出")
        return prompt

    def test_short_handover_points_to_complete_output_file(self):
        prompt = self.failed_handover("一条失败输出")
        self.assertIn("完整输出在 测试结果.md", prompt)
        self.assertIn(str(self.cdir / "测试结果.md"), prompt)

    def test_unittest_keeps_each_failure_title_last_six_lines_and_totals(self):
        output, tails = unittest_output()
        prompt = self.failed_handover(output)
        for line in ["FAIL: test_first (test_old.T)", "ERROR: test_second (test_old.T)", *tails,
                     "Ran 8 tests in 0.01s", "FAILED (failures=1, errors=1)"]:
            self.assertIn(line, prompt)
        self.assertFalse("omitted_dependency" in prompt, "每个失败块只交回溯最后六行")
        self.assertNotIn("开头无关日志", prompt)

    def test_pytest_keeps_failed_and_error_lines_only(self):
        output = ("收集测试时的无关输出\n___ test_retry ___\n> assert actual == expected\n"
                  "E   AssertionError: retry failed\nE   assert 1 == 2\n无关的捕获日志\n"
                  "FAILED tests/test_old.py::T::test_retry - AssertionError\n")
        prompt = self.failed_handover(output)
        for line in ("E   AssertionError: retry failed", "E   assert 1 == 2", "FAILED tests/test_old.py::T::test_retry"):
            self.assertIn(line, prompt)
        self.assertNotIn("收集测试时的无关输出", prompt)
        self.assertNotIn("无关的捕获日志", prompt)
        self.assertNotIn("> assert actual", prompt)

    def test_unrecognized_output_keeps_only_last_sixty_lines(self):
        output = "\n".join(f"未知格式日志<{n:03d}>" for n in range(90))
        prompt = self.failed_handover(output)
        self.assertNotIn("未知格式日志<029>", prompt)
        for n in range(30, 90):
            self.assertIn(f"未知格式日志<{n:03d}>", prompt)

    def test_summary_is_capped_at_four_thousand_characters(self):
        prompt = self.failed_handover("𠮷" * 9000)
        self.assertLessEqual(prompt.count("𠮷"), 4000, "测试输出最多四千字")

    def test_unittest_summary_also_obeys_four_thousand_character_limit(self):
        output = ('FAIL: test_long (test_old.T)\n' + '-' * 70 +
                  '\nTraceback (most recent call last):\n  File "tests/test_old.py", line 1, in test_long\n' +
                  'AssertionError: ' + '𠮷' * 9000 + '\n\nRan 1 test in 0.1s\nFAILED (failures=1)\n')
        prompt = self.failed_handover(output)
        self.assertLessEqual(prompt.count("𠮷"), 4000)

    def test_pytest_summary_also_obeys_four_thousand_character_limit(self):
        output = 'E   AssertionError: ' + '𠮷' * 9000 + '\nFAILED tests/test_old.py::test_long\n'
        prompt = self.failed_handover(output)
        self.assertLessEqual(prompt.count("𠮷"), 4000)

    def test_resumed_failed_test_uses_same_short_handover(self):
        output, tails = unittest_output()
        prompt = self.failed_handover(output, resume=True)
        self.assertIn(tails[-1], prompt)
        self.assertIn("FAIL: test_first", prompt)
        self.assertFalse("omitted_dependency" in prompt, "接着跑也不能把旧报错全文重新塞给开发")
        self.assertIn("完整输出在 测试结果.md", prompt)
        self.assertIn(str(self.cdir / "测试结果.md"), prompt)


class OldTestHandover(FlowCase):
    def prepare_old_test(self):
        (self.worktree / "money.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.worktree / "tests" / "test_old.py").write_text("from money import add\n", encoding="utf-8")

    def assert_short_and_archived(self, output):
        prompt = self.calls_for("dev")[1]["prompt"]
        self.assertIn("test_first", prompt)
        self.assertIn("test_second", prompt)
        self.assertIn("AssertionError: first failure", prompt)
        self.assertFalse("omitted_dependency" in prompt, "老测试交接也要去掉回溯前部的无关行")
        self.assertIn("完整输出在 测试结果.md", prompt)
        self.assertIn(str(self.cdir / "测试结果.md"), prompt)
        self.assertTrue(output in (self.cdir / "测试结果.md").read_text(encoding="utf-8"), "完整归档不能丢掉前部日志")
        self.assertTrue(any(text_of(self.cdir, e) == output for e in self.events("判定")))

    def test_related_old_test_failure_is_compressed_and_fully_archived(self):
        self.prepare_old_test()
        output, _ = unittest_output()
        output = "归档开头不能丢\n" + "旧测试运行过程日志" * 3000 + "\n" + output
        old_calls = 0

        def judge(command, **kw):
            nonlocal old_calls
            if isinstance(command, list):
                old_calls += 1
                return Verdict(FAIL, 1, output) if old_calls <= 2 else Verdict(PASS, 0, "ok")
            return Verdict(PASS, 0, "ok")

        card, _ = self.flow(judge=judge, changed_fn=lambda wt: ["money.py"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.calls_for("dev")), 2)
        self.assert_short_and_archived(output)

    def test_background_full_result_is_compressed_and_fully_archived(self):
        self.prepare_old_test()
        output, _ = unittest_output()
        output = "归档开头不能丢\n" + "旧测试运行过程日志" * 3000 + "\n" + output

        class Runner:
            def __init__(self):
                self.runs = iter([Verdict(FAIL, 1, output), Verdict(PASS, 0, "ok")])

            def run(self, worktree, stop):
                return next(self.runs)

            def base(self, worktree, files, stop):
                return {name: Verdict(PASS, 0, "ok") for name in files}

        card, _ = self.flow(full_runner=Runner())
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.calls_for("dev")), 2)
        self.assert_short_and_archived(output)


if __name__ == "__main__":
    unittest.main()
