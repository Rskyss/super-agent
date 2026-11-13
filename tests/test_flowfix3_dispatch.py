"""流程修理第三批：返工交代收窄（㊾）、被停下但测试已过直接交复查（㊾）、停下判断和测试的问题（㊿）。

69 里 agy 改一句提示语 30 分钟没改完：退回时它收到整卡交接资料加复查全文，把整张卡又核了一遍；
被停下时测试其实已经全过，流程里却没有直接交复查的出口。
"""
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, _stop_reason, run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("## 要改的文件\n整卡资料\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def events(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind]

    def go(self, reviews, dev_results=None, verdicts=None):
        calls = {"dev": [], "tester": [], "review": []}
        reviews, dev_results = list(reviews), list(dev_results or [])
        verdicts = list(verdicts or [])
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"].append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "测试本身写错了" in prompt or "编写自动化测试" in prompt:
                calls["tester"].append(prompt)
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            if dev_results:
                return dev_results.pop(0)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        def judge(*a, **k):
            return verdicts.pop(0) if verdicts else Verdict(PASS, 0, "ok")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "agy", "codex",
                           ask_fn=ask_fn, judge_fn=judge, reviewer_adapter="codex")
        return card, calls


class NarrowReturn(Base):
    """㊾：退回时只交要改的这几条，不要重新核对，不附整卡交接资料。"""

    def test_return_prompt_is_narrow(self):
        review = ("结论：不通过\n1. 【轻微】已有卡片的积木块被移走时提示丢了。\n   原因：run 里 card 恒不为空。\n"
                  "指定测试 466 项全部通过。")
        _, calls = self.go([review, "结论：通过"])
        back = calls["dev"][1]
        self.assertIn("只改下面这 1 条", back)
        self.assertIn("不要重新核对", back)
        self.assertIn("原因：run 里 card 恒不为空", back)
        self.assertNotIn(str(self.cdir / HANDOVER), back, "退回轮不带整卡交接资料")
        self.assertNotIn("466 项", back, "复查员的验证过程不交给写代码的")
        self.assertIn(str(self.cdir / HANDOVER), calls["dev"][0], "第一次写代码照旧带交接资料")


class StoppedButDone(Base):
    """㊾：写代码的被停下，但本卡测试已经全过 → 直接交复查。"""

    def test_timeout_with_passing_tests_goes_to_review(self):
        card, calls = self.go(["结论：通过"],
                              dev_results=[AgentResult(ok=False, output="超时", attempts=1)])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["review"]), 1)
        note = [e for e in read_flow(self.cdir) if "直接交复查" in str(e.get("say") or e.get("summary") or "")]
        self.assertTrue(note, "流转里看得见、行政会说")
        self.assertFalse(self.events("转人工"))

    def test_timeout_with_failing_tests_still_stops(self):
        card, calls = self.go([], dev_results=[AgentResult(ok=False, output="超时", attempts=1)],
                              verdicts=[Verdict(FAIL, 1, "1 项没过")])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls["review"], [])

    def test_out_of_credit_is_not_stopped(self):
        """额度用完、报错不算“被停下”：它根本没干成，照旧停下说原因"""
        card, calls = self.go([], dev_results=[AgentResult(ok=False, output="Error: quota exceeded", attempts=1)])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls["review"], [])


class CursorSandbox(Base):
    """51：Cursor 沙箱建不了 .git 的测试——不绕路、说明一句，程序会完整跑（已知取舍）"""

    def test_cursor_is_told(self):
        seen = []

        def ask_fn(adapter, prompt, cwd, **kw):
            seen.append((adapter, prompt))
            if "编写自动化测试" in prompt:
                (self.worktree / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：好了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "cursor", "codex",
                 ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"))
        dev = [p for a, p in seen if a == "cursor"][0]
        tester = [p for a, p in seen if a == "codex"][0]
        self.assertIn("Operation not permitted", dev)
        self.assertNotIn("Operation not permitted", tester, "只告诉 Cursor")


class StopJudgement(Base):
    """㊿：停下时先看测试的问题；同一问题连位置一起认；退回用完时测试的问题先改一次再停。"""

    def _replies(self, *texts):
        for t in texts:
            record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过", text=t)

    def test_test_problem_first(self):
        self._replies(*["结论：不通过\n1. 【测试的问题】【中等】测试把编号写成 tavily，实际是 web-search。"] * 3)
        reason = _stop_reason(self.cdir)
        self.assertIn("测试本身写错了", reason)
        self.assertNotIn("施工单没写准", reason)

    def test_same_place_is_same_problem(self):
        self._replies("结论：不通过\n1. 编号写错也能出草稿。位置：[tasks.py:587](/x/tasks.py:587)",
                      "结论：不通过\n1. 按服务商名兜底绕过了校验。位置：[tasks.py:587](/x/tasks.py:587)",
                      "结论：不通过\n1. 不存在的积木块仍能保存。位置：[tasks.py:587](/x/tasks.py:587)")
        self.assertIn("同一个问题反复出现", _stop_reason(self.cdir))

    def test_test_problem_gets_one_more_try_when_returns_used_up(self):
        bad = "结论：不通过\n1. 【中等】按钮是红的。"
        test_bad = "结论：不通过\n1. 【测试的问题】【中等】测试把编号写错了。"
        card, calls = self.go([bad, bad, test_bad, "结论：通过"])
        self.assertEqual(card.state, "待验收", "测试的问题先交写测试的改，再让写代码的改一次")
        self.assertEqual(len(calls["tester"]), 2)
        self.assertEqual(len(self.events("转人工")), 0)


if __name__ == "__main__":
    unittest.main()


class RelatedOldTests(Base):
    """㉜：本卡测试过了，再跑“用到这次改动文件的老测试”，不过就当测试没过退回（说明是哪项老测试）。"""

    def setUp(self):
        super().setUp()
        import subprocess
        (self.worktree / "pkg").mkdir()
        (self.worktree / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.worktree / "pkg" / "money.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.worktree / "pkg" / "other.py").write_text("X = 1\n", encoding="utf-8")
        (self.worktree / "tests" / "test_old_money.py").write_text(
            "import unittest\nfrom pkg import money\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(money.add(1, 2), 3)\n", encoding="utf-8")
        (self.worktree / "tests" / "test_old_other.py").write_text(
            "import unittest\nfrom pkg.other import X\n\nclass T(unittest.TestCase):\n"
            "    def test_x(self):\n        self.assertEqual(X, 1)\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=self.worktree, check=True, capture_output=True)

    def _run(self, dev_breaks):
        from collab.judge import judge
        wt = self.worktree
        state = {"n": 0}

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_new.py").write_text("import unittest\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            state["n"] += 1
            if dev_breaks and state["n"] == 1:
                (wt / "pkg" / "money.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            else:
                (wt / "pkg" / "money.py").write_text("def add(a, b):\n    return a + b  # 改好\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree,
                           ["/bin/bash", "-c", "true"], "agy", "codex", ask_fn=ask_fn,
                           judge_fn=judge, reviewer_adapter="codex")
        return card, state["n"]

    def test_breaking_old_feature_is_sent_back(self):
        card, rounds = self._run(dev_breaks=True)
        verdicts = [e["summary"] for e in self.events("判定")]
        self.assertTrue(any("老测试" in v and "test_old_money" in v for v in verdicts), verdicts)
        self.assertEqual(rounds, 2, "退回写代码的再改一次")
        self.assertEqual(card.state, "待验收")

    def test_only_related_old_tests_run(self):
        card, rounds = self._run(dev_breaks=False)
        self.assertEqual(rounds, 1)
        olds = [e for e in read_flow(self.cdir) if e.get("kind") == "老测试"]
        self.assertTrue(olds)
        self.assertIn("test_old_money", olds[-1]["summary"])
        self.assertNotIn("test_old_other", olds[-1]["summary"], "没用到改动文件的老测试不跑")
