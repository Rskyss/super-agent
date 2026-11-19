"""C1.5 补测：用临时副本和流转记录检查重写、再次复查和断点续跑。"""

import json
import subprocess

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow, record, text_of
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from desktop.view import card_view
import test_flowfix_c15_acceptance as acceptance


class ReviewEdges(acceptance.Base):
    def run_card(self, replies, *, state="待派发", tester=None, draft=False):
        replies = iter(replies)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            sent = next(e for e in reversed(read_flow(self.cdir)) if e["kind"] == "派活")
            if sent["dst"] == "复查员":
                answer = next(replies, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            elif sent["dst"] == "测试员":
                if tester:
                    tester(sent["step"])
                answer = "汇报：测试写好了"
            else:
                answer = "汇报：已处理"
            return AgentResult(ok=True, output="", attempts=1, final=answer)

        card, _ = run_card(
            Card("CARD-001", state=state), self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=ask, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
            reviewer_adapter="codex", draft=draft,
        )
        return card

    def events(self, kind):
        return [e for e in read_flow(self.cdir) if e["kind"] == kind]

    def prompts(self, role):
        return [text_of(self.cdir, e) for e in self.events("派活") if e["dst"] == role]

    def record_conclusion(self, raw, result, listed):
        reply = record(self.cdir, "回复", src="复查员", ok=True, step="复查", round=1,
                       summary=raw.splitlines()[0], text=raw)
        record(self.cdir, "复查结论", src="程序", reply=reply["seq"], result=result, 清单=listed)

    def test_malformed_last_json_forces_rewrite_even_with_earlier_valid_list(self):
        original = acceptance.review("通过") + "\n```json\n{\"结论\":\n```"
        fixed = acceptance.review("通过", [acceptance.NOW])

        card = self.run_card([original, fixed, acceptance.review("通过")])

        prompts = self.prompts("复查员")
        self.assertEqual(len(prompts), 3, "前一个清单合格，末尾清单坏了也要重写，然后复查修正后的代码")
        self.assertIn("按固定格式重写", prompts[1])
        self.assertIn("不用重新复查", prompts[1])
        self.assertIn(original, prompts[1])
        first = self.events("复查结论")[0]
        self.assertEqual(first["result"], "退回")
        self.assertEqual(first.get("清单"), {"结论": "通过", "问题": [acceptance.NOW]})
        self.assertEqual(card.state, "待验收")

    def test_last_valid_list_wins_over_earlier_example(self):
        example = json.dumps({"结论": "不通过", "问题": [acceptance.NOW]}, ensure_ascii=False)
        raw = acceptance.review("通过", prose=f"举例：\n```json\n{example}\n```")

        card = self.run_card([raw])

        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.prompts("复查员")), 1)
        conclusion, = self.events("复查结论")
        self.assertEqual(conclusion["result"], "通过")
        self.assertEqual(conclusion.get("清单"), {"结论": "通过", "问题": []})

    def test_bad_rewrite_cannot_change_original_pass_into_automatic_return(self):
        card = self.run_card(["结论：通过\n看过保存流程。", "结论：不通过\n这次仍漏了清单。"])

        self.assertEqual(len(self.prompts("复查员")), 2, "格式最多重写一次")
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.prompts("开发员")), 1)
        self.assertIn("复查结论格式不合格，不能自动放行", self.events("转人工")[-1]["summary"])

    def test_second_review_lists_old_changes_and_only_new_skips_with_line_numbers(self):
        tests = self.worktree / "tests"
        old = tests / "test_old.py"
        old.write_text('import unittest\n\nclass Old(unittest.TestCase):\n'
                       '    @unittest.skip("开工前就跳过")\n    def test_existing(self):\n        pass\n\n'
                       '    def test_save(self):\n        pass\n', encoding="utf-8")
        deleted = tests / "test_deleted.py"
        deleted.write_text("# 开工前已有的测试文件\n", encoding="utf-8")
        for args in (["init", "-q"], ["add", "tests"],
                     ["-c", "user.name=测试", "-c", "user.email=test@example.invalid", "commit", "-qm", "测试基线"]):
            subprocess.run(["git", *args], cwd=self.worktree, check=True, capture_output=True)

        def tester(step):
            if step == "写测试":
                old.write_text(old.read_text(encoding="utf-8").replace(
                    "    def test_save", '    @unittest.skip("这轮给老用例加的")\n    def test_save'),
                    encoding="utf-8")
                deleted.unlink()
            elif step == "补测试":
                (tests / "test_new.py").write_text(
                    'import unittest\n\nclass New(unittest.TestCase):\n'
                    '    def test_skip_button_hides(self):\n        pass\n\n'
                    '    def test_export(self):\n        self.skipTest("补测时新加的")\n', encoding="utf-8")

        self.run_card([acceptance.review("不通过", [acceptance.NOW]), acceptance.review("通过")], tester=tester)

        first, second = self.prompts("复查员")
        self.assertNotIn("补测时新加的", first)
        self.assertIn("tests/test_deleted.py", second, "第二轮仍要提醒老测试被删")
        for location, code in (("tests/test_old.py:8", '@unittest.skip("这轮给老用例加的")'),
                               ("tests/test_new.py:8", 'self.skipTest("补测时新加的")')):
            with self.subTest(location=location):
                self.assertTrue(any(location in line and code in line for line in second.splitlines()),
                                f"第二轮要列出 {location} 和新增的跳过语句")
        self.assertNotIn("开工前就跳过", second)
        self.assertNotIn("test_skip_button_hides", second)
        self.assertRegex(second, r"不合理[^。\n]*判不通过")

    def test_resume_uses_latest_conclusion_and_retries_unfinished_guard(self):
        wrong_test = acceptance.item(kind="测试写错", what="导出测试把列名写反了", where="tests/test_export.py:24")
        record(self.cdir, "回复", src="测试员", ok=True, step="写测试", round=1)
        record(self.cdir, "测试收尾", src="程序", step="写测试", round=1)
        self.record_conclusion("结论：不通过", "退回", {"结论": "不通过", "问题": [acceptance.SEVERE]})
        record(self.cdir, "回复", src="开发员", ok=True, step="开发", round=1)
        self.record_conclusion(
            "结论：通过\n1. 【严重】只在正文里说的丢数据。", "退回",
            {"结论": "通过", "问题": [acceptance.NOW, wrong_test, acceptance.OLD]},
        )
        record(self.cdir, "回复", src="测试员", ok=True, step="改测试", round=1)
        record(self.cdir, "测试收尾", src="程序", step="改测试", round=1)
        # 停在测试员回复之后、收尾之前。续跑时只需重派未收尾的补测试。
        record(self.cdir, "回复", src="测试员", ok=True, step="补测试", round=1)

        card = self.run_card([acceptance.review("通过")], state="开发中")

        tasks = self.events("派活")
        self.assertEqual([(e["dst"], e["step"]) for e in tasks[:2]],
                         [("测试员", "补测试"), ("开发员", "开发")])
        guard, fix = [text_of(self.cdir, e) for e in tasks[:2]]
        for prompt in (guard, fix):
            self.assertIn(acceptance.NOW["现象"], prompt)
            self.assertIn(acceptance.NOW["位置"], prompt)
            self.assertIn(acceptance.NOW["证据"]["命令"], prompt)
            self.assertIn(acceptance.NOW["证据"]["输出"], prompt)
            self.assertNotIn(acceptance.OLD["现象"], prompt)
            self.assertNotIn(acceptance.SEVERE["现象"], prompt)
            self.assertNotIn("只在正文里说的丢数据", prompt)
        self.assertNotIn(wrong_test["现象"], guard)
        cleanup = [e for e in self.events("测试收尾") if e["step"] == "补测试"]
        self.assertEqual(len(cleanup), 1)
        self.assertLess(tasks[0]["seq"], cleanup[0]["seq"])
        self.assertLess(cleanup[0]["seq"], tasks[1]["seq"])
        self.assertEqual(card.state, "待验收")

    def test_right_panel_shows_return_even_when_reply_says_pass(self):
        Card("CARD-001", state="开发中").save(self.cdir / "状态.json")
        raw = acceptance.review("通过", [acceptance.NOW])
        self.record_conclusion(raw, "退回", {"结论": "通过", "问题": [acceptance.NOW]})

        stations = card_view(str(self.collab.parent), "CARD-001")["stations"]
        self.assertEqual(next(s for s in stations if s["key"] == "rev")["state"], "fail")

    def test_stopped_card_uses_list_over_legacy_issue_text(self):
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", cause="复查不通过", summary="复查不通过",
               清单={"结论": "不通过", "问题": [acceptance.SEVERE]},
               issues=["正文里的旧提示"], text="结论：不通过\n1. 【轻微】正文里的旧提示")

        issues = card_phase(str(self.collab.parent), "CARD-001")["issues"]
        line = next((line for line in issues.splitlines() if acceptance.SEVERE["现象"] in line), "")
        self.assertIn("严重", line)
        self.assertNotIn("正文里的旧提示", issues)

    def test_draft_and_stop_use_structured_issues_after_two_returns(self):
        # C1.6：第 2 轮起新冒出的不严重问题不退回，上轮的按编号核对。这里测“停下和起草都读清单”，
        # 所以第 2 轮那条改成严重的，第 2、3 轮都按编号答上轮“改好了”，每轮实际退回的就是新写的那一条
        rounds = [acceptance.NOW, acceptance.item(level="严重", what="导出后表格列错位"), acceptance.SEVERE]
        prose = "1. 【轻微】【这次改的】只在正文里写的提示颜色。"

        def reply(n, problem):
            data = {"结论": "不通过", "问题": [problem]}
            if n:
                data["上轮核对"] = [{"编号": 1, "结果": "改好了"}]
            block = json.dumps(data, ensure_ascii=False, indent=1)
            return f"结论：不通过\n\n{prose}\n\n复查清单：\n```json\n{block}\n```\n"
        replies = [reply(n, problem) for n, problem in enumerate(rounds)]

        card = self.run_card(replies, draft=True)

        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.events("退回")), 2)
        self.assertEqual(len(self.prompts("开发员")), 3)
        stop = self.events("转人工")[-1]
        self.assertEqual((stop.get("清单") or {}).get("结论"), "不通过")
        self.assertEqual((stop.get("清单") or {}).get("问题"), [acceptance.SEVERE])   # C1.6：清单里可能多一段“上轮核对”，结论和问题分开比
        self.assertNotIn("都是小问题", json.dumps(stop, ensure_ascii=False))
        drafts = [text_of(self.cdir, e) for e in self.events("派活") if e["step"] == "起草"]
        self.assertEqual(len(drafts), 1, "清单里有严重问题，要起草继续改")
        for problem in rounds:
            self.assertIn(problem["现象"], drafts[0])
        self.assertNotIn("只在正文里写的提示颜色", drafts[0])
