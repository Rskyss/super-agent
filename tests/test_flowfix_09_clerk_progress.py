"""流程修理第 12 条（试跑记录 ⑬）：卡住时行政说清原因和下一步能点什么；问进度按此刻状态答。

68b 里卡住时行政只说“复查员复查不通过，需要调度员看原因”，用户不知道该点什么；
以前答过的“开发中、返工 0 次”留在对话里，卡住后看着像行政答错了。
"""
from collab.flow import record
from desktop.clerk.tasks import TaskWatcher, _report_text, next_step_words
from test_acceptance_22_card_chat import ChatCase

STOP = "复查员（codex）复查不通过（已自动退回 2 次）：1. 还剩一句"


class Words(ChatCase):
    def test_next_step_lists_buttons(self):
        text = next_step_words({"actions": ["resume", "revise", "accept_issues", "discard"]})
        for name in ("接着跑", "不满意，继续改", "接受剩下的问题，进入待确认", "放弃这个需求"):
            self.assertIn(name, text)
        self.assertEqual(next_step_words({"actions": []}), "")

    def test_blocked_report_says_what_to_click(self):
        text = _report_text("blocked", "改按钮", {"detail": STOP, "actions": ["resume", "accept_issues"]})
        self.assertIn("卡住了", text)
        self.assertIn("还剩一句", text)
        self.assertIn("接受剩下的问题，进入待确认", text)

    def test_card_chat_news_on_stop_says_what_to_click(self):
        cdir = self.building()
        cid = self.open()
        record(cdir, "转人工", src="程序", dst="调度员", summary=STOP)
        from collab.state import Card
        Card("CARD-001", state="需Claude介入").save(cdir / "状态.json")
        TaskWatcher(self.desk).tick(self.project)
        said = [w for w in self.words(cid) if w and "还剩一句" in w]
        self.assertTrue(said, self.words(cid))
        self.assertIn("接受剩下的问题，进入待确认", said[0])

    def test_card_chat_answers_from_current_state(self):
        cdir = self.building()
        cid = self.open()
        record(cdir, "转人工", src="程序", dst="调度员", summary=STOP)
        from collab.state import Card
        Card("CARD-001", state="需Claude介入").save(cdir / "状态.json")
        self.say(cid, "现在到什么进度了")
        system = self.client.calls[-1][0][0]["content"]
        self.assertIn("卡住了", system)
        self.assertIn("接着跑", system)
        self.assertIn("以前说过的进度可能已经过时", system)
