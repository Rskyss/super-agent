"""第四轮 54 再次复查退回的九条问题。"""

import subprocess
import unittest
from unittest.mock import patch

from collab import dispatch
from collab.agents import AgentResult
from collab.flow import read_flow, record, text_of
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from test_flowfix4_converge import Base
from test_flowfix_c15_acceptance import item, review


class Interrupted(RuntimeError):
    """在指定的流转记录落盘前后模拟中断。"""


class ReviewReturns(Base):
    def run_flow(self, reviews, *, resume=False, worker=None):
        replies = iter(reviews)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            task = self.events("派活")[-1]
            if task["dst"] == "复查员":
                answer = next(replies, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            else:
                result = worker(task["step"]) if worker else None
                if result is not None:
                    return result
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

    def events(self, kind, **fields):
        return [event for event in read_flow(self.cdir) if event["kind"] == kind
                and all(event.get(key) == value for key, value in fields.items())]

    def prompts(self, role):
        return [text_of(self.cdir, task) for task in self.events("派活", dst=role)]

    def interrupt_at(self, kind, *, before=False, occurrence=1, **fields):
        seen = 0

        def record_or_stop(cdir, event_kind, *args, **kwargs):
            nonlocal seen
            if event_kind == kind and all(kwargs.get(key) == value for key, value in fields.items()):
                seen += 1
                if seen == occurrence:
                    if not before:
                        record(cdir, event_kind, *args, **kwargs)
                    raise Interrupted
            return record(cdir, event_kind, *args, **kwargs)

        return patch.object(dispatch, "record", side_effect=record_or_stop)

    def init_git(self):
        subprocess.run(["git", "init", "-q"], cwd=self.worktree, capture_output=True, check=True)

    def test_01_inline_json_opening_requires_rewrite_before_acceptance(self):
        original = review("通过").replace(
            "\n```json\n", "\n清单接在这一行普通文字后 ```json\n", 1,
        )

        card = self.run_flow([original, "结论：通过\n重写时还是漏了清单。"])

        self.assertEqual(
            (len(self.prompts("复查员")), card.state), (2, "需Claude介入"),
            "清单开头必须独立成行，重写仍不合格就不能进入待验收",
        )
        self.assertIn("按固定格式重写", self.prompts("复查员")[1])

    def test_02_stop_and_confirmation_keep_severe_issue_from_invalid_list(self):
        severe = item(level="严重", what="换设备后会覆盖全部已保存的草稿",
                      proof={"方式": "实跑", "命令": "python3 -m unittest tests.test_save"})
        minor = "1. 【轻微】【这次改的】关闭按钮颜色偏浅。"
        original = review("不通过", [severe], prose=minor)

        card = self.run_flow([
            review("不通过", [item(what="搜索时一直转圈")]),
            review("不通过", [item(what="导出时没有生成文件")]),
            original, f"结论：不通过\n{minor}",
        ])

        self.assertEqual(len(self.events("退回")), 2)
        self.assertEqual(card.state, "需Claude介入")
        self.assertIn("按固定格式重写", self.prompts("复查员")[-1])
        phase = card_phase(str(self.collab.parent), self.cdir.name)
        stop = self.events("转人工")[-1]
        # C1.8 按新口径改（登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：重写仍看不懂直接停下，严重问题在交给 Claude 的原话里；这种停下不能“接受剩下的问题”
        self.assertEqual(
            {
                "交给 Claude 的原话列出严重问题": severe["现象"] in (text_of(self.cdir, stop) or ""),
                "停下提示说原话里有严重": "写到了“严重”" in phase["detail"],   # C1.8a 复查第 2 轮后定：停下说明不从原话里挑问题（挑就会漏），只提示“原话里写到了严重”，完整原话在转人工记录里
                "能直接接受剩下的问题": "accept_issues" in phase["actions"],
                "把剩下的都说成小问题": "都是小问题" in phase["detail"],
                "建议接受剩下的问题": "点“接受剩下的问题" in phase["detail"],
            },
            {"交给 Claude 的原话列出严重问题": True, "停下提示说原话里有严重": True, "能直接接受剩下的问题": False,
             "把剩下的都说成小问题": False, "建议接受剩下的问题": False},
        )

    def test_03_resume_checks_return_limit_after_third_rejection_was_saved(self):
        reviews = [
            review("不通过", [item(level="严重", what="搜索时一直转圈")]),  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的
            review("不通过", [item(level="严重", what="导出时没有生成文件")]),
            review("通过", [item(level="严重", what="清空搜索时删除了草稿")]),
        ]
        with self.interrupt_at("复查结论", occurrence=3, result="退回"), self.assertRaises(Interrupted):
            self.run_flow(reviews)
        self.assertEqual(len(self.events("退回")), 2)
        sent = self.events("派活")

        card = self.run_flow([review("通过")], resume=True)

        new_tasks = [(e["dst"], e["step"]) for e in self.events("派活")[len(sent):]]
        self.assertEqual(
            (card.state, new_tasks), ("需Claude介入", []),
            "第三次退回判定已存，续跑应当停下，不能再派补测、开发或复查",
        )

    def test_04_resume_returns_full_original_and_records_it_in_review_history(self):
        original = (
            "结论：不通过\n1. 保存草稿后列表没更新。\n"
            + "".join(f"第 {i:04d} 条保存过程记录，草稿仍显示旧版本。\n" for i in range(500))
            + "2. 恢复时遗漏了最后一份草稿。位置：backup/restore.py:427"
        )
        with self.interrupt_at("回复", src="复查员"), self.assertRaises(Interrupted):
            self.run_flow([original])
        reply, = self.events("回复", src="复查员")
        self.assertEqual(text_of(self.cdir, reply), original)

        card = self.run_flow(["结论：不通过\n重写时依然漏了清单。", review("通过")], resume=True)

        self.assertIn("按固定格式重写", self.prompts("复查员")[1])
        stop = self.events("转人工")[-1]
        # C1.8 按新口径改（登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：续跑时重写仍看不懂 → 停下，整段原文完整交给 Claude（原来是整段交给开发员）
        self.assertEqual(
            {"停下": card.state == "需Claude介入",
             "没有再派开发员": len(self.prompts("开发员")) == 1,
             "交给 Claude 的含整段原文": original in (text_of(self.cdir, stop) or "")},
            {"停下": True, "没有再派开发员": True, "交给 Claude 的含整段原文": True},
        )

    def test_05_interrupted_cleanup_and_failed_retry_restore_pre_guard_tests(self):
        self.init_git()
        test_file = self.worktree / "tests" / "test_save.py"
        baseline = "def test_save_keeps_draft():\n    assert '草稿'.strip() == '草稿'\n"
        first_edit = baseline + "\ndef test_first_attempt():\n    assert False\n"
        retry_edit = baseline + "\ndef test_second_attempt():\n    assert False\n"

        def first_run(step):
            if step == "写测试":
                test_file.write_text(baseline, encoding="utf-8")
            if step == "补测试":
                test_file.write_text(first_edit, encoding="utf-8")

        with self.interrupt_at("测试收尾", before=True, step="补测试"), self.assertRaises(Interrupted):
            self.run_flow([review("不通过", [item()])], worker=first_run)
        self.assertEqual(len(self.events("回复", src="测试员", step="补测试", ok=True)), 1)
        self.assertEqual(self.events("测试收尾", step="补测试"), [])

        before_retry = []

        def retry(step):
            if step == "补测试":
                before_retry.append(test_file.read_text(encoding="utf-8"))
                test_file.write_text(retry_edit, encoding="utf-8")
                return AgentResult(ok=False, output="补测没交成", attempts=1)

        self.run_flow([review("通过")], resume=True, worker=retry)

        self.assertEqual(len(self.events("回复", src="测试员", step="补测试", ok=False)), 1)
        self.assertEqual(
            (before_retry, test_file.read_text(encoding="utf-8")), ([baseline], baseline),
            "重派开始前和失败回退后都应恢复第一次补测前的测试",
        )

    def test_06_resume_uses_review_already_recorded_as_deferred(self):
        old = item(level="轻微", source="原来就有的", what="历史列表的关闭按钮颜色偏浅")
        original = review("不通过", [old])
        with self.interrupt_at("以后再修"), self.assertRaises(Interrupted):
            self.run_flow([original])
        reply, = self.events("回复", src="复查员")

        card = self.run_flow([original], resume=True)

        self.assertEqual(
            (card.state, len(self.prompts("复查员")), len(self.events("以后再修"))),
            ("待验收", 1, 1),
            "已交回的清单应当接着判定，不重复派复查，也不重复记以后再修",
        )
        conclusion, = self.events("复查结论")
        self.assertEqual((conclusion["result"], conclusion["reply"]), ("通过", reply["seq"]))

    def test_07_review_prompt_cites_skipped_parameterized_test(self):
        self.init_git()
        skip = "test.skip.each(['草稿', '空白'])('保存 %s', draft => expect(draft.trim()).toBe(draft));"
        test_file = self.worktree / "tests" / "save.test.js"
        test_file.write_text(f"import {{ expect, test }} from '@jest/globals';\n{skip}\n", encoding="utf-8")

        self.run_flow([review("通过")])

        prompt, = self.prompts("复查员")
        self.assertIn(f"tests/save.test.js:2 {skip}", prompt, "整组跳过也要带文件、行号和原文提醒复查员")

    def test_08_paths_with_spaces_in_different_directories_are_not_same_place(self):
        first = item(what="保存时覆盖旧文件", where="first draft/save.py:24")
        second = item(level="严重", what="导出时进度一直不动", where="second draft/save.py:87")  # C1.6：不严重的退满会自动进待验收 / 第 2 轮起新冒出的不严重问题不退回；这里测原来那件事，问题改成严重的

        self.run_flow([review("不通过", [first]), review("不通过", [second]), review("通过")])

        prompt = self.prompts("开发员")[2]
        self.assertIn(second["位置"], prompt)
        self.assertNotIn("都出在同一处", prompt, "两个带空格的目录各有一个 save.py，不能合并成一处")


class TestFailureSummary(unittest.TestCase):
    def test_09_unittest_preparation_log_is_not_a_failed_test_listing(self):
        prep = "- tests/test_save.py: 正在准备数据"
        failure = (
            "======================================================================\n"
            "FAIL: test_keeps_draft (tests.test_save.Save)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            '  File "/checkout/tests/test_save.py", line 42, in test_keeps_draft\n'
            "AssertionError: 找不到草稿\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.01s\n\nFAILED (failures=1)\n"
        )
        listing = "- tests/test_save.py：test_keeps_draft"
        fullrun = "本卡测试过了，但全部测试里这些老测试没过：\n" + listing

        ordinary_summary = dispatch._short_test_output(f"{prep}\n{failure}")
        fullrun_summary = dispatch._short_test_output(f"{fullrun}\n\n{failure}")

        self.assertIn("FAIL: test_keeps_draft", ordinary_summary)
        self.assertIn('tests/test_save.py", line 42', ordinary_summary)
        self.assertIn("AssertionError: 找不到草稿", ordinary_summary)
        self.assertIn(listing, fullrun_summary)
        self.assertNotIn(prep, ordinary_summary, "失败块前的准备日志不能当成程序列出的失败项目")
