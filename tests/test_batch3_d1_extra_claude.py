"""D1 第 3 轮复查（Claude 接手补）：普通报错停下时，停下说明仍要写清是哪个角色、哪个工具没完成。"""
import test_redfirst_acceptance as rf
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import FAIL
from collab.state import Card


class PlainErrorKeepsWho(rf.Base):
    def test_plain_error_says_who(self):
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "cursor":
                return AgentResult(ok=False, attempts=1, final="", output="网络断了")
            return self.ask_fn(adapter, prompt, cwd, **kw)
        self.verdicts = [FAIL, FAIL]
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD, "cursor", "codex",
                        ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn, reviewer_adapter="agy",
                        require_tests=True)[0]
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertIn("开发员（cursor）没有完成", stop.get("say") or "")
        self.assertIn("网络断了", stop.get("say") or "")
