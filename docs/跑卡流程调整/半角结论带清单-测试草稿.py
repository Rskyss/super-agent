"""以后再修：复查员用半角冒号写“结论:不通过”并附清单（或加粗、引用写法），又碰上复查退满后全部测试超时停卡时，右栏复查格可能亮绿勾。
CARD-017 施工方和 Claude 接手时在“回复摘要”上修了三轮，每轮冒新边角（丢同行说明、删掉通配符和反引号、首行通过后文改判时显示通过），已撤回。
根本修法：程序在“退满 + 全部测试没过”停卡那条路上补记本轮复查结论，右栏照结论显示，不去改摘要。下面是那几轮写的测试，修时拿来用。"""

# —— 原 tests/test_panelfix_extra.py HalfWidthReviewConclusion ——
    def test_dispatched_half_width_fail_with_checklist_is_not_green(self):
        cdir = self.card(state="待验收")
        worktree = self.project.parent / f"{self.project.name}-worktrees" / "CARD-001"
        reply = (
            "结论:不通过\n"
            "复查清单：\n"
            "```json\n"
            '{"结论":"不通过","问题":[{"级别":"中等","来源":"这次改的",'
            '"类型":"功能问题","现象":"按钮没反应","位置":"app.py:1",'
            '"证据":"实跑发现点击没反应"}]}\n'
            "```\n"
        )

        def ask_fn(adapter, prompt, cwd, **kwargs):
            return AgentResult(ok=True, output=reply, attempts=1, final=reply)

        _dispatch(ask_fn, cdir, "复查员", "codex", "复查", "复查", worktree, {}, 1)
        saved_reply = next(e for e in reversed(read_flow(cdir))
                           if e.get("kind") == "回复" and e.get("src") == "复查员")
        self.assertIn("结论:不通过", text_of(cdir, saved_reply))
        self.assertFalse([e for e in read_flow(cdir) if e.get("kind") == "复查结论"])
        view = card_view(self.project, "CARD-001")
        self.assertNotEqual(_station(view, "rev")["state"], "done",
                            "真实派活存下的半角冒号“不通过”不能先亮绿勾")

# —— 原 tests/test_panelfix_extra_claude.py test_bold_and_quote_verdict_summary ——
    def test_bold_and_quote_verdict_summary(self):
        from collab.dispatch import _reply_summary
        for text in ("**结论:** 不通过（按钮没反应）\n\n```json\n{}\n```",
                     "> 结论:不通过（按钮没反应）\n\n```json\n{}\n```"):
            with self.subTest(text=text[:12]):
                self.assertEqual(_reply_summary("复查", text, True), "结论：不通过（按钮没反应）")

# —— 原 tests/test_panelfix_extra_claude.py test_reason_keeps_wildcards ——
    def test_reason_keeps_wildcards(self):
        from collab.dispatch import _reply_summary
        self.assertEqual(_reply_summary("复查", "结论：不通过（漏跑 test_panelfix_extra*.py）\n", True),
                         "结论：不通过（漏跑 test_panelfix_extra*.py）")
        self.assertEqual(_reply_summary("复查", "**结论：不通过（漏跑 tests/*）**\n", True),
                         "结论：不通过（漏跑 tests/*）")

# —— 原 tests/test_panelfix_extra_return.py（整份） ——
"""复查退回补充测试：半角冒号的“不通过”不能在右栏亮绿勾。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _dispatch, run_card
from collab.flow import read_flow, record, text_of
from collab.judge import FAIL, PASS, TIMEOUT, Verdict
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view


def _station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


def _half_width_reject():
    problem = {
        "级别": "中等",
        "来源": "这次改的",
        "类型": "功能问题",
        "现象": "复查写不通过时右栏仍亮绿勾",
        "位置": "desktop/view.py:1",
        "证据": {"方式": "推演", "对照": "desktop/view.py:926 与 collab/flow.py:123"},
    }
    block = json.dumps({"结论": "不通过", "问题": [problem]}, ensure_ascii=False, indent=1)
    return f"结论:不通过\n\n复查清单：\n```json\n{block}\n```\n"


class HalfWidthRejectDuringProgramDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        self.collab = init_collab(self.project)

    def card(self, card_id="CARD-001", state="测试中"):
        worktree = Path(self.tmp.name) / "worktrees" / card_id
        worktree.mkdir(parents=True)
        cdir = self.collab / "cards" / card_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "需求卡.md").write_text("# 右栏显示\n", encoding="utf-8")
        Card(card_id, state=state).save(cdir / "状态.json")
        record(cdir, "需求", src="你", dst="调度员", summary="右栏显示")
        record(cdir, "批准", src="你", dst="调度员", summary="批准方案")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
               tools={"测试员": "codex", "开发员": "codex", "复查员": "codex"})
        return cdir, worktree

    def test_reject_is_red_while_waiting_and_after_timeout_stop(self):
        cdir, worktree = self.card("CARD-001")

        def ask_fn(adapter, prompt, cwd, **kwargs):
            return AgentResult(ok=True, output="", attempts=1, final=_half_width_reject())

        _dispatch(ask_fn, cdir, "复查员", "codex", "复查", "复查", worktree, {}, 1)
        saved_reply = next(e for e in reversed(read_flow(cdir))
                           if e.get("kind") == "回复" and e.get("src") == "复查员")
        self.assertIn("结论:不通过", text_of(cdir, saved_reply))
        record(cdir, "全量", src="程序", phase="开始", summary="复查的同时在跑全部测试")

        waiting_view = card_view(self.project, "CARD-001")
        with self.subTest(waiting_for_program_decision=True):
            self.assertEqual(_station(waiting_view, "rev")["state"], "fail",
                             "复查员已写半角冒号“不通过”，等程序判定时不能亮绿勾")

        cdir2, worktree2 = self.card("CARD-002")
        reviews = [_half_width_reject() for _ in range(3)]

        class FullRunner:
            def __init__(self):
                self.calls = 0

            def run(self, worktree, stop):
                self.calls += 1
                if self.calls < 3:
                    stop.wait(5)
                    return Verdict(FAIL, 1, "这一轮被退回叫停，结果不能用来放行")
                return Verdict(TIMEOUT, None, "")

            def base(self, worktree, files, stop):
                return {name: Verdict(FAIL, 1, "ok") for name in files}

        def run_ask_fn(adapter, prompt, cwd, **kwargs):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt or "只改测试" in prompt:
                (worktree2 / "tests").mkdir(exist_ok=True)
                (worktree2 / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="汇报：写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(
            Card("CARD-002", state="待派发"),
            self.collab,
            worktree2,
            "true",
            "qwen",
            "codex",
            ask_fn=run_ask_fn,
            judge_fn=lambda *args, **kwargs: Verdict(PASS, 0, "ok"),
            reviewer_adapter="codex",
            full_runner=FullRunner(),
        )
        self.assertEqual(card.state, "需Claude介入")

        stopped_view = card_view(self.project, "CARD-002")
        with self.subTest(stopped_after_full_timeout=True):
            self.assertEqual(_station(stopped_view, "rev")["state"], "fail",
                             "退回用完且全部测试超时停卡后，复查格不能保留绿勾")


if __name__ == "__main__":
    unittest.main()
