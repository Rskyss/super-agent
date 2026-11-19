"""第四轮 54 复查退回：每条问题对应一个回归测试。"""

import subprocess
import unittest
from unittest.mock import patch

from collab import dispatch
from collab.agents import AgentResult
from collab.flow import read_flow, record, text_of
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from desktop.view import card_view
from test_flowfix_c15_acceptance import Base, NOW, item, review


class Interrupted(RuntimeError):
    """模拟流转记录落盘后，跑卡进程立即退出。"""


class ReviewRegressions(Base):
    def run_flow(self, reviews, *, resume=False, on_work=None):
        remaining = iter(reviews)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            task = self.events("派活")[-1]
            if task["dst"] == "复查员":
                answer = next(remaining, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            else:
                if on_work:
                    on_work(task["step"])
                answer = "汇报：已交活"
            return AgentResult(ok=True, output="", attempts=1, final=answer)

        card = (Card.load(self.cdir / "状态.json") if resume
                else Card(self.cdir.name, state="待派发"))
        card, _ = dispatch.run_card(
            card, self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=ask, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
            reviewer_adapter="codex",
        )
        return card

    def events(self, kind):
        return [event for event in read_flow(self.cdir) if event["kind"] == kind]

    def prompts(self, role):
        return [text_of(self.cdir, task) for task in self.events("派活") if task["dst"] == role]

    def interrupt_after(self, kind, **fields):
        def save_then_stop(cdir, event_kind, *args, **kwargs):
            event = record(cdir, event_kind, *args, **kwargs)
            if event_kind == kind and all(event.get(key) == value for key, value in fields.items()):
                raise Interrupted
            return event

        return patch.object(dispatch, "record", side_effect=save_then_stop)

    def use_second_card(self):
        original_needs = (self.cdir / "需求卡.md").read_text(encoding="utf-8")
        self.cdir = self.cdir.with_name("CARD-002")
        self.cdir.mkdir()
        (self.cdir / "需求卡.md").write_text(original_needs, encoding="utf-8")

    def review_station_state(self):
        view = card_view(str(self.collab.parent), self.cdir.name)
        return next(station["state"] for station in view["stations"] if station["key"] == "rev")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.worktree, capture_output=True, check=True)

    def test_01_unclosed_list_followed_by_text_fence_requires_rewrite(self):
        original = (
            '结论：通过\n复查清单：\n```json\n{"结论": "通过", "问题": []}\n'
            '```text\n这里开始另一段测试输出\n```\n'
        )

        card = self.run_flow([original, review("通过")])

        prompts = self.prompts("复查员")
        self.assertEqual(len(prompts), 2, "带语言名的代码块开头不能闭合清单，放行前要重写")
        self.assertIn("按固定格式重写", prompts[1])
        self.assertIn(original, prompts[1])
        self.assertEqual(card.state, "待验收")

    def test_02_bad_rewrite_preserves_original_rejection_without_saving_invalid_list(self):
        original = (
            '结论：不通过\n保存会删掉整份草稿。取消之后还会覆盖原文件。\n'
            '复查清单：\n```json\n{"结论": "不通过", "问题": 3}\n```'
        )
        bad_rewrite = "结论：通过\n重写时仍然漏了清单。"

        def outcome():
            return (
                any(original in prompt for prompt in self.prompts("开发员")[1:]),
                [e["清单"] for e in self.events("复查结论") if e["result"] == "退回" and e.get("清单")],
            )

        with self.interrupt_after("回复", src="复查员", summary="结论：通过"), self.assertRaises(Interrupted):
            self.run_flow([original, bad_rewrite])
        self.assertEqual(len(self.prompts("复查员")), 2)
        self.assertEqual(self.events("复查结论"), [])
        resumed = self.run_flow([review("通过")], resume=True)
        resumed_outcome = outcome()

        self.use_second_card()
        uninterrupted = self.run_flow([original, bad_rewrite, review("通过")])

        # C1.8 按新口径改（登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：两种走法都停下转 Claude（原来是整段退回原文），数字问题列表仍不能存成正式清单
        self.assertEqual(
            {"接着跑": resumed_outcome, "连续跑": outcome()},
            {"接着跑": (False, []), "连续跑": (False, [])},
            "两种走法都停下，不整段退回原文，也不存坏清单",
        )
        self.assertEqual((resumed.state, uninterrupted.state), ("需Claude介入", "需Claude介入"))

    def test_03_stop_and_accept_confirmation_do_not_hide_unnumbered_severe_issue(self):
        severe = "恢复草稿时会覆盖所有历史版本"
        minor = "提示按钮的颜色偏浅"
        original = f"结论：不通过\n【严重】【这次改的】{severe}。\n\n1. 【轻微】{minor}。"
        bad_rewrite = f"结论：不通过\n1. 【轻微】{minor}。"

        card = self.run_flow([
            review("不通过", [NOW]), review("不通过", [item(what="退出时没有保存")]),
            original, bad_rewrite,
        ])

        self.assertEqual(len(self.events("退回")), 2)
        self.assertEqual(card.state, "需Claude介入")
        phase = card_phase(str(self.collab.parent), self.cdir.name)
        detail = phase["detail"]
        stop = self.events("转人工")[-1]
        # C1.8 按新口径改（登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：重写仍看不懂直接停下，严重问题在交给 Claude 的原话里（原来是从原话里抢救后写进停下提示）
        self.assertEqual(
            {
                "交给 Claude 的原话保留严重问题": severe in (text_of(self.cdir, stop) or ""),
                "停下提示说原话里有严重": "写到了“严重”" in detail,   # C1.8a 复查第 2 轮后定：停下说明不从原话里挑问题（挑就会漏），只提示“原话里写到了严重”，完整原话在转人工记录里
                "没有误报都很轻微": "都是小问题" not in detail,
                "没有建议直接接受": "点“接受剩下的问题" not in detail,
                "不能直接接受剩下的问题": "accept_issues" not in phase["actions"],
            },
            {"交给 Claude 的原话保留严重问题": True, "停下提示说原话里有严重": True, "没有误报都很轻微": True,
             "没有建议直接接受": True, "不能直接接受剩下的问题": True},
        )

    def test_04_resume_defers_old_minor_issue_even_if_reply_says_reject(self):
        old = item(level="轻微", source="原来就有的", what="历史列表的关闭按钮颜色偏浅")
        original = review("不通过", [old])
        with self.interrupt_after("回复", src="复查员"), self.assertRaises(Interrupted):
            self.run_flow([original])
        self.assertEqual(self.events("复查结论"), [])

        card = self.run_flow([original], resume=True)

        self.assertEqual(len(self.prompts("开发员")), 1, "只有原来就有的轻微问题，续跑不能再派开发员")
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.events("以后再修")), 1)
        self.assertIn(old["现象"], text_of(self.cdir, self.events("以后再修")[0]))

    def test_05_sidebar_does_not_show_pass_after_two_malformed_replies(self):
        bad_rewrite = "结论：通过\n两次都没有写清单。"
        # C1.8 按新口径改（登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：原结论不通过、重写仍不合格 → 停下（原来是退回），右栏照样不能显示绿勾
        self.run_flow(["结论：不通过\n保存时有草稿丢失。", bad_rewrite])
        returned = self.review_station_state()

        self.use_second_card()
        card = self.run_flow(["结论：通过\n检查过保存。", bad_rewrite])
        stopped = self.review_station_state()

        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(
            {"原结论不通过时没有绿勾": returned != "done", "转人工时没有绿勾": stopped != "done"},
            {"原结论不通过时没有绿勾": True, "转人工时没有绿勾": True},
        )

    def test_06_resume_guard_restores_product_file_to_before_first_dispatch(self):
        product = self.worktree / "save.py"
        product.write_text("saved = '开工前'\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "save.py")
        developer_version = "saved = '开发员改过'\n"
        tester_version = "saved = '补测时误改'\n"

        def first_run(step):
            if step == "开发":
                product.write_text(developer_version, encoding="utf-8")
            if step == "补测试":
                product.write_text(tester_version, encoding="utf-8")

        with self.interrupt_after("回复", src="测试员", step="补测试"), self.assertRaises(Interrupted):
            self.run_flow([review("不通过", [NOW])], on_work=first_run)
        self.assertEqual(product.read_text(encoding="utf-8"), tester_version)
        self.assertFalse(any(event["step"] == "补测试" for event in self.events("测试收尾")))

        seen_by_developer = []

        def after_resume(step):
            if step == "开发":
                seen_by_developer.append(product.read_text(encoding="utf-8"))

        card = self.run_flow([review("通过")], resume=True, on_work=after_resume)

        self.assertEqual(card.state, "待验收")
        self.assertEqual(seen_by_developer, [developer_version], "补测试收尾必须还原第一次派活前的开发成果")
        self.assertEqual(product.read_text(encoding="utf-8"), developer_version)

    def test_07_review_prompt_lists_directly_imported_skip_decorators(self):
        self.git("init", "-q")
        (self.worktree / "tests" / "test_save.py").write_text(
            'from unittest import TestCase, skipIf, skipUnless\n\n'
            'class Save(TestCase):\n'
            '    @skipIf(True, "跳过保存检查")\n'
            '    def test_save(self):\n        pass\n\n'
            '    @skipUnless(False, "跳过恢复检查")\n'
            '    def test_restore(self):\n        pass\n',
            encoding="utf-8",
        )

        self.run_flow([review("通过")])

        prompt, = self.prompts("复查员")
        expected = [
            'tests/test_save.py:4 @skipIf(True, "跳过保存检查")',
            'tests/test_save.py:8 @skipUnless(False, "跳过恢复检查")',
        ]
        self.assertEqual([line for line in expected if line not in prompt], [], "复查交代漏掉的跳过语句")

    def test_08_same_symptom_at_another_location_is_still_deferred(self):
        symptom = "点保存后提示不消失"
        current = item(what=symptom, where="editor/save.py:24")
        old = item(what=symptom, where="archive/save.py:87", source="原来就有的")

        self.run_flow([review("不通过", [current, old]), review("通过")])

        fix = self.prompts("开发员")[1]
        self.assertIn(current["位置"], fix)
        self.assertNotIn(old["位置"], fix)
        deferred = self.events("以后再修")
        self.assertEqual(len(deferred), 1, "现象相同、位置不同的旧问题也要记进以后再修")
        self.assertIn(symptom, text_of(self.cdir, deferred[0]))

    def test_09_long_return_hands_off_the_last_problem_with_location_and_evidence(self):
        long_proof = {"方式": "实跑", "命令": "python3 -m unittest tests.test_save",
                      "输出": "逐行保存日志\n" * 1500}
        last_proof = {"方式": "实跑", "命令": "python3 -m unittest tests.test_restore",
                      "输出": "AssertionError: missing backup at checkpoint 427"}
        last = item(what="恢复时漏掉最后一份备份", where="backup/restore.py:427", proof=last_proof)
        original = review("不通过", [item(proof=long_proof), last])

        self.run_flow([original, review("通过")])

        prompt = self.prompts("开发员")[1]
        fragments = [last["现象"], last["位置"], last_proof["命令"], last_proof["输出"]]
        missing = [fragment for fragment in fragments if fragment not in prompt]
        reply = next(event for event in self.events("回复") if event["src"] == "复查员")
        sources = ["复查结果.md", reply["text"]]
        has_complete_reference = any(
            source in prompt and (self.cdir / source).is_file()
            and all(fragment in (self.cdir / source).read_text(encoding="utf-8") for fragment in fragments)
            for source in sources
        )
        self.assertTrue(not missing or has_complete_reference, f"退回交代缺少 {missing}，也没给完整意见的查阅位置")

    def test_10_same_filename_in_different_directories_is_not_same_place(self):
        first = item(what="保存时覆盖旧文件", where="editor/save.py:24")
        second = item(level="严重", what="导出时进度一直不动", where="archive/save.py:87")  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的

        self.run_flow([review("不通过", [first]), review("不通过", [second]), review("通过")])

        prompt = self.prompts("开发员")[2]
        self.assertIn(second["位置"], prompt)
        self.assertNotIn("都出在同一处", prompt, "目录不同的两个 save.py 不是同一处")


class TestOutputRegressions(unittest.TestCase):
    def test_11_unittest_failure_summary_drops_bullet_logs_mentioning_python_files(self):
        listing = "- tests/test_save.py：test_save_keeps_draft"
        log = "- 准备运行时已加载 editor/save.py"
        output = (
            f"本卡测试过了，但全部测试里这些老测试没过：\n{listing}\n\n{log}\n"
            "======================================================================\n"
            "FAIL: test_save_keeps_draft (tests.test_save.Save)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            '  File "/checkout/tests/test_save.py", line 42, in test_save_keeps_draft\n'
            "AssertionError: 找不到草稿\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.01s\n\nFAILED (failures=1)\n"
        )

        summary = dispatch._short_test_output(output)

        self.assertIn(listing, summary)
        self.assertIn('tests/test_save.py", line 42', summary)
        self.assertIn("AssertionError: 找不到草稿", summary)
        self.assertNotIn(log, summary, "失败块外的日志只提到 .py 文件，不能当成失败项目")
