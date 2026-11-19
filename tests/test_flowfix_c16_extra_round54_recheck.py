"""C1.6 再次复查退回的三个场景。"""

import re
from shutil import copytree
from unittest.mock import patch

from collab import dispatch
from collab.agents import AgentResult
from collab.flow import record, text_of
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from test_flowfix4_converge import Base
from test_flowfix4_full import FakeRunner
from test_flowfix_c15_acceptance import item, review
from test_flowfix_c16_acceptance import checked, events


class Interrupted(RuntimeError):
    """第二次退回已存，还没派活时中断。"""


class ReviewReturns(Base):
    def run_reviews(self, replies, *, runner=None, resume=False):
        replies = iter(replies)

        def ask(_adapter, _prompt, _cwd, **_kwargs):
            task = events(self.cdir, "派活")[-1]
            if task["dst"] == "复查员":
                answer = next(replies, None)
                self.assertIsNotNone(answer, "复查员被多派了一次")
            else:
                if task["step"] == "写测试":
                    (self.worktree / "tests" / "test_export.py").write_text("", encoding="utf-8")
                answer = "汇报：已交活"
            return AgentResult(ok=True, output="", attempts=1, final=answer)

        card = (Card.load(self.cdir / "状态.json") if resume
                else Card(self.cdir.name, state="待派发"))
        card, _ = dispatch.run_card(
            card, self.collab, self.worktree, "true", "qwen", "codex",
            ask_fn=ask, judge_fn=lambda *a, **kw: Verdict(PASS, 0, "本卡测试通过"),
            reviewer_adapter="codex", full_runner=runner,
        )
        return card

    def test_new_severe_issue_survives_failed_review_rewrite(self):
        # C1.8 按新口径改（登记见方案-C1.8 第 3 节）：第二轮清单格式不对、重写也不对 → 直接停下转 Claude，
        # 严重问题在存下的原话里交给 Claude，不会丢，也不会放行（原来测的是“进入第三轮编号清单”）
        original = item(what="导出时没保留所选排序", where="app/export.py:12")
        severe = item(level="严重", what="取消导出会删掉原文件", where="app/export.py:48")
        invalid = dict(severe, 碰上="偶尔")

        card = self.run_reviews([
            review("不通过", [original]),
            checked("不通过", [invalid], [(1, "没改好")]),
            "结论：不通过\n重写后仍没附清单。",
        ])

        prompts = [text_of(self.cdir, task) for task in events(self.cdir, "派活")
                   if task["dst"] == "复查员"]
        self.assertIn("按固定格式重写", prompts[2])
        self.assertEqual(len(events(self.cdir, "退回")), 1)
        self.assertEqual(card.state, "需Claude介入")
        stop = events(self.cdir, "转人工")[-1]
        self.assertIn(severe["现象"], text_of(self.cdir, stop) or "", "严重问题在交给 Claude 的原话里")

    def test_zero_tests_cannot_clear_two_used_returns(self):
        original = item(what="导出时没保留所选排序")
        still_unfixed = checked("不通过", checks=[(1, "没改好")])
        empty_run = Verdict(PASS, 0, "Ran 0 tests in 0.001s\n\nOK\n")
        runner = FakeRunner([empty_run] * 3)

        card = self.run_reviews([review("不通过", [original]), still_unfixed, still_unfixed], runner=runner)

        self.assertEqual(len(events(self.cdir, "退回")), 2)
        self.assertEqual(runner.calls, 3, "第三轮应当确实收到一次全部测试结果")
        self.assertEqual(card.state, "需Claude介入", "汇总写着 OK，但实际跑了零项，不能自动放行")
        detail = card_phase(str(self.collab.parent), self.cdir.name)["detail"]
        self.assertRegex(detail, "全(?:部|量)测试")

    # test_failed_rewrite_hands_missing_items_to_workers_even_after_restart：C1.8 删除——它测的“整段原话退回后的下一轮”已不存在（重写仍看不懂直接停下转 Claude），登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节

