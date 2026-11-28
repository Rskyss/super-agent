"""v0.96 D：模型不回话先自己重试；每次停下写明是哪一类（docs/跑卡不停半路/方案.md 第 7、8 条）。

D1：派活时暂时性失败（没回话被看门人停下、超时、限流、服务忙）→ 同一个模型再试 1 次；写代码的还不行就换备份
    （团队设置“备份写代码的”，没配是 Codex）再试 1 次。额度用完、你点了停下不重试。只在真跑卡时开（run_card(retry=True)，
    命令行 run 打开），老测试的假派活不受影响。以前人工处理时也多是直接点“接着跑”（开发助手卡 4、超级玩家卡 3）或换个模型（卡 10）。
D2：转人工记录标“类别”：要你定 / 程序没判出来 / 模型挂了；卡片标题下第一句、行政答进度都带上。老记录没有类别照旧显示。
"""
import json
import re
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask
from test_external_trial_fixes import CMD, Base

ROOT = Path(__file__).resolve().parents[1]
QUIET = "看门人：超过 15 分钟没有任何文件变化，已停止"


class Retry(Base):
    def go(self, dev_results, tester_results=None, retry=True, green_before_dev=False, reviews=None,
           green_after_dev_calls=None):
        self.calls = []
        dev_results, tester_results = list(dev_results), list(tester_results or [])
        reviews = list(reviews or [])
        done = {"dev": green_before_dev}

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                self.calls.append(("复查", adapter))
                return reviews.pop(0) if reviews else AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                self.calls.append(("测试员", adapter))
                if tester_results:
                    r = tester_results.pop(0)
                    if r is not None:
                        return r
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            self.calls.append(("开发员", adapter))
            r = dev_results.pop(0) if dev_results else None
            r = r or AgentResult(ok=True, output="", attempts=1, final="汇报：改好了")
            done["dev"] = done["dev"] or r.ok or (green_after_dev_calls is not None
                                                  and len(self.dev_calls()) >= green_after_dev_calls)
            return r

        def judge(*a, **k):   # 写代码的真交了活之前，本卡测试不过
            return Verdict(PASS, 0, "ok") if done["dev"] else Verdict(FAIL, 1, "AssertionError: x")

        kw = {"retry": True} if retry else {}
        return run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "agy",
                        ask_fn=listed_ask(ask_fn), judge_fn=judge,
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"], **kw)[0]

    def dev_calls(self):
        return [a for who, a in self.calls if who == "开发员"]

    def retries(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "重试"]

    def test_quiet_dev_retried_once_with_same_model(self):
        card = self.go([AgentResult(ok=False, output=QUIET, attempts=1)])
        self.assertEqual(self.dev_calls(), ["cursor", "cursor"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.retries()), 1)
        self.assertIn("再试一次", self.retries()[0]["summary"])

    def test_dev_failing_twice_switches_to_backup_once(self):
        bad = AgentResult(ok=False, output="超时", attempts=1)
        card = self.go([bad, bad])
        self.assertEqual(self.dev_calls(), ["cursor", "cursor", "codex"], "同一个再试一次，还不行换备份 Codex")
        self.assertEqual(card.state, "待验收")
        self.assertIn("备份", self.retries()[-1]["summary"])

    def test_all_three_fail_stops_as_model_down(self):
        bad = AgentResult(ok=False, output="API error: UNAVAILABLE (code 503): No capacity available", attempts=1)
        card = self.go([bad, bad, bad])
        self.assertEqual(len(self.dev_calls()), 3)
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertEqual(stop.get("类别"), "模型挂了")

    def test_stopped_but_tests_green_goes_to_review_without_retry(self):
        """第三批 ㊾ 优先：写代码的被停下、本卡测试已经全过，直接交复查，不再派人动代码（复查 D 中等 1）。"""
        card = self.go([AgentResult(ok=False, output=QUIET, attempts=1)], green_before_dev=True)
        self.assertEqual(self.dev_calls(), ["cursor"])
        self.assertEqual(card.state, "待验收")
        self.assertFalse(self.retries())

    def test_second_stop_with_green_tests_does_not_switch_to_backup(self):
        """复查 D 第 2 轮中等 1：第二次也被停下、但这次代码已经改好（本卡测试全过），直接交复查，不再换备份。"""
        card = self.go([AgentResult(ok=False, output=QUIET, attempts=1)] * 2, green_after_dev_calls=2)
        self.assertEqual(self.dev_calls(), ["cursor", "cursor"])
        self.assertEqual(card.state, "待验收")

    def test_tool_already_reran_inside_skips_same_model_retry(self):
        """复查 D 中等 2：工具自己已经重跑过一次（attempts=2），不再叠同一个模型，直接换备份。"""
        bad = AgentResult(ok=False, output="API error: UNAVAILABLE (code 503)", attempts=2)
        card = self.go([bad])
        self.assertEqual(self.dev_calls(), ["cursor", "codex"])
        self.assertEqual(card.state, "待验收")

    def test_backup_failure_is_reported_under_backup(self):
        """复查 D 中等 5：换了备份还失败，停卡说明写的是备份。"""
        bad = AgentResult(ok=False, output="超时", attempts=1)
        card = self.go([bad, bad, AgentResult(ok=False, output="429 Too Many Requests", attempts=1)])
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertIn("codex", stop.get("summary", "") + stop.get("say", ""))

    def test_reviewer_reply_without_conclusion_is_not_retried(self):
        """复查 D 第 2 轮严重 1：复查员正常交回、只是没写结论的不重派——它交回的可能是程序读得懂的“不通过”清单，
        重派会被下一次的“通过”盖掉。照旧停下。"""
        card = self.go([], reviews=[AgentResult(ok=True, output="", attempts=1, final="我已经看过改动了。")])
        self.assertEqual([a for who, a in self.calls if who == "复查"], ["codex"])
        self.assertEqual(card.state, "需Claude介入")

    def test_reviewer_delivering_nothing_is_retried(self):
        card = self.go([], reviews=[AgentResult(ok=False, output="", attempts=1)])
        self.assertEqual([a for who, a in self.calls if who == "复查"], ["codex", "codex"])
        self.assertEqual(card.state, "待验收")

    def test_out_of_credit_is_not_retried(self):
        card = self.go([AgentResult(ok=False, output="Error: insufficient_quota", attempts=1)])
        self.assertEqual(self.dev_calls(), ["cursor"], "额度用完重试也没用")
        self.assertEqual(card.state, "需Claude介入")

    def test_user_stop_is_not_retried(self):
        card = self.go([AgentResult(ok=False, output="已停工", attempts=1)])
        self.assertEqual(self.dev_calls(), ["cursor"])

    def test_tester_retried_with_same_model_only(self):
        bad = AgentResult(ok=False, output=QUIET, attempts=1)
        card = self.go([], tester_results=[bad, bad])
        testers = [a for who, a in self.calls if who == "测试员"]
        self.assertEqual(testers, ["agy", "agy"], "写测试的没有备份，只重试同一个")
        self.assertEqual(card.state, "需Claude介入")

    def test_off_by_default_for_old_callers(self):
        self.go([AgentResult(ok=False, output=QUIET, attempts=1)], retry=False)
        self.assertEqual(self.dev_calls(), ["cursor"])

    def test_cli_turns_it_on_for_real_runs(self):
        src = (ROOT / "collab" / "cli.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"retry=ask_fn is None")


class Kinds(unittest.TestCase):
    KINDS = {"要你定", "程序没判出来", "模型挂了"}

    def test_every_stop_in_dispatch_has_a_kind(self):
        src = (ROOT / "collab" / "dispatch.py").read_text(encoding="utf-8")
        calls = [m.start() for m in re.finditer(r"(?<!def )_escalate\(\s*\n?\s*collab", src)]
        direct = [m.start() for m in re.finditer(r'record\(cdir, "转人工"', src)]
        self.assertTrue(calls and direct)
        for pos in calls + direct:
            chunk = src[pos:src.index("\n\n", pos) if "\n\n" in src[pos:] else len(src)]
            depth, end = 0, None
            for i, ch in enumerate(chunk):
                depth += ch == "("
                depth -= ch == ")"
                if depth == 0 and ch == ")":
                    end = i
                    break
            call = chunk[:end]
            line = src[:pos].count("\n") + 1
            self.assertRegex(call, r"类别=", f"dispatch.py 第 {line} 行转人工没写类别")

    def _phase(self, event):
        import tempfile
        from collab.phase import card_phase
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cdir = Path(tmp.name) / ".collab" / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        Card("CARD-001", state="需Claude介入").save(cdir / "状态.json")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
        record(cdir, "转人工", src="程序", dst="调度员", **event)
        return card_phase(tmp.name, "CARD-001")

    def test_card_detail_starts_with_kind(self):
        ph = self._phase({"summary": "写代码的停下等拍板：要改范围外", "类别": "要你定"})
        self.assertTrue(ph["detail"].startswith("【要你定】"), ph["detail"])

    def test_old_stop_without_kind_unchanged(self):
        ph = self._phase({"summary": "返工 3 次后测试仍没过，需要你看看"})
        self.assertEqual(ph["detail"], "返工 3 次后测试仍没过，需要你看看")

    def test_clerk_progress_answer_has_kind(self):
        src = (ROOT / "desktop" / "clerk" / "tools.py").read_text(encoding="utf-8")
        self.assertIn("类别", src, "行政答进度时带上类别")


if __name__ == "__main__":
    unittest.main()
