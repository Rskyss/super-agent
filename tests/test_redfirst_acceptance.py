"""测试先红卡验收测试（Claude 写；**施工方不许改**，补充测试另写 test_redfirst_extra*.py）。
依据 docs/跑卡流程调整/方案-测试先红卡.md、施工单-测试先红卡.md。

写测试的交活后，程序先跑一遍本卡测试：一跑就全过 → 退回写测试的一次；再全过 → 停下交把关的人。
“测试本身写错了”那种改测试的轮次不查。只在真实跑卡时查（run_card(red_check=True)，命令行 run 打开），
老测试用假的“判定永远通过”不受影响。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask

CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 -m unittest discover -s tests"]
PASS_REVIEW = "结论：通过"
TEST_WRONG = "结论：不通过\n1. 【中等】【这次改的】【测试的问题】测试把按钮颜色写反了。"
PRODUCT_BAD = "结论：不通过\n1. 【中等】【这次改的】按钮点了没反应。"


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
        self.collab = self.project / ".collab"
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改按钮\n", encoding="utf-8")
        self.calls = []        # [(谁, 交代)]
        self.reviews = []      # 复查员依次给的意见，用完就一直“通过”
        self.verdicts = []     # 判定依次给的结果，用完就一直“过”

    def tearDown(self):
        self.tmp.cleanup()

    def ask_fn(self, adapter, prompt, cwd, **kw):
        if adapter == "agy":
            self.calls.append(("复查", prompt))
            return AgentResult(ok=True, output="", attempts=1, final=self.reviews.pop(0) if self.reviews else PASS_REVIEW)
        if adapter == "codex":
            self.calls.append(("测试", prompt))
            n = sum(1 for who, _ in self.calls if who == "测试")
            (Path(cwd) / "tests" / f"test_new{n}.py").write_text(f"# 第 {n} 次写的测试\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="写好了测试")
        self.calls.append(("代码", prompt))
        (Path(cwd) / "app.py").write_text(f"A = {len(self.calls)}\n", encoding="utf-8")
        return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

    def judge_fn(self, *a, **k):
        result = self.verdicts.pop(0) if self.verdicts else PASS
        return Verdict(result, 0 if result == PASS else 1, "Ran 1 test\n\nOK" if result == PASS else "FAILED (failures=1)")

    def go(self, card=None, red_check=True):
        card = card or Card("CARD-001", state="待派发")
        kwargs = {"red_check": True} if red_check else {}
        return run_card(card, self.collab, self.project, CMD, "cursor", "codex", ask_fn=listed_ask(self.ask_fn),
                        judge_fn=self.judge_fn, reviewer_adapter="agy", require_tests=True, **kwargs)[0]

    def who(self):
        return [w for w, _ in self.calls]

    def checks(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "先红检查"]

    def stops(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"]


class FirstRound(Base):
    def test_red_at_once_goes_straight_to_developer(self):
        self.verdicts = [FAIL]
        card = self.go()
        self.assertEqual(self.who()[:2], ["测试", "代码"], "先红了就照常交给写代码的")
        self.assertEqual(self.who().count("测试"), 1)
        self.assertEqual(len(self.checks()), 1)
        self.assertEqual(self.checks()[0].get("result"), FAIL)
        self.assertEqual(self.checks()[0].get("step"), "写测试")
        self.assertEqual(card.state, "待验收")

    def test_green_once_sends_back_to_tester_once(self):
        self.verdicts = [PASS, FAIL]
        card = self.go()
        self.assertEqual(self.who()[:3], ["测试", "测试", "代码"])
        again = self.calls[1][1]
        self.assertIn("一跑就全过", again, "退回时说清为什么：一跑就全过，抓不到问题")
        self.assertIn("不改产品代码", again)
        self.assertEqual([c.get("result") for c in self.checks()], [PASS, FAIL])
        self.assertEqual(card.state, "待验收")
        self.assertFalse(self.stops())

    def test_green_twice_stops_for_gatekeeper(self):
        self.verdicts = [PASS, PASS]
        card = self.go()
        self.assertEqual(self.who(), ["测试", "测试"], "再全过就停下，不交给写代码的")
        self.assertEqual(card.state, "需Claude介入")
        stop = self.stops()[-1]
        self.assertEqual(stop.get("cause"), "测试先红")
        self.assertIn("全过", stop.get("summary", ""))
        self.assertEqual(len(self.checks()), 2)

    def test_resume_after_stop_goes_to_developer_without_checking_again(self):
        self.verdicts = [PASS, PASS]
        card = self.go()
        self.assertEqual(card.state, "需Claude介入")
        self.calls.clear()
        card = self.go(card=Card.load(self.cdir / "状态.json"))
        self.assertEqual(self.who()[:1], ["代码"], "把关的人看过点接着跑：直接交给写代码的")
        self.assertNotIn("测试", self.who())
        self.assertEqual(len(self.checks()), 2, "接着跑不再查")
        self.assertEqual(card.state, "待验收")

    def test_off_by_default_for_old_callers(self):
        card = self.go(red_check=False)
        self.assertEqual(self.who()[:2], ["测试", "代码"])
        self.assertEqual(self.checks(), [])
        self.assertEqual(card.state, "待验收")


class ReviewRounds(Base):
    def test_fixing_wrong_tests_is_not_checked(self):
        self.verdicts = [FAIL]          # 第一轮先红；之后判定一直过
        self.reviews = [TEST_WRONG]
        card = self.go()
        steps = [c.get("step") for c in self.checks()]
        self.assertEqual(steps, ["写测试"], "改测试那一轮不查先红")
        self.assertFalse([s for s in self.stops() if s.get("cause") == "测试先红"])
        self.assertEqual(card.state, "待验收")

    def test_added_tests_after_review_green_warns_developer_without_stopping(self):
        self.verdicts = [FAIL]
        self.reviews = [PRODUCT_BAD]
        card = self.go()
        checks = [c for c in self.checks() if c.get("step") == "补测试"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].get("result"), PASS)
        dev_after = [p for w, p in self.calls if w == "代码"][-1]
        self.assertIn("没抓住", dev_after, "补的测试一跑就过：告诉写代码的别当成已经有测试守住")
        self.assertEqual(self.who().count("测试"), 2, "补测试那一轮不重派、不停下")
        self.assertFalse([s for s in self.stops() if s.get("cause") == "测试先红"])
        self.assertEqual(card.state, "待验收")


if __name__ == "__main__":
    unittest.main()
