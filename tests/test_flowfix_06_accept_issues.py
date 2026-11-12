"""流程修理第 9 条（试跑记录 ㉒）：复查不通过卡住时，用户可以“接受剩下的问题，进入待确认”。

68b 里最后剩两句少见说法，Claude 判为已知取舍，产品里却没有按钮，只能手动改卡片状态。用户 11-12 定：只有用户能点。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from acceptance_helpers import STATIC, needs_node
from collab.flow import read_flow, record
from collab.phase import card_phase
from collab.state import Card
from test_acceptance_11_fixes import Case

REVIEW_STOP = "复查员（codex）复查不通过（已自动退回 2 次）：1. 还剩一句少见说法"


class StateMachine(unittest.TestCase):
    def test_blocked_card_can_go_to_awaiting_confirmation(self):
        card = Card("CARD-001", state="需Claude介入")
        card.transition("待验收")
        self.assertEqual(card.state, "待验收")


class Button(Case):
    def blocked(self, reason):
        cdir = self.make_card(state="需Claude介入", plan=True)
        record(cdir, "转人工", src="程序", dst="调度员", summary=reason)
        self.make_copy()
        return cdir

    def test_offered_only_when_stopped_by_review(self):
        self.blocked(REVIEW_STOP)
        self.assertIn("accept_issues", card_phase(self.project, "CARD-001")["actions"])

    def test_not_offered_when_tests_failed(self):
        self.blocked("返工 3 次后测试仍没过，需要调度员处理")
        self.assertNotIn("accept_issues", card_phase(self.project, "CARD-001")["actions"])

    def test_accepting_moves_card_and_records_what_was_accepted(self):
        cdir = self.blocked(REVIEW_STOP)
        status, answer = self.act("accept_issues")
        self.assertEqual(status, 200, answer)
        self.assertEqual(self.state(), "待验收")
        took = [e for e in read_flow(cdir) if e.get("kind") == "接受"]
        self.assertEqual(len(took), 1)
        self.assertEqual(took[0]["src"], "你")
        self.assertIn("还剩一句少见说法", (cdir / took[0]["text"]).read_text(encoding="utf-8"))
        self.assertIn("accept", card_phase(self.project, "CARD-001")["actions"], "接受后照常点确认放进项目")

    def test_refused_when_not_stopped_by_review(self):
        self.blocked("返工 3 次后测试仍没过，需要调度员处理")
        status, _ = self.act("accept_issues")
        self.assertEqual(status, 409)
        self.assertEqual(self.state(), "需Claude介入")


@needs_node()
class PanelButton(unittest.TestCase):
    def test_label_and_confirm_text(self):
        script = (
            "var P=require(%r);"
            "var b=P.actionButtons({actions:['resume','revise','accept_issues','discard']},true);"
            "var k=b.map(function(x){return x.label});"
            "if(k.indexOf('接受剩下的问题，进入待确认')<0) throw new Error(k.join());"
            "var t=P.acceptIssuesConfirm('1. 还剩一句');"
            "if(t.indexOf('1. 还剩一句')<0||t.indexOf('确认没问题，放进项目')<0) throw new Error(t);"
            "console.log('ok');" % str(STATIC / "panel.js"))
        out = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
