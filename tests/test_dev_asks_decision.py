"""写代码的说“要拍板”就停下（train CARD-016，11-27）。

写代码的查清问题要改允许范围外的页面、要改开卡前就有的老规则，按规矩停下、没改代码，
程序却当成“测试没过”连退 3 轮（约 15 分钟、780 万 token 白费）。
现在：回复末尾有单独一行“停下：原因”，程序不跑判定、不算返工，直接转人工并写明原因。
"""
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask
from test_external_trial_fixes import CMD, Base


class DevAsksForDecision(Base):
    def go(self, dev_reply, judge=None, state="待派发"):
        calls = []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                calls.append("复查")
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                calls.append("测试员")
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            calls.append("开发员")
            self.prompts.append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final=dev_reply)

        judge = judge or (lambda *a, **k: Verdict(FAIL, 1, "AssertionError: x"))
        card = run_card(Card("CARD-001", state=state), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn), judge_fn=judge,
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        return card, calls

    def test_stop_line_hands_to_person_without_rework(self):
        card, calls = self.go("查了一圈。\n停下：要改允许范围外的复盘页，还要改开卡前就有的互看规则，等拍板\n"
                              "汇报：没改代码，停下等拍板")
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls.count("开发员"), 1, "不再退回写代码的空转")
        self.assertEqual(card.rework, 0, "停下等拍板不算返工")
        flow = read_flow(self.cdir)
        stop = [e for e in flow if e.get("kind") == "转人工"][-1]
        self.assertIn("复盘页", stop.get("summary", ""))
        self.assertIn("拍板", stop.get("summary", ""))
        self.assertFalse([e for e in flow if e.get("kind") == "判定"], "停下等拍板时不用再跑测试")

    def test_half_width_colon_also_counts(self):
        card, calls = self.go("停下: 需求自相矛盾\n汇报：等拍板")
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls.count("开发员"), 1)

    def test_mention_inside_sentence_is_not_a_stop(self):
        card, calls = self.go("上次说要停下：其实不用，已改好\n汇报：改好了")
        self.assertGreater(calls.count("开发员"), 1, "不是单独一行开头的“停下：”，照旧按测试没过退回")

    def test_quoted_stop_line_in_body_is_not_a_stop(self):
        card, calls = self.go("上次的原话：\n停下：旧原因\n这次已经处理好了\n汇报：完成")
        self.assertGreater(calls.count("开发员"), 1, "正文里单独一行引用的旧话不算，只认紧挨“汇报：”的那一行")

    def test_resume_after_stop_sends_dev_again(self):
        self.go("停下：要改范围外的页面\n汇报：没改代码，等拍板")
        card, calls = self.go("汇报：改好了", judge=lambda *a, **k: Verdict(PASS, 0, "ok"))
        self.assertIn("开发员", calls, "停下等拍板那次不算交过活，接着跑要重新派写代码的")
        self.assertEqual(card.state, "待验收")

    def test_normal_reply_still_returns_on_fail(self):
        card, calls = self.go("汇报：改好了")
        self.assertGreater(calls.count("开发员"), 1)

    def test_prompt_tells_how_to_stop(self):
        self.go("停下：等拍板\n汇报：等拍板")
        self.assertIn("停下：", self.prompts[0], "交代里要写清停下怎么写，程序才认得出")


if __name__ == "__main__":
    import unittest
    unittest.main()
