"""补测测试先红的中断续跑、判定边界和命令行开关。"""
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import acceptance_helpers
import test_redfirst_acceptance as acceptance
from collab.agents import AgentResult
from collab.cli import main
from collab.dispatch import run_card
from collab.flow import read_flow, record, working_now
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT
from collab.phase import card_phase
from collab.state import Card
from review_fmt import listed_ask


class InterruptedRun(Exception):
    """模拟派活后、收到回复前被停下。"""


def check_results(cdir):
    return [(e["step"], e["result"]) for e in read_flow(cdir) if e["kind"] == "先红检查"]


class RedCheckDispatch(acceptance.Base):
    def setUp(self):
        super().setUp()
        self.interrupt_step = None
        self.failed_step = None
        self.judged = []

    def ask_fn(self, adapter, prompt, cwd, **kwargs):
        if adapter == "codex":
            step = working_now(read_flow(self.cdir))["step"]
            if step == self.interrupt_step:
                raise InterruptedRun(step)
            if step == self.failed_step:
                return AgentResult(ok=False, output="测试员没有交活", attempts=1)
        return super().ask_fn(adapter, prompt, cwd, **kwargs)

    def judge_fn(self, command, cwd, timeout):
        self.judged.append((command, Path(cwd), timeout))
        return super().judge_fn(command, cwd=cwd, timeout=timeout)

    def resume(self):
        return self.go(card=Card.load(self.cdir / "状态.json"))

    def test_resume_checks_successful_first_reply_if_check_was_interrupted(self):
        with patch.object(self, "judge_fn", side_effect=InterruptedRun):
            with self.assertRaises(InterruptedRun):
                self.go()
        self.assertEqual(self.who(), ["测试"], "先红检查停下时，写代码的人还没接活")
        self.assertEqual(self.checks(), [])

        self.verdicts = [FAIL]
        card = self.resume()
        self.assertEqual(check_results(self.cdir), [("写测试", FAIL)])
        self.assertEqual(self.who(), ["测试", "代码", "复查"], "已经交活的写测试不用重派")
        self.assertEqual(card.state, "待验收")

    def test_resume_reissues_interrupted_rewrite_without_repeating_first_check(self):
        self.interrupt_step = "重写测试"
        with self.assertRaises(InterruptedRun):
            self.go()
        self.assertEqual(check_results(self.cdir), [("写测试", PASS)])
        self.assertEqual(self.who(), ["测试"])
        self.assertEqual(working_now(read_flow(self.cdir))["step"], "重写测试")

        self.interrupt_step = None
        self.verdicts = [FAIL]
        card = self.resume()
        self.assertEqual(self.who(), ["测试", "测试", "代码", "复查"])
        self.assertEqual(check_results(self.cdir), [("写测试", PASS), ("重写测试", FAIL)])
        rewrite = self.calls[1][1]
        self.assertIn(acceptance.CMD[-1], rewrite)
        self.assertIn("一跑就全过", rewrite)
        self.assertIn("不改产品代码", rewrite)
        self.assertIn("汇报", rewrite)
        self.assertRegex(rewrite, r"现有代码[^。]*(?:已经|做到)")
        self.assertEqual(card.state, "待验收")

    def test_failed_rewrite_stops_as_worker_failure_without_checking_again(self):
        self.failed_step = "重写测试"
        card = self.go()
        self.assertEqual(check_results(self.cdir), [("写测试", PASS)])
        self.assertEqual(self.who(), ["测试"])
        self.assertEqual(card.state, "需Claude介入")
        stop, = self.stops()
        self.assertNotEqual(stop.get("cause"), "测试先红")
        self.assertIn("测试员", stop["summary"])
        self.assertIn("没有完成", stop["summary"])

    def test_revision_starts_fresh_even_after_two_previous_green_checks(self):
        self.assertEqual(self.go().state, "需Claude介入")
        revision = record(self.cdir, "追加要求", src="你", text="按钮还要支持键盘操作。")
        self.verdicts = [PASS, FAIL]
        self.calls.clear()

        card = self.resume()
        new_checks = [(e["step"], e["result"]) for e in self.checks() if e["seq"] > revision["seq"]]
        self.assertEqual(new_checks, [("写测试", PASS), ("重写测试", FAIL)])
        self.assertEqual(self.who(), ["测试", "测试", "代码", "复查"])
        self.assertIn("按钮还要支持键盘操作", self.calls[0][1])
        self.assertEqual(card.state, "待验收")

    def assert_unrunnable_check_allows_development(self, result):
        self.verdicts = [result]
        card = self.go()
        self.assertEqual(self.who(), ["测试", "代码", "复查"])
        check, = self.checks()
        self.assertEqual((check["src"], check["step"], check["result"]), ("程序", "写测试", result))
        self.assertIn("没跑成", check["summary"])
        self.assertIn(result, check["summary"])
        self.assertEqual(len(self.judged), 2, "先红检查和改完代码后的判定各跑一次")
        self.assertEqual(self.judged[0], self.judged[1], "用同一个命令、副本和时限")
        self.assertEqual(self.judged[0][:2], (acceptance.CMD, self.project))
        self.assertEqual(card.state, "待验收")

    def test_environment_error_is_recorded_then_development_proceeds(self):
        self.assert_unrunnable_check_allows_development(ENV_ERROR)

    def test_timeout_is_recorded_then_development_proceeds(self):
        self.assert_unrunnable_check_allows_development(TIMEOUT)

    def test_no_card_test_command_skips_red_check(self):
        card, _ = run_card(
            Card(self.cdir.name, state="待派发"), self.collab, self.project, [], "cursor", "codex",
            ask_fn=listed_ask(self.ask_fn), judge_fn=self.judge_fn, require_tests=True, red_check=True,
        )
        self.assertEqual(self.who(), ["测试", "代码"])
        self.assertEqual(self.checks(), [])
        self.assertEqual(len(self.judged), 1, "只有写代码之后的判定")
        self.assertEqual(card.state, "待验收")

    def test_resume_checks_added_tests_and_warns_developer_if_green(self):
        self.reviews = [acceptance.PRODUCT_BAD]
        self.verdicts = [FAIL, PASS]
        self.interrupt_step = "补测试"
        with self.assertRaises(InterruptedRun):
            self.go()
        self.assertEqual(check_results(self.cdir), [("写测试", FAIL)])

        self.interrupt_step = None
        card = self.resume()
        self.assertEqual(check_results(self.cdir), [("写测试", FAIL), ("补测试", PASS)])
        self.assertEqual(self.who(), ["测试", "代码", "复查", "测试", "代码", "复查"])
        dev_prompt = [prompt for role, prompt in self.calls if role == "代码"][-1]
        self.assertIn("没抓住", dev_prompt)
        self.assertIn("改完在汇报里写明怎么确认", dev_prompt)
        self.assertFalse(self.stops())
        self.assertEqual(card.state, "待验收")

    def test_failed_supplement_is_not_checked(self):
        self.reviews = [acceptance.PRODUCT_BAD]
        self.verdicts = [FAIL]
        self.failed_step = "补测试"

        card = self.go()
        self.assertEqual(check_results(self.cdir), [("写测试", FAIL)])
        dev_prompt = [prompt for role, prompt in self.calls if role == "代码"][-1]
        self.assertIn("补测试没成功", dev_prompt)
        self.assertEqual(card.state, "待验收")

    def test_red_supplement_keeps_the_usual_developer_instruction(self):
        self.reviews = [acceptance.PRODUCT_BAD]
        self.verdicts = [FAIL, PASS, FAIL]

        card = self.go()
        self.assertEqual(check_results(self.cdir), [("写测试", FAIL), ("补测试", FAIL)])
        dev_prompt = [prompt for role, prompt in self.calls if role == "代码"][-1]
        self.assertIn("已经补了测试", dev_prompt)
        self.assertNotIn("没抓住", dev_prompt)
        self.assertEqual(card.state, "待验收")


class RedCheckCli(acceptance_helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="待派发", plan=True)
        self.args = ["run", self.project, self.cdir.name, "--test-command", "true",
                     "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none"]

    def test_real_command_enables_check_and_shows_existing_blocked_actions(self):
        os.environ.pop("SUPERAGENT_NO_RED_CHECK", None)
        code, out = self.cli(*self.args)
        self.assertEqual(check_results(self.cdir), [("写测试", PASS), ("重写测试", PASS)], out)
        self.assertEqual(code, 2, out)
        phase = card_phase(self.project, self.cdir.name)
        self.assertEqual(phase["label"], "卡住了")
        self.assertIn("全过", phase["detail"])
        self.assertCountEqual(phase["actions"], ["resume", "revise", "discard"])

    def test_environment_switch_disables_check_for_real_command(self):
        os.environ["SUPERAGENT_NO_RED_CHECK"] = "1"
        code, out = self.cli(*self.args)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state(), "待验收")
        self.assertEqual(check_results(self.cdir), [])

    def test_injected_worker_does_not_enable_check(self):
        os.environ.pop("SUPERAGENT_NO_RED_CHECK", None)

        def fake_ask(adapter, prompt, cwd, **kwargs):
            if adapter == "codex":
                tests = Path(cwd) / "tests"
                tests.mkdir(exist_ok=True)
                (tests / "test_fake.py").write_text("# 假测试\n", encoding="utf-8")
            return AgentResult(ok=True, output="写好了", attempts=1)

        output = io.StringIO()
        with patch("collab.cli.signal.signal"), redirect_stdout(output):
            code = main(self.args, ask_fn=fake_ask)
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(self.state(), "待验收")
        self.assertEqual(check_results(self.cdir), [])
