"""外部项目试跑修复 · 第一批“别白跑”（docs/外部项目试跑修复/，2026-11-23 用户批准）。

来历：开发助手第一次接外部项目（外部项目 train CARD-001），问题记在本机观察笔记（不进 git）。
T1 ⑲ 测试员复查退回后新建的测试文件，写代码的交活时被范围检查当成“范围外改动”撤掉又恢复，冤枉写代码的。
T2 ⑫ 测试没过、报错全在测试文件自己身上（train 3 处类型写法错误），程序照样退给不许改测试的写代码的，空转 3 轮。
T3 ⑭ 有人接手修好、说“已全过”，接着跑时程序不重跑测试，拿停卡前的旧报错派写代码的白跑一轮。
T4 ⑧⑮ 停卡换人、接手后接着跑，派活的话和第一次一字不差，不说副本里已有什么、谁改过；DeepSeek 因此把来龙去脉说反。
T5 ⑪（用户点名）右栏“本卡合计”下面加干活总耗时，只算干活时间（停卡、等人点按钮不算）。
   样本：tests/fixtures/train001/ 是 train CARD-001 到 18:20 停下时的真实流转（只留时间和类型，没有代码和原文）。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _test_file_errors, run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask

CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 -m unittest tests.test_x"]


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "t@t")
        git(self.project, "config", "user.name", "t")
        (self.project / "app.py").write_text("A = 1\n", encoding="utf-8")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_old.py").write_text("# old\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "init")
        self.wt = self.project
        self.collab = self.project / ".collab"
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        self.prompts = []

    def tearDown(self):
        self.tmp.cleanup()


class NewTestFileAfterReviewIsInScope(Base):
    """T1 ⑲：补测试时新建的测试文件，写代码的交活时不能被当成范围外改动。"""

    def test_new_test_file_from_review_return_is_not_reverted(self):
        reviews = ["结论：不通过\n1. 【中等】【这次改的】按钮没反应。", "结论：通过"]
        tester_calls = []

        def ask_fn(adapter, prompt, cwd, **kw):
            self.prompts.append(prompt)
            wt = Path(cwd)
            if "编写自动化测试" in prompt or "补测试" in prompt:
                tester_calls.append(prompt)
                name = "test_x.py" if len(tester_calls) == 1 else "test_y.py"
                (wt / "tests" / name).write_text(f"# {name}\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了测试")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            (wt / "app.py").write_text(f"A = {len(self.prompts)}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                 ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                 reviewer_adapter="codex", require_tests=True, allowed=["app.py"])
        self.assertGreaterEqual(len(tester_calls), 2, "复查退回后应该先派写测试的补测试")
        self.assertTrue((self.wt / "tests" / "test_y.py").is_file())
        wrong = [e.get("summary", "") for e in read_flow(self.cdir) if e.get("kind") == "范围"
                 and "test_y.py" in e.get("summary", "")]
        self.assertEqual(wrong, [], "写代码的没动补测试新建的测试文件，不能记成范围外改动或它改过测试")

    def test_test_names_with_wildcards_do_not_open_scope(self):
        """复查第 10 条：登记名带 [ ] 这类字符时，不能把别的文件也放进范围"""
        from collab.scope import out_of_scope
        from collab.dispatch import _scope_with_tests
        allowed = _scope_with_tests(["app.py"], ["tests/check[0].py"])
        self.assertEqual(out_of_scope(["tests/check0.py"], allowed), ["tests/check0.py"])
        self.assertEqual(out_of_scope(["tests/check[0].py"], allowed), [])


# train CARD-001 第 1 轮判定的真实报错（类型检查卡在测试文件自己 3 处）
TRAIN_TSC = """\
✔ P6豁免：AI接管已生成方案时，未填过程表不会堵住兜底交卷 (27.553667ms)
ℹ tests 109
ℹ pass 109
ℹ fail 0

> web-app@0.8.2 typecheck /path/to/train-worktrees/CARD-001
> tsc --noEmit

tests/process-worksheets.test.ts(531,72): error TS2741: Property 's' is missing in type 'Payload' but required in type '{ s: string; }'.
tests/process-worksheets.test.ts(839,80): error TS7006: Parameter 'option' implicitly has an 'any' type.
tests/process-worksheets.test.ts(1299,74): error TS2345: Argument of type 'Map<unknown, unknown>' is not assignable to parameter of type 'Map<string, string>'.
  Type 'unknown' is not assignable to type 'string'.
 ELIFECYCLE  Command failed with exit code 1.
"""
TESTS = ["tests/process-worksheets.test.ts", "tests/process-worksheets-review.test.ts"]


class TellTestFileErrors(unittest.TestCase):
    """T2 ⑫：认出“报错全在测试文件自己身上”。宁可少认，不可把产品的错认成测试的错。"""

    def test_train_typecheck_errors_only_in_test_file(self):
        got = _test_file_errors(TRAIN_TSC, TESTS)
        self.assertEqual(len(got), 3, got)
        self.assertTrue(all("process-worksheets.test.ts" in line for line in got))

    def test_absolute_path_still_counts(self):
        out = "/Users/x/train-worktrees/CARD-001/tests/process-worksheets.test.ts:1055:0: ERROR: Unexpected end of file\n"
        self.assertEqual(len(_test_file_errors(out, TESTS)), 1)

    def test_any_product_error_means_not_test_only(self):
        out = TRAIN_TSC + "lib/process-worksheets.ts(12,3): error TS2322: Type 'number' is not assignable to type 'string'.\n"
        self.assertEqual(_test_file_errors(out, TESTS), [])

    def test_assertion_failure_stack_in_test_file_is_not_a_test_error(self):
        """功能没做好时，断言失败的位置本来就在测试文件里——这是写代码的事，不能交回测试员"""
        out = ("✖ P5：已交卷锁定时回显云端草稿 (93ms)\n  AssertionError [ERR_ASSERTION]: 应有brief-pane-tab-tasks，实际找到0处\n"
               "      at one (/Users/x/CARD-001/tests/process-worksheets.test.ts:303:10)\n"
               "      at TestContext.<anonymous> (/Users/x/CARD-001/tests/process-worksheets.test.ts:1130:101)\n"
               "ℹ fail 1\n")
        self.assertEqual(_test_file_errors(out, TESTS), [])

    def test_python_assertion_in_test_file_is_not_a_test_error(self):
        out = ('FAIL: test_a (test_x.T.test_a)\nTraceback (most recent call last):\n'
               '  File "/p/tests/test_x.py", line 9, in test_a\n    self.assertEqual(f(), 2)\nAssertionError: 1 != 2\n')
        self.assertEqual(_test_file_errors(out, ["tests/test_x.py"]), [])

    def test_python_syntax_error_in_test_file_counts(self):
        out = ('  File "/p/tests/test_x.py", line 9\n    def broken(\n              ^\n'
               'SyntaxError: \'(\' was never closed\n')
        self.assertEqual(len(_test_file_errors(out, ["tests/test_x.py"])), 1)

    def test_unregistered_file_does_not_count(self):
        out = "tests/other.test.ts(3,1): error TS2304: Cannot find name 'x'.\n"
        self.assertEqual(_test_file_errors(out, TESTS), [])

    def test_no_errors_no_route(self):
        self.assertEqual(_test_file_errors("ℹ fail 3\n", TESTS), [])


class TestFileErrorsGoBackToTester(Base):
    """T2 ⑫：跑卡时报错全在测试文件里 → 交回写测试的改，不再退给写代码的。"""

    def run_card_with(self, verdicts):
        self.devs, self.testers = [], []
        verdicts = list(verdicts)

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt or "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                (wt / "tests" / "test_x.py").write_text(f"# v{len(self.testers)}\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="改好了")
            self.devs.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.devs) + 1}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        def judge(*a, **k):
            return verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok")

        return run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn), judge_fn=judge, reviewer_adapter="codex",
                        require_tests=True, allowed=["app.py"])[0]

    def test_typecheck_errors_in_test_file_go_to_tester_then_review(self):
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        card = self.run_card_with([Verdict(FAIL, 1, out)])
        self.assertEqual(len(self.devs), 1, "写代码的只该干一次，测试文件的错交回写测试的")
        self.assertEqual(len(self.testers), 2, "写测试的：开工一次 + 改测试文件的错一次")
        self.assertIn("tests/test_x.py:9:1", self.testers[1], "把报错原文交给写测试的")
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 0, "测试文件写错不算写代码的返工")
        kinds = [(e.get("kind"), e.get("dst")) for e in read_flow(self.cdir)]
        self.assertIn(("交回测试", "测试员"), kinds)

    def test_mixed_with_product_error_goes_to_developer(self):
        out = ("tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
               "app.py:1:1: error: Incompatible types  [assignment]\n")
        card = self.run_card_with([Verdict(FAIL, 1, out)])
        self.assertEqual(len(self.testers), 1)
        self.assertEqual(len(self.devs), 2, "有一处在产品代码就照旧退写代码的")
        self.assertEqual(card.rework, 1)

    def test_tester_gets_at_most_two_tries_then_stop(self):
        """复查第 5 条：方案写的是交回 2 次还不过就停卡转人工，不扣写代码的返工"""
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        card = self.run_card_with([Verdict(FAIL, 1, out), Verdict(FAIL, 1, out + " "), Verdict(FAIL, 1, out + "  ")])
        self.assertEqual(len(self.testers), 3, "开工一次 + 交回最多 2 次")
        self.assertEqual(len(self.devs), 1, "不再退给写代码的")
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(card.rework, 0)

    def test_tester_deleting_all_tests_stops(self):
        """复查第 3 条：改测试时把本卡测试删光，不能接着判定放行"""
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, out)]
        self.devs, self.testers = [], []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                (wt / "tests" / "test_x.py").unlink()
                return AgentResult(ok=True, output="", attempts=1, final="删掉了")
            self.devs.append(prompt)
            (wt / "app.py").write_text("A = 2\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(card.state, "需Claude介入")

    def test_tester_says_product_missing_goes_back_to_developer(self):
        out = "tests/test_x.py:9:1: error: Module \"app\" has no attribute \"total\"  [attr-defined]\n"
        verdicts = [Verdict(FAIL, 1, out)]
        self.devs, self.testers = [], []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                self.testers.append(prompt)
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                return AgentResult(ok=True, output="", attempts=1,
                                   final="不是测试的问题：产品代码里还没有 total 这个函数，测试是按施工单写的。")
            self.devs.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.devs) + 1}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                 ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                 reviewer_adapter="codex", require_tests=True, allowed=["app.py"])
        self.assertEqual(len(self.testers), 2)
        self.assertEqual(len(self.devs), 2, "写测试的说是产品没写，转回写代码的")
        self.assertIn("产品代码里还没有 total", self.devs[1], "把写测试的判断带给写代码的")

    def test_tester_says_product_but_deleted_tests_stops(self):
        """第 3 条：写测试的回“不是测试的问题”，但把测试删光了，也要停"""
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, out)]

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                (wt / "tests" / "test_x.py").unlink()
                return AgentResult(ok=True, output="", attempts=1, final="不是测试的问题：产品没写。")
            (wt / "app.py").write_text("A = 3\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(card.state, "需Claude介入")

    def test_tester_failing_to_deliver_stops_not_developer(self):
        """新问题 3：写测试的没交成（超时、报错），不能把测试文件的错退给写代码的"""
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, out)]
        self.devs = []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                return AgentResult(ok=False, output="超时：没有交活", attempts=1, final="")
            self.devs.append(prompt)
            (wt / "app.py").write_text("A = 3\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(len(self.devs), 1, "不退给写代码的")
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(card.rework, 0)

    def test_product_judgment_only_covers_those_errors(self):
        """新问题 4：写测试的说“是产品问题”只管那几条；之后冒出不同的测试文件错误，照样交回写测试的"""
        first = "tests/test_x.py:9:1: error: Module \"app\" has no attribute \"total\"  [attr-defined]\n"
        second = "tests/test_x.py:20:1: error: Name \"bar\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, first), Verdict(FAIL, 1, second)]
        self.devs, self.testers = [], []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                if len(self.testers) == 1:
                    return AgentResult(ok=True, output="", attempts=1, final="不是测试的问题：产品没有 total。")
                (wt / "tests" / "test_x.py").write_text("# fixed\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="改好了")
            self.devs.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.devs) + 2}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(len(self.testers), 2, "第二次不同的测试文件错误照样交回写测试的")
        self.assertEqual(card.state, "待验收")

    def test_marker_only_counts_on_first_line(self):
        """第 3 轮第 2 条：暗号只认第一行；正文里顺带提到不算"""
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, out)]
        self.devs = []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                (wt / "tests" / "test_x.py").write_text("# fixed\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1,
                                   final="已修好。\n没有发现需要用『不是测试的问题』标记的产品缺失。")
            self.devs.append(prompt)
            (wt / "app.py").write_text("A = 3\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(len(self.devs), 1, "修好了就重新判定，不转回写代码的")
        self.assertEqual(card.state, "待验收")

    def test_product_judgment_covers_remaining_subset(self):
        """第 2 轮新增 4 续：写测试的说三条都是产品问题，写代码的修掉一条后剩下两条，仍归写代码的"""
        e = ["tests/test_x.py:%d:1: error: Module \"app\" has no attribute \"f%d\"  [attr-defined]\n" % (i, i) for i in (1, 2, 3)]
        verdicts = [Verdict(FAIL, 1, "".join(e)), Verdict(FAIL, 1, e[1] + e[2]), Verdict(FAIL, 1, e[2])]
        self.devs, self.testers = [], []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final="不是测试的问题：三个函数产品都没写。")
            self.devs.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.devs) + 2}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(len(self.testers), 1, "剩下的是写测试的已判断过的那几条，不再交回")
        self.assertEqual(card.state, "待验收")

    def test_returns_counted_per_run(self):
        """新问题 2：交回次数按这次开跑算，之前开跑里修好的不占这次的名额"""
        from collab.dispatch import _test_file_returns
        record(self.cdir, "开跑", src="调度员", summary="开始施工")
        record(self.cdir, "交回测试", src="程序", summary="1")
        record(self.cdir, "交回测试", src="程序", summary="2")
        record(self.cdir, "开跑", src="调度员", summary="按新要求继续施工")
        self.assertEqual(_test_file_returns(self.cdir), 0)
        record(self.cdir, "交回测试", src="程序", summary="3")
        self.assertEqual(_test_file_returns(self.cdir), 1)

    def test_product_route_is_not_cut_as_spinning(self):
        """复查第 5 条：写代码的上一轮也是这份报错时，转回去不能被当成“原地打转”直接停卡"""
        out = "tests/test_x.py:9:1: error: Module \"app\" has no attribute \"total\"  [attr-defined]\n"
        mixed = out + "app.py:1:1: error: Incompatible types  [assignment]\n"
        verdicts = [Verdict(FAIL, 1, mixed), Verdict(FAIL, 1, out)]
        self.devs, self.testers = [], []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "报错全在测试文件自己身上" in prompt:
                self.testers.append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final="不是测试的问题：产品没有 total。")
            self.devs.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.devs) + 1}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                 ask_fn=listed_ask(ask_fn),
                 judge_fn=lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"),
                 reviewer_adapter="codex", require_tests=True, allowed=["app.py"])
        self.assertEqual(len(self.testers), 1)
        self.assertEqual(len(self.devs), 3)
        self.assertIn("产品没有 total", self.devs[2])


class ResumeBase(Base):
    """停在“测试没过”、有人接手后接着跑的场景（T3、T4 共用）。"""

    def setUp(self):
        super().setUp()
        self.devs = []

    def ask(self, adapter, prompt, cwd, **kw):
        wt = Path(cwd)
        if "只读复查" in prompt:
            return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
        if "编写自动化测试" in prompt:
            (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="写好了")
        self.devs.append(prompt)
        (wt / "app.py").write_text(f"A = {len(self.devs) + 1}\n", encoding="utf-8")
        return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

    def run_with(self, judge):
        return run_card(self.card, self.collab, self.wt, CMD, "cursor", "codex", ask_fn=listed_ask(self.ask),
                        judge_fn=judge, reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]

    def stop_after_reworks(self):
        n = iter(range(100))
        self.card = Card("CARD-001", state="待派发")
        self.card = self.run_with(lambda *a, **k: Verdict(FAIL, 1, f"OLD-ERROR {next(n)}"))
        self.assertEqual(self.card.state, "需Claude介入")
        record(self.cdir, "接手", src="Gemini", summary="修好了测试文件，验收命令全绿，送回复查")
        self.card.transition("开发中")   # 和 collab run --resume-blocked 一样
        self.before = len(self.devs)


class ResumeAfterFailRejudges(ResumeBase):
    """T3 ⑭：停在“测试没过”后接着跑，先重跑本卡测试。"""

    def test_takeover_fixed_goes_straight_to_review(self):
        self.stop_after_reworks()
        card = self.run_with(lambda *a, **k: Verdict(PASS, 0, "ok"))
        self.assertEqual(len(self.devs), self.before, "接手已修好，不该再派写代码的")
        self.assertEqual(card.state, "待验收")
        self.assertTrue(any(e.get("kind") == "接着走" and "已经过了" in e.get("summary", "")
                            for e in read_flow(self.cdir)))

    def test_rejudge_pass_is_checked_again_after_restoring(self):
        """复查第 1 条：先重跑过了，但之后程序还会撤越界改动、还原测试快照——必须再判定一次，不能拿先前的“过了”放行"""
        self.stop_after_reworks()
        (self.wt / "tests" / "test_x.py").write_text("# 接手的人把测试改松了\n", encoding="utf-8")
        calls = []

        def judge(*a, **k):
            calls.append(1)
            return Verdict(PASS, 0, "ok") if len(calls) == 1 else Verdict(FAIL, 1, f"after restore {len(calls)}")
        card = self.run_with(judge)
        self.assertGreaterEqual(len(calls), 2, "还原以后要再判定")
        self.assertNotEqual(card.state, "待验收")

    def test_rejudge_pass_then_same_old_failure_still_sends_developer(self):
        """新问题 1：先测过了跳过写代码的，还原后又出现停卡前那条报错——要派写代码的，不能当“原地打转”直接停"""
        self.stop_after_reworks()
        self.card.rework = 1   # 还有返工额度
        old = [e for e in read_flow(self.cdir) if e.get("kind") == "判定"][-1]
        from collab.flow import text_of
        old_text = text_of(self.cdir, old)
        calls = []

        def judge(*a, **k):
            calls.append(1)
            return Verdict(PASS, 0, "ok") if len(calls) == 1 else (
                Verdict(FAIL, 1, old_text) if len(calls) == 2 else Verdict(PASS, 0, "ok"))
        self.run_with(judge)
        self.assertGreater(len(self.devs), self.before, "要派写代码的去改")

    def test_unfinished_tester_fix_is_undone_before_rejudge(self):
        """第 3 轮第 1 条：修测试时被停下，写测试的越界改的产品文件要先还原，再重跑测试"""
        from collab import dispatch
        self.stop_after_reworks()
        guard = self.cdir / dispatch._GUARD_BACKUP_DIR / "修测试报错"
        before = {}
        for rel in dispatch._not_card_data(self.wt, self.cdir, dispatch._expand_to_files(
                self.wt, [p for p in dispatch._changed_list(self.wt) if not dispatch.is_cache(p)])):
            before[rel] = dispatch._file_digest(self.wt / rel)
        dispatch._save_guard_backup(guard, self.cdir, self.wt, dispatch._latest_review_seq(self.cdir), before,
                                    dispatch._load_test_files(self.cdir))
        kept = (self.wt / "app.py").read_text(encoding="utf-8")
        (self.wt / "app.py").write_text("A = 999  # 写测试的越界改的半成品\n", encoding="utf-8")
        seen = []

        def judge(*a, **k):
            seen.append((self.wt / "app.py").read_text(encoding="utf-8"))
            return Verdict(PASS, 0, "ok")
        self.run_with(judge)
        self.assertEqual(seen[0], kept, "重跑前先还原写测试的半成品")
        self.assertFalse(guard.exists())

    def test_rejudge_timeout_stops_without_dev(self):
        """复查第 6 条：先重跑就超时，直接停下叫人，不白派写代码的"""
        from collab.judge import TIMEOUT
        self.stop_after_reworks()
        card = self.run_with(lambda *a, **k: Verdict(TIMEOUT, None, "超时"))
        self.assertEqual(len(self.devs), self.before)
        self.assertEqual(card.state, "需Claude介入")

    def test_rejudge_test_file_errors_go_to_tester_first(self):
        """复查第 6 条：先重跑的报错全在测试文件里，先交回写测试的，不先派写代码的"""
        self.stop_after_reworks()
        out = "tests/test_x.py:9:1: error: Name \"foo\" is not defined  [name-defined]\n"
        verdicts = [Verdict(FAIL, 1, out), Verdict(FAIL, 1, out)]
        testers = []
        orig = self.ask

        def ask(adapter, prompt, cwd, **kw):
            if "报错全在测试文件自己身上" in prompt:
                testers.append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final="改好了")
            return orig(adapter, prompt, cwd, **kw)
        self.ask = ask
        self.run_with(lambda *a, **k: verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok"))
        self.assertEqual(len(testers), 1)
        self.assertEqual(len(self.devs), self.before, "测试改好后就过了，不派写代码的")

    def test_still_failing_uses_fresh_output(self):
        self.stop_after_reworks()
        self.run_with(lambda *a, **k: Verdict(FAIL, 1, "NEW-ERROR after takeover"))
        self.assertGreater(len(self.devs), self.before)
        first = self.devs[self.before]
        self.assertIn("NEW-ERROR", first, "用接着跑时新跑出的报错派活")
        self.assertNotIn("OLD-ERROR", first, "不拿停卡前的旧报错派活")


class ResumeTellsWhatHappened(ResumeBase):
    """T4 ⑧⑮：接着跑时，每个岗位这次第一次派活末尾带“前情”。"""

    def test_first_run_has_no_story(self):
        self.card = Card("CARD-001", state="待派发")
        self.prompts = []
        orig = self.ask

        def ask(adapter, prompt, cwd, **kw):
            self.prompts.append(prompt)
            return orig(adapter, prompt, cwd, **kw)
        self.ask = ask
        self.run_with(lambda *a, **k: Verdict(PASS, 0, "ok"))
        self.assertTrue(self.prompts)
        self.assertFalse(any("【前情】" in p for p in self.prompts), "第一次开跑不带前情")

    def test_resumed_dev_gets_story_once(self):
        self.stop_after_reworks()
        n = iter(range(100))
        self.run_with(lambda *a, **k: Verdict(FAIL, 1, f"NEW-ERROR {next(n)}"))
        new = self.devs[self.before:]
        self.assertGreaterEqual(len(new), 1)
        story = new[0]
        self.assertIn("【前情】", story)
        self.assertIn("停过", story)
        self.assertIn("Gemini", story, "接手人是谁")
        self.assertIn("修好了测试文件，验收命令全绿", story, "接手说明原话")
        self.assertIn("tests/test_x.py", story, "副本里已有的测试")
        self.assertIn("app.py", story, "副本里已改的文件")
        self.assertIn("汇报：改了", story, "同岗位上一棒最后的汇报")
        self.assertTrue(all("【前情】" not in p for p in new[1:]), "同一岗位这次只交代一次")

    def test_half_written_unregistered_test_listed_as_test(self):
        """复查第 8 条：写测试写到一半被停，测试文件还没登记，也要算“已有测试”"""
        from collab.dispatch import _prior_story
        self.stop_after_reworks()
        (self.wt / "tests" / "test_half.py").write_text("# 写到一半\n", encoding="utf-8")
        story = _prior_story(self.cdir, self.wt)
        self.assertIn("tests/test_half.py", story["改动"].split("副本里已改过的其他文件")[0])

    def test_revise_run_has_no_story(self):
        """复查第 8 条：做完的卡点“继续改”是新要求，不是接着中断的活，不附前情"""
        from collab.dispatch import _prior_story
        self.stop_after_reworks()
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="按钮改成蓝色")
        self.assertIsNone(_prior_story(self.cdir, self.wt))

    def test_story_cleared_even_if_cleanup_fails(self):
        """复查第 11 条：收尾出错也要清掉这张卡的前情"""
        from collab import dispatch
        self.stop_after_reworks()
        orig = dispatch._FullSlot.stop

        def boom(self_):
            raise RuntimeError("收尾写记录失败")
        dispatch._FullSlot.stop = boom
        try:
            with self.assertRaises(RuntimeError):
                self.run_with(lambda *a, **k: Verdict(PASS, 0, "ok"))
        finally:
            dispatch._FullSlot.stop = orig
        self.assertNotIn(str(self.cdir), dispatch._PRIOR)


# ---------------- 第 1 轮复查（docs/外部项目试跑修复/复查记录/第1轮.md）退回的 12 条 ----------------

class Round1Parser(unittest.TestCase):
    """复查第 4 条：认“测试文件自己的错”还要更严——混着产品错误、断言失败、产品缺导出都不转；路径带空格要认。"""

    def test_colored_product_error_mixed_in_is_not_test_only(self):
        out = TRAIN_TSC + "\x1b[96mlib/process-worksheets.ts\x1b[0m:\x1b[93m12\x1b[0m:\x1b[93m3\x1b[0m - \x1b[91merror\x1b[0m TS2322: x\n"
        self.assertEqual(_test_file_errors(out, TESTS), [])

    def test_assertion_failures_mixed_in_is_not_test_only(self):
        out = TRAIN_TSC.replace("ℹ fail 0", "ℹ fail 2")
        self.assertEqual(_test_file_errors(out, TESTS), [], "还有用例没过，可能是产品的错，交给写代码的")

    def test_unparsed_error_line_blocks(self):
        out = TRAIN_TSC + "error TS5023: Unknown compiler option 'foo'.\n"
        self.assertEqual(_test_file_errors(out, TESTS), [], "认不出位置的报错，宁可不转")

    def test_missing_export_in_product_is_not_test_error(self):
        out = "tests/process-worksheets.test.ts(3,10): error TS2305: Module '\"../lib/process-worksheets\"' has no exported member 'assignProcessTasks'.\n"
        self.assertEqual(_test_file_errors(out, TESTS), [], "产品模块缺导出是写代码的事")

    def test_path_with_spaces(self):
        out = "tests/my sheet.test.ts(5,1): error TS2304: Cannot find name 'x'.\n"
        self.assertEqual(len(_test_file_errors(out, ["tests/my sheet.test.ts"])), 1)

    def test_python_traceback_through_product_then_test_syntax_error(self):
        out = ('Traceback (most recent call last):\n  File "/p/app.py", line 3, in <module>\n    import tests.test_x\n'
               '  File "/p/tests/test_x.py", line 9\n    def broken(\n              ^\nSyntaxError: \'(\' was never closed\n')
        self.assertEqual(len(_test_file_errors(out, ["tests/test_x.py"])), 1)


class Round1Scrub(unittest.TestCase):
    """复查第 7 条：前情里不带密钥，长度有上限。"""

    def test_secrets_hidden_and_length_capped(self):
        from collab.dispatch import _scrub_secrets
        text = "汇报：配好了 OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789 和 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        got = _scrub_secrets(text)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz", got)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ", got)
        self.assertIn("汇报：配好了", got)


class Round1ResumeCondition(unittest.TestCase):
    """复查第 2、6 条：只有“最后一次判定是没过、之后没人再交过活”才先重跑；之后测过了、复查员回过话的不重跑。"""

    def test_later_pass_means_no_rejudge(self):
        from collab.dispatch import _resume_after_fail
        ev = [{"seq": 1, "kind": "判定", "result": FAIL}, {"seq": 2, "kind": "回复", "src": "开发员", "ok": True},
              {"seq": 3, "kind": "判定", "result": PASS}, {"seq": 4, "kind": "派活", "dst": "复查员"},
              {"seq": 5, "kind": "回复", "src": "复查员", "ok": True}]
        self.assertFalse(_resume_after_fail(ev))

    def test_stopped_right_after_fail_rejudges_even_if_rework_zero(self):
        from collab.dispatch import _resume_after_fail
        ev = [{"seq": 1, "kind": "回复", "src": "开发员", "ok": True}, {"seq": 2, "kind": "判定", "result": FAIL},
              {"seq": 3, "kind": "停下"}]
        self.assertTrue(_resume_after_fail(ev))

    def test_dev_replied_after_fail_no_rejudge(self):
        from collab.dispatch import _resume_after_fail
        ev = [{"seq": 1, "kind": "判定", "result": FAIL}, {"seq": 2, "kind": "派活", "dst": "开发员"},
              {"seq": 3, "kind": "回复", "src": "开发员", "ok": True}, {"seq": 4, "kind": "停下"}]
        self.assertFalse(_resume_after_fail(ev), "写代码的改完还没判定就停了：照老路（直接判定）")


# ---------------- 第 2 轮复查（复查记录/第2轮.md）：没修好的 6 条 + 新引入的 5 条 ----------------

class Round2Parser(unittest.TestCase):
    def test_python_loader_syntax_error_in_test_counts(self):
        """第 4 条：unittest 加载测试文件就语法错，是测试自己的错"""
        out = ("E\n======================================================================\n"
               "ERROR: test_x (unittest.loader._FailedTest.test_x)\n"
               "----------------------------------------------------------------------\n"
               "ImportError: Failed to import test module: test_x\nTraceback (most recent call last):\n"
               '  File "/usr/lib/python3/unittest/loader.py", line 396, in _find_test_path\n    module = self._get_module_from_name(name)\n'
               '  File "/p/tests/test_x.py", line 9\n    def broken(\n              ^\nSyntaxError: \'(\' was never closed\n\n'
               "Ran 1 test in 0.000s\n\nFAILED (errors=1)\n")
        self.assertEqual(len(_test_file_errors(out, ["tests/test_x.py"])), 1)

    def test_passing_case_named_assertion_error_does_not_block(self):
        """第 4 条：通过的用例名字里带 AssertionError 不算有用例没过"""
        out = "✔ 抛出 AssertionError 时提示用户 (3ms)\nℹ pass 1\nℹ fail 0\n" + TRAIN_TSC
        self.assertEqual(len(_test_file_errors(out, TESTS)), 3)

    def test_truncated_output_never_routes(self):
        """新问题 5：判定输出被截短（前面的产品报错可能被截掉）时不转"""
        from collab.judge import _OUTPUT_LIMIT
        out = ("x" * _OUTPUT_LIMIT + TRAIN_TSC)[-_OUTPUT_LIMIT:]
        self.assertEqual(_test_file_errors(out, TESTS), [])


class Round2Scrub(unittest.TestCase):
    def test_json_quoted_secret_hidden(self):
        """第 7 条：JSON 里带引号的密钥也要隐去"""
        from collab.dispatch import _scrub_secrets
        got = _scrub_secrets('配置是 {"api_key": "abcd1234efgh5678ijkl", "model": "x"}')
        self.assertNotIn("abcd1234efgh5678ijkl", got)
        self.assertIn('"model": "x"', got)


class Round2Resume(unittest.TestCase):
    def test_timeout_record_does_not_hide_earlier_fail(self):
        """第 6 条：先测超时记了一条，环境好了再接着跑，仍按“上次没过”先重跑"""
        from collab.dispatch import _resume_after_fail
        from collab.judge import TIMEOUT
        ev = [{"seq": 1, "kind": "判定", "result": FAIL}, {"seq": 2, "kind": "转人工"},
              {"seq": 3, "kind": "判定", "result": TIMEOUT}, {"seq": 4, "kind": "转人工"}]
        self.assertTrue(_resume_after_fail(ev))


class Round2WorkTime(unittest.TestCase):
    def test_takeover_while_dead_does_not_count(self):
        """第 9 条：进程死了没记停下，等了一阵才记“接手”：等的时间不算"""
        from desktop.view import _work_seconds
        ev = [{"kind": "开跑", "ts": 0.0}, {"kind": "派活", "ts": 360.0},
              {"kind": "接手", "ts": 3900.0}, {"kind": "开工", "ts": 3960.0}, {"kind": "开跑", "ts": 3961.0},
              {"kind": "停下", "ts": 4321.0}]
        self.assertEqual(_work_seconds(ev, running=False, now=None), 720)


# ---------------- 第 3 轮复查（复查记录/第3轮.md，最后一轮）----------------

class Round3Parser(unittest.TestCase):
    def test_python_product_import_error_mixed_in_blocks(self):
        """第 2 轮第 4 条续：测试文件语法错和产品缺导出的导入错一起出现，不转"""
        out = ('  File "/p/tests/test_x.py", line 9\n    def broken(\n              ^\nSyntaxError: \'(\' was never closed\n'
               "ImportError: cannot import name 'total' from 'app' (/p/app.py)\n")
        self.assertEqual(_test_file_errors(out, ["tests/test_x.py"]), [])

    def test_loader_wrapper_import_error_is_fine(self):
        out = ("ERROR: test_x (unittest.loader._FailedTest.test_x)\nImportError: Failed to import test module: test_x\n"
               '  File "/p/tests/test_x.py", line 9\n    def broken(\n              ^\nSyntaxError: \'(\' was never closed\n')
        self.assertEqual(len(_test_file_errors(out, ["tests/test_x.py"])), 1)


class Round3Scrub(unittest.TestCase):
    def test_db_url_password_hidden(self):
        from collab.dispatch import _scrub_secrets
        got = _scrub_secrets("连上了 postgres://admin:S3cretPassw0rd@db.example.com:5432/app")
        self.assertNotIn("S3cretPassw0rd", got)


class Round3Resume(unittest.TestCase):
    def test_stopped_on_old_tests_does_not_rejudge(self):
        """第 2 轮新增 1 续：上次停在“本卡测试过了、老测试没过”，接着跑不能用本卡测试的“过了”跳过写代码的"""
        from collab.dispatch import _resume_after_fail
        ev = [{"seq": 1, "kind": "判定", "result": FAIL, "summary": "第 4 轮：本卡测试过了，但老测试没过（test_a），改之前它们是过的"},
              {"seq": 2, "kind": "转人工"}]
        self.assertFalse(_resume_after_fail(ev))


class Round3Returns(Base):
    def test_returns_counted_since_new_requirement_in_same_run(self):
        """第 2 轮新增 2 续：跑着时确认的追加要求在同一次开跑里接着做，旧要求的交回次数不算"""
        from collab.dispatch import _test_file_returns
        record(self.cdir, "开跑", src="调度员", summary="开始施工")
        record(self.cdir, "交回测试", src="程序", summary="1")
        record(self.cdir, "交回测试", src="程序", summary="2")
        record(self.cdir, "追加要求", src="你", summary="按钮改成蓝色")
        self.assertEqual(_test_file_returns(self.cdir), 0)

    def test_product_judgment_return_does_not_use_quota(self):
        """第 2 轮新增 4 续：写测试的判断“是产品问题”的那次不占“修测试”名额"""
        from collab.dispatch import _test_file_returns
        record(self.cdir, "开跑", src="调度员", summary="开始施工")
        record(self.cdir, "交回测试", src="程序", summary="1")
        record(self.cdir, "接着走", src="程序", dst="开发员", summary="不是测试的问题", product=True)
        self.assertEqual(_test_file_returns(self.cdir), 0)


class RelativeProjectPath(unittest.TestCase):
    """㉔ train CARD-001：在项目文件夹里用 “.” 叫放进项目，程序去找 “-worktrees” 文件夹，报“改好的版本不在了”。"""

    def test_merge_with_dot_finds_the_copy(self):
        import os
        from collab.cli import main
        from collab.flow import read_flow
        from collab.workspace import init_collab
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = Path(tmp.name) / "train"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        init_collab(str(project))
        cdir = project / ".collab" / "cards" / "CARD-001"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "状态.json").write_text('{"card_id": "CARD-001", "state": "待验收", "rework": 0}', encoding="utf-8")
        (Path(tmp.name) / "train-worktrees" / "CARD-001").mkdir(parents=True)
        old = os.getcwd()
        os.chdir(project)
        try:
            import contextlib, io
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                main(["merge", ".", "CARD-001"])
        finally:
            os.chdir(old)
        missing = [e for e in read_flow(cdir) if "改好的版本不在了" in (e.get("summary") or "")]
        self.assertEqual(missing, [], "副本明明在，不能因为项目写成 “.” 就找不到")


class BracketPathsGetMerged(unittest.TestCase):
    """㉕ train CARD-001：Next.js 的 app/[id]/… 这类带方括号的文件，放进项目时没被提交（方括号被当成通配符），
    同步后文档检查判“改了代码没记文档”，合并失败。"""

    def test_exact_bracket_path_is_in_scope_for_merge(self):
        from collab.merge import _keep
        allowed = ["app/trainer/scenarios/[id]/actions.ts", "app/api/rooms/[room_id]/process-worksheets/route.ts"]
        self.assertTrue(_keep("app/trainer/scenarios/[id]/actions.ts", allowed, set()))
        self.assertTrue(_keep("app/api/rooms/[room_id]/process-worksheets/route.ts", allowed, set()))
        self.assertFalse(_keep("app/trainer/scenarios/i/actions.ts", ["app/trainer/scenarios/[id]/actions.ts"], set()),
                         "写明的是带方括号的真文件名，不能放宽成通配")

    def test_bracket_folder_prefix(self):
        from collab.scope import _matches
        self.assertTrue(_matches("app/[id]/page.tsx", "app/[id]/"))


ROOT = Path(__file__).resolve().parents[1]
TRAIN001 = ROOT / "tests" / "fixtures" / "train001"


class WorkTime(unittest.TestCase):
    """T5 ⑪：只算干活时间。train 三段：16:24–16:40、16:40–17:24、17:26–18:20，共约 1 小时 53 分。"""

    def events(self):
        return [json.loads(x) for x in (TRAIN001 / "流转.jsonl").read_text(encoding="utf-8").splitlines() if x]

    def test_train_card_three_runs(self):
        from desktop.view import _work_seconds
        got = _work_seconds(self.events(), running=False, now=None)
        self.assertAlmostEqual(got / 60, 113, delta=1)

    def test_running_counts_until_now(self):
        from desktop.view import _work_seconds
        ev = [{"kind": "开跑", "ts": 1000.0}, {"kind": "派活", "ts": 1001.0}]
        self.assertEqual(_work_seconds(ev, running=True, now=1600.0), 600)

    def test_interrupted_run_ends_at_last_record(self):
        """进程没了又没记停下（比如电脑重启）：算到最后一条记录，不算到现在"""
        from desktop.view import _work_seconds
        ev = [{"kind": "开跑", "ts": 1000.0}, {"kind": "派活", "ts": 1300.0}]
        self.assertEqual(_work_seconds(ev, running=False, now=99999.0), 300)

    def test_restart_after_dead_run_does_not_count_waiting(self):
        """复查第 9 条：进程死了没记停下，桌面重开先记“开工”再记“开跑”——等人重开的时间不算"""
        from desktop.view import _work_seconds
        ev = [{"kind": "开跑", "ts": 0.0}, {"kind": "派活", "ts": 360.0},
              {"kind": "开工", "ts": 3960.0}, {"kind": "开跑", "ts": 3961.0}, {"kind": "停下", "ts": 4321.0}]
        self.assertEqual(_work_seconds(ev, running=False, now=None), 720)

    def test_never_ran(self):
        from desktop.view import _work_seconds
        self.assertIsNone(_work_seconds([{"kind": "需求", "ts": 1.0}], running=False, now=2.0))

    def test_card_view_carries_it(self):
        from collab.workspace import init_collab
        from desktop.view import card_view
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = Path(tmp.name) / "项目"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        init_collab(str(project))
        shutil.copytree(TRAIN001, project / ".collab" / "cards" / "CARD-001")
        v = card_view(str(project), "CARD-001")
        self.assertAlmostEqual(v.get("work_seconds") / 60, 113, delta=1)

    def test_panel_shows_it(self):
        proc = subprocess.run(["node", str(ROOT / "tests" / "worktime_page_check.js")], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()


class ReviewAfterReviseIsFirstRound(unittest.TestCase):
    """㉚ train CARD-002：复查退满转人工 → 追加要求重跑，第一次复查被当成“退回后的复查”降一档、只看改动。
    退回次数、上轮核对、退不退回都按最后一条追加要求之后算，复查强度和“只看改动”也要一样。"""

    def test_first_review_after_revise_is_full_strength(self):
        from test_flowfix_c1_acceptance import Flow

        class F(Flow):
            def runTest(self):
                pass

        f = F()
        f.setUp()
        try:
            record(f.cdir, "复查结论", src="程序", summary="复查退回 2 件", result="退回")
            record(f.cdir, "转人工", src="程序", dst="调度员", summary="复查不通过")
            record(f.cdir, "追加要求", src="你", dst="调度员", summary="换个做法", text="换个做法")
            _, calls = f.run_flow(["结论：不通过\n1. 【中等】【这次改的】空表标错。", "结论：通过"])
            reviews = f.of(calls, "review")
            self.assertEqual(len(reviews), 2)
            self.assertEqual(reviews[0][2].get("codex_effort"), "xhigh", "追加要求后的第一次复查照第 1 轮")
            self.assertNotIn("只看上一次复查以后改动", reviews[0][1])
            self.assertEqual(reviews[1][2].get("codex_effort"), "high", "这次开工内退回过才降一档")
            self.assertIn("只看上一次复查以后改动", reviews[1][1])
        finally:
            f.tearDown()
