"""流程修理第 18 条（试跑记录 ⑥）：项目窗口的行政说了“点确认就建成任务”却没出确认框，提醒它一次。

2026-11-11 用户截图：行政整理完需求说“点确认我就变成任务”，屏幕上没有确认框。
"""
import os
import unittest

from acceptance_helpers import ScriptedClient, wait_idle
from collab.flow import record
from desktop.clerk.desk import claims_task_box
from test_acceptance_22_card_chat import ChatCase


class Words(unittest.TestCase):
    def test_claims(self):
        for text in ("我整理好了，你点确认就会建成任务卡片。", "点一下“确认”，我就把它变成任务。", "你点确认按钮就生成任务卡片了。"):
            self.assertTrue(claims_task_box(text), text)

    def test_not_claims(self):
        for text in ("要我把它建成任务卡片吗？", "你确认的话，我就出一个确认框。", "等你说完我再建任务卡片。",
                     "如果没问题，点确认就建成任务。", "这个任务卡片已经做完了。"):
            self.assertFalse(claims_task_box(text), text)


class MainChatReminds(ChatCase):
    def test_reminder_when_box_is_missing(self):
        self.client.steps = [{"text": "我整理好了，你点确认就会建成任务卡片。"}, {"text": "刚才没出，我现在出。"}]
        cid = self.desk.send(self.project, "", "帮我建个任务")
        wait_idle(self.desk, self.project, cid)
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn("这是系统提示", self.client.calls[1][0][-1]["content"])

    def test_card_chat_still_not_reminded(self):
        self.building()
        cid = self.open()
        self.client.steps = [{"text": "确认框已经出好了，你看一下"}, {"text": "多余"}]
        self.desk.send(self.project, cid, "改一下")
        wait_idle(self.desk, self.project, cid)
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
