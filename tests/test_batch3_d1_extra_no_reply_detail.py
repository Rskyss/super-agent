"""第三批 D1 复查：模型没回话停下后，右栏详情不能丢原始原因。"""
import unittest

import test_redfirst_acceptance as rf
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.judge import FAIL
from collab.state import Card


class NoReplyStopDetail(rf.Base):
    def test_no_reply_stop_keeps_wait_reason_in_right_panel(self):
        stop_reason = (
            "看门人：超过 15 分钟模型没回话"
            "（工具在等模型，没在跑命令、没改文件），已停止"
        )

        def ask(adapter, prompt, cwd, **kwargs):
            if adapter == "cursor":
                return AgentResult(ok=False, attempts=1, final="", output=stop_reason)
            return self.ask_fn(adapter, prompt, cwd, **kwargs)

        self.verdicts = [FAIL]
        card = run_card(
            Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD,
            "cursor", "codex", ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn,
            reviewer_adapter="agy", require_tests=True,
        )[0]

        self.assertEqual(card.state, "需Claude介入")
        from desktop.view import card_view
        detail = card_view(self.project, "CARD-001")["card"]["phase"]["detail"]
        self.assertIn("超过 15 分钟模型没回话", detail)
        self.assertIn("工具在等模型", detail)
        self.assertIn("没在跑命令", detail)
        self.assertIn("没改文件", detail)
        self.assertIn("再点“接着跑”", detail)


if __name__ == "__main__":
    unittest.main()
