"""v0.96 A：写代码的“停下：”两条没测到的路（CARD-035 Codex 第 2 轮）。

1. 停下那次顺带改了范围外的文件、程序撤销失败先转了人工：没来得及记“等拍板”，下一次接着跑把停工回复当成交过活，
   直接跑判定进了待验收（严重）。
2. 复查退回 → 写代码的停下等拍板 → 接着跑：要重新派写代码的（测试替身原来只会让复查通过，没走过这条）。
"""
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.judge import PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask
from test_external_trial_fixes import CMD, Base


class StopPaths(Base):
    def go(self, dev_replies, reviews=None, changed_fn=None, revert_fn=None, judge=None):
        calls = []
        reviews = list(reviews or ["结论：通过"])
        dev_iter = iter(dev_replies)

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                calls.append("复查")
                text = reviews.pop(0) if len(reviews) > 1 else reviews[0]
                return AgentResult(ok=True, output="", attempts=1, final=text)
            if "编写自动化测试" in prompt:
                calls.append("测试员")
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            calls.append("开发员")
            self.prompts.append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final=next(dev_iter))

        judge = judge or (lambda *a, **k: Verdict(PASS, 0, "ok"))
        kw = {}
        if changed_fn is not None:
            kw.update(changed_fn=changed_fn, revert_fn=revert_fn)
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn), judge_fn=judge, reviewer_adapter="codex",
                        require_tests=True, allowed=["app.py"], **kw)[0]
        return card, calls

    def test_revert_failure_on_stop_reply_still_counts_as_asked(self):
        state = {"dirty": True, "reverts": 0}

        def changed(_wt):
            return ["app.py", "other.py"] if state["dirty"] else ["app.py"]

        def revert(_wt, _files):
            state["reverts"] += 1
            if state["reverts"] >= 2:   # 第一次撤不掉，第二次撤掉
                state["dirty"] = False

        card, _ = self.go(["停下：要改范围外的 other.py，等拍板\n汇报：没改代码"], changed_fn=changed, revert_fn=revert)
        self.assertEqual(card.state, "需Claude介入")
        card, calls = self.go(["汇报：改好了"], changed_fn=changed, revert_fn=revert)
        self.assertIn("开发员", calls, "停工那次不算交过活：接着跑要重新派写代码的，不能直接判定进待验收")
        self.assertEqual(card.state, "待验收")

    def test_revert_raising_on_stop_reply_still_counts_as_asked(self):
        """复查第 1 轮严重 1：撤越界时抛异常（真实里是删新建文件的 unlink 没权限），停下标记也要已经记下。"""
        state = {"dirty": True, "raise": True}

        def changed(_wt):
            return ["app.py", "other.py"] if state["dirty"] else ["app.py"]

        def revert(_wt, _files):
            if state["raise"]:
                raise PermissionError("没权限删")
            state["dirty"] = False

        with self.assertRaises(PermissionError):
            self.go(["停下：要改范围外的 other.py，等拍板\n汇报：没改代码"], changed_fn=changed, revert_fn=revert)
        state.update(dirty=False)
        state["raise"] = False
        card, calls = self.go(["汇报：改好了"], changed_fn=changed, revert_fn=revert)
        self.assertIn("开发员", calls, "停工那次不算交过活：接着跑要重新派写代码的")

    def test_review_reject_then_stop_then_resume_sends_dev_with_notes(self):
        card, calls = self.go(
            ["汇报：改好了", "停下：复查要的改法要动范围外的页面，等拍板\n汇报：没改"],
            reviews=["结论：不通过\n1. 【中等】【这次改的】按钮点了没反应。", "结论：通过"])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls.count("开发员"), 2)
        self.prompts.clear()
        card, calls = self.go(["汇报：按复查改好了"], reviews=["结论：通过"])
        self.assertIn("开发员", calls, "停下等拍板后接着跑，要重新派写代码的")
        self.assertTrue(self.prompts and "按钮点了没反应" in self.prompts[0], "重新派活要带上那次复查退回的意见")


if __name__ == "__main__":
    import unittest
    unittest.main()
