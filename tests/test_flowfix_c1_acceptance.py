"""跑卡流程调整第二批 · 卡 C1（复查规矩）验收测试，Claude 先写（施工前应为红）。

口径见 docs/跑卡流程调整/需求共识-第二批.md A1–A6、方案-第二批.md：
A1 这次改出来的问题不分轻重一律退回；只有开工前就有的（严重的除外）记进“以后再修”；每一轮复查都要标这两种
A2 开工前就有的不在点确认时弹给用户；“等你确认”的说明改成“已记进台账”
A3 复查最终放行，右栏复查格是绿勾“通过”
A4 复查退回（不是测试本身写错）时，先派写测试的补测试守住，再派写代码的，交代里要求同类问题一起查
A5 退回后的复查只看改动、强度比第 1 轮低一档
A6 老测试被改坏时，交给写代码的只有失败测试名和关键几行
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, _triage, run_card
from collab.flow import read_flow
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from test_flowfix4_converge import Base

ROOT = Path(__file__).resolve().parents[1]
OLD_ONLY = "结论：不通过\n1. 【中等】【原来就有的】切换对话时卡在正在取回。"
MINOR_NOW = "结论：不通过\n1. 【轻微】【这次改的】提示字太小。"


class A1Triage(unittest.TestCase):
    def test_this_time_minor_goes_back(self):
        items = ["【轻微】【这次改的】字小了。", "【轻微】没标是哪种。", "【中等】【原来就有的】切换对话时卡住。",
                 "【轻微】【原来就有的】旧提示有错字。", "【严重】【原来就有的】重启后结果没了。"]
        back, later = _triage(items)
        self.assertEqual(back, [items[0], items[1], items[4]], "这次改的、拿不准的、严重的都退回")
        self.assertEqual(later, [items[2], items[3]], "只有开工前就有的（非严重）记进以后再修")


class Flow(Base):
    """沿用第四轮收口测试的假跑卡：ask_fn 按交代内容分辨是谁在干活。"""

    def run_flow(self, reviews, judge=None, models=None):
        reviews = list(reviews)
        calls = []   # 按顺序记 (谁, 交代, 模型设置)
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls.append(("review", prompt, kw.get("models") or {}))
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt or "测试员" in prompt[:40] or "补一条测试" in prompt or "补测试" in prompt:
                calls.append(("test", prompt, kw.get("models") or {}))
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="汇报：写了测试")
            calls.append(("dev", prompt, kw.get("models") or {}))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        role = {"复查员": dict(models or {"codex": "gpt-6.1-sol", "codex_effort": "xhigh"})}
        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=judge or (lambda *a, **k: Verdict(PASS, 0, "ok")),
                           reviewer_adapter="codex", role_models=role)
        return card, calls

    @staticmethod
    def of(calls, who):
        return [c for c in calls if c[0] == who]


class A1Flow(Flow):
    def test_first_review_asks_for_tags(self):
        _, calls = self.run_flow(["结论：通过"])
        self.assertIn("【原来就有的】", self.of(calls, "review")[0][1], "第 1 轮复查也要标这次改的/原来就有的")

    def test_this_time_minor_is_returned(self):
        card, calls = self.run_flow([MINOR_NOW, "结论：通过"])
        devs = self.of(calls, "dev")
        self.assertEqual(len(devs), 2, "这次改出来的轻微问题要退回写代码的")
        self.assertIn("提示字太小", devs[1][1])
        self.assertFalse([e for e in read_flow(self.cdir) if e.get("kind") == "以后再修"])

    def test_old_only_passes_on_first_review(self):
        card, calls = self.run_flow([OLD_ONLY])
        self.assertEqual(card.state, "待验收", "只有开工前就有的问题，第 1 轮就放行")
        self.assertEqual(len(self.of(calls, "dev")), 1)
        self.assertIn("切换对话", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))


class A2NoPopup(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_accept_has_no_later_popup(self):
        script = ("const P=require('./desktop/static/panel.js');"
                  "if(typeof P.acceptConfirm==='function'&&P.acceptConfirm(['切换对话时卡住'])!==null)process.exit(1);")
        done = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, "点确认不再弹“以后再修”清单")
        src = (ROOT / "desktop/static/panel.js").read_text(encoding="utf-8")
        self.assertNotIn("acceptConfirm(", src.split("if (typeof module")[1] if "if (typeof module" in src else "",
                         "点确认时不再调用以后再修弹窗")

    def test_phase_says_ledger_not_popup(self):
        from collab.flow import record
        from collab.phase import card_phase
        from collab.workspace import init_collab
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "proj"
            project.mkdir()
            collab = init_collab(project)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            Card("CARD-001", state="待验收").save(cdir / "状态.json")
            record(cdir, "以后再修", src="程序", items=["切换对话时卡住"], summary="记进以后再修 1 件")
            record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
            ph = card_phase(str(project), "CARD-001")
            self.assertIn("开工前就有", ph["detail"])
            self.assertIn("台账", ph["detail"])
            self.assertNotIn("点确认时会列给你看", ph["detail"])


class A3GreenTick(Flow):
    def test_closed_review_station_is_pass(self):
        from desktop.view import card_view
        card, _ = self.run_flow([MINOR_NOW, OLD_ONLY])
        self.assertEqual(card.state, "待验收")
        view = card_view(str(self.collab.parent), "CARD-001")
        rev = next(s for s in view["stations"] if s.get("key") == "rev")
        self.assertEqual(rev.get("state"), "done", "复查放行要显示绿勾")
        self.assertIn("通过", rev.get("detail", ""))
        self.assertNotIn("不通过", rev.get("detail", ""))


class A4TestFirst(Flow):
    def test_tester_guards_before_dev_fixes(self):
        r1 = "结论：不通过\n1. 【中等】【这次改的】断网时页面一直重问。"
        _, calls = self.run_flow([r1, "结论：通过"])
        seq = [c[0] for c in calls]
        first_review = seq.index("review")
        after = seq[first_review + 1:]
        self.assertIn("test", after, "退回时先派写测试的补测试")
        self.assertLess(after.index("test"), after.index("dev"), "写测试的在写代码的前面")
        guard = [c for c in calls[first_review + 1:] if c[0] == "test"][0][1]
        self.assertIn("断网时页面一直重问", guard)
        fix = [c for c in calls[first_review + 1:] if c[0] == "dev"][0][1]
        self.assertIn("同类", fix, "交代里要求同类问题一起查")


class A5LighterReReview(Flow):
    def test_second_review_only_changes_and_lower_effort(self):
        r1 = "结论：不通过\n1. 【中等】【这次改的】断网时页面一直重问。"
        _, calls = self.run_flow([r1, "结论：通过"])
        reviews = self.of(calls, "review")
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0][2].get("codex_effort"), "xhigh", "第 1 轮照旧")
        self.assertEqual(reviews[1][2].get("codex_effort"), "high", "退回后的复查降一档")
        self.assertIn("只看", reviews[1][1])
        self.assertNotIn("只看这次退回", reviews[0][1])


class A6KeyLinesOnly(Flow):
    def test_failed_tests_send_only_names_and_key_lines(self):
        noise = "\n".join(f"无关的网络报错第 {i} 行" for i in range(400))
        out = (f"{noise}\n" + "=" * 70 + "\nFAIL: test_old_feature (tests.test_old.Old)\n" + "-" * 70 +
               "\nTraceback (most recent call last):\n  File \"tests/test_old.py\", line 12, in test_old_feature\n"
               "    self.assertEqual(a, 'b')\nAssertionError: 'a' != 'b'\n\n" + "-" * 70 +
               "\nRan 30 tests in 1.2s\n\nFAILED (failures=1)")
        state = {"n": 0}

        def judge(*a, **k):
            state["n"] += 1
            return Verdict(FAIL, 1, out) if state["n"] == 1 else Verdict(PASS, 0, "ok")

        _, calls = self.run_flow(["结论：通过"], judge=judge)
        devs = self.of(calls, "dev")
        self.assertGreaterEqual(len(devs), 2, "测试没过要退回写代码的")
        back = devs[1][1]
        self.assertIn("test_old_feature", back)
        self.assertIn("AssertionError", back)
        self.assertNotIn("无关的网络报错第 200 行", back, "不再整段贴输出")


if __name__ == "__main__":
    unittest.main()
