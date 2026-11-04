import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict
from collab.state import Card
from collab.workspace import init_collab


def _ok(**_):
    return AgentResult(ok=True, output="", attempts=1)


class _Script:
    """按顺序返回判定结果；记录每次调用的提示词，便于断言交接内容。"""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.prompts = []

    def ask(self, adapter, prompt, cwd, **kwargs):
        self.prompts.append((adapter, prompt))
        return AgentResult(ok=True, output="", attempts=1)

    def judge(self, command, cwd, timeout):
        result = self.verdicts.pop(0)
        self.count = getattr(self, "count", 0) + 1
        return Verdict(result=result, returncode=0 if result == PASS else 1,
                       output=f"输出-{result}-第{self.count}次")


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.collab = init_collab(self.tmp.name)
        self.card_dir = self.collab / "cards" / "CARD-001"
        self.card_dir.mkdir(parents=True)
        (self.card_dir / "需求卡.md").write_text("做一个函数")
        self.worktree = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script, dev_ask=None):
        card = Card("CARD-001", state="待派发")
        return card, run_card(
            card, collab_dir=self.collab, worktree=self.worktree,
            test_command=["fake-test"], dev_adapter="cursor", test_adapter="codex",
            ask_fn=dev_ask or script.ask, judge_fn=script.judge,
        )

    def test_tester_writes_tests_before_first_judgement(self):
        script = _Script([PASS])
        self._run(script)
        adapters = [a for a, _ in script.prompts]
        # 顺序：先写测试（codex），再开发（cursor）——测试先行
        self.assertEqual(adapters[:2], ["codex", "cursor"])

    def test_tester_failure_escalates_without_judging(self):
        script = _Script([])
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "codex":
                return AgentResult(ok=False, output="", attempts=2)
            return AgentResult(ok=True, output="", attempts=1)
        card, _ = self._run(script, dev_ask=ask)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(script.verdicts, [])

    def test_every_round_test_output_is_kept(self):
        script = _Script([FAIL, FAIL, PASS])
        self._run(script)
        text = (self.card_dir / "测试结果.md").read_text(encoding="utf-8")
        for round_no in (1, 2, 3):
            self.assertIn(f"第 {round_no} 轮", text)
        self.assertIn(f"输出-{FAIL}", text)  # 第一次失败的输出没有被覆盖
        self.assertIn(f"输出-{PASS}", text)

    def test_execution_log_records_who_did_each_step(self):
        script = _Script([PASS])
        self._run(script)
        log = (self.card_dir / "执行记录.md").read_text(encoding="utf-8")
        self.assertIn("cursor", log)
        self.assertIn("codex", log)
        self.assertIn("开发", log)
        self.assertIn("写测试", log)

    def test_pass_first_time_goes_to_acceptance(self):
        script = _Script([PASS])
        card, _ = self._run(script)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 0)
        self.assertTrue((self.card_dir / "测试结果.md").exists())

    def test_fail_then_pass_records_rework_and_passes_test_output_to_dev(self):
        script = _Script([FAIL, PASS])
        card, _ = self._run(script)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        dev_prompts = [p for a, p in script.prompts if a == "cursor"]
        self.assertEqual(len(dev_prompts), 2)
        self.assertIn(f"输出-{FAIL}", dev_prompts[1])  # 测试结果原文交回开发
        self.assertIn("第 1 次返工", (self.card_dir / "返工记录.md").read_text())

    def test_four_failures_escalate_to_claude(self):
        script = _Script([FAIL, FAIL, FAIL, FAIL])
        card, _ = self._run(script)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(card.rework, 3)

    def test_exhausted_rework_is_logged_as_escalation_not_another_rework(self):
        script = _Script([FAIL, FAIL, FAIL, FAIL])
        self._run(script)
        log = (self.card_dir / "返工记录.md").read_text(encoding="utf-8")
        self.assertEqual(log.count("第 3 次返工"), 1)  # 第 4 次失败不是第 4 次返工
        self.assertIn("转需Claude介入", log)

    def test_same_error_twice_in_a_row_escalates_early(self):
        """同样的报错连续出现两次，判定为原地打转，立刻转人工，不再白白返工。"""
        outputs = iter(["同一个报错", "同一个报错", "不该走到这里"])
        verdicts_seen = []

        def judge(command, cwd, timeout):
            verdicts_seen.append(1)
            return Verdict(result=FAIL, returncode=1, output=next(outputs))

        script = _Script([])
        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.worktree,
                           test_command=["fake-test"], dev_adapter="cursor",
                           test_adapter="codex", ask_fn=script.ask, judge_fn=judge)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(verdicts_seen), 2)  # 第二次同样的报错就停
        log = (self.card_dir / "返工记录.md").read_text(encoding="utf-8")
        self.assertIn("原地打转", log)

    def test_env_error_escalates_without_counting_rework(self):
        script = _Script([ENV_ERROR])
        card, _ = self._run(script)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(card.rework, 0)

    def test_timeout_escalates(self):
        script = _Script([TIMEOUT])
        card, _ = self._run(script)
        self.assertEqual(card.state, "需Claude介入")

    def test_dev_failure_escalates_and_never_tests(self):
        script = _Script([])
        failing_dev = lambda **_: AgentResult(ok=False, output="", attempts=2)
        card, _ = self._run(script, dev_ask=lambda adapter, prompt, cwd, **kw: failing_dev())
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(script.verdicts, [])  # 没有调用过判定

    def test_state_is_persisted_after_run(self):
        script = _Script([PASS])
        self._run(script)
        loaded = Card.load(self.card_dir / "状态.json")
        self.assertEqual(loaded.state, "待验收")


if __name__ == "__main__":
    unittest.main()


class ScopeAndPageCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.collab = init_collab(self.tmp.name)
        self.card_dir = self.collab / "cards" / "CARD-001"
        self.card_dir.mkdir(parents=True)
        (self.card_dir / "需求卡.md").write_text("做一个函数", encoding="utf-8")
        self.worktree = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_out_of_scope_change_is_reverted_by_program_and_testing_continues(self):
        """开发员在自动模式下不能执行命令、删不掉文件，所以越界改动由程序直接撤销，不算返工。"""
        script = _Script([PASS])
        changes = iter([["README.md", "src/a.ts"], ["src/a.ts"]])  # 撤销前有越界，撤销后只剩范围内
        reverted = []
        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.worktree,
                           test_command=["fake-test"], dev_adapter="cursor", test_adapter="codex",
                           ask_fn=script.ask, judge_fn=script.judge,
                           allowed=["src/*"], changed_fn=lambda w: next(changes),
                           revert_fn=lambda w, paths: reverted.extend(paths))
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 0)  # 不算返工
        self.assertEqual(reverted, ["README.md"])
        log = (self.card_dir / "返工记录.md").read_text(encoding="utf-8")
        self.assertIn("已自动撤销范围外的改动：README.md", log)
        self.assertEqual(len([a for a, _ in script.prompts if a == "cursor"]), 1)  # 没有再派一次开发

    def test_revert_that_does_not_stick_escalates(self):
        script = _Script([])
        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.worktree,
                           test_command=["fake-test"], dev_adapter="cursor", test_adapter="codex",
                           ask_fn=script.ask, judge_fn=script.judge,
                           allowed=["src/*"], changed_fn=lambda w: ["README.md"],
                           revert_fn=lambda w, paths: None)  # 撤销没生效
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(script.verdicts, [])
        self.assertIn("撤销失败", (self.card_dir / "返工记录.md").read_text(encoding="utf-8"))

    def test_page_check_failure_is_a_rework(self):
        script = _Script([PASS, PASS])  # 第一个 PASS 是测试，第二个 PASS 是页面检查
        results = iter([PASS, FAIL, PASS, PASS])  # 第 1 轮页面检查没过，第 2 轮通过
        judged = []

        def judge(command, cwd, timeout):
            r = next(results)
            judged.append(command)
            return Verdict(r, 0 if r == PASS else 1, f"输出-{r}-{len(judged)}")

        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, collab_dir=self.collab, worktree=self.worktree,
                           test_command=["fake-test"], dev_adapter="cursor", test_adapter="codex",
                           ask_fn=script.ask, judge_fn=judge,
                           page_command=["fake-page"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        self.assertIn(["fake-page"], judged)
        self.assertTrue((self.card_dir / "页面检查结果.md").exists())


if __name__ == "__main__":
    unittest.main()
