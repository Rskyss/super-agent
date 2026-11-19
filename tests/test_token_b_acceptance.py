"""token 用量第二版 B 卡验收测试（Claude 写；**施工方不许改**，补充测试另写 test_token_b_extra*.py）。

依据 docs/token用量第二版/方案.md（行政部分）、施工单-B.md：行政每段对话累计用了多少 token，
每回答一次往上加（含它中途调工具的每个来回、起标题那一次），存进对话记录，重开还在。
"""
import threading
import unittest

from acceptance_helpers import ProjectCase, needs_node, run_node, tool_call, wait_idle, wait_until
from desktop.clerk.desk import ClerkDesk


class CountingClient:
    """假模型：每一步可以写 tokens（不写就当这一步没报用量）。"""

    def __init__(self, steps, title_tokens=7):
        self.steps = list(steps)
        self.title_tokens = title_tokens
        self.lock = threading.Lock()

    def chat(self, messages, on_text=None, tools=None):
        text_all = str(messages)
        with self.lock:
            if "用不超过 12 个字" in text_all:   # 起标题
                return {"text": "标题", "tool_calls": [], "tokens": self.title_tokens}
            step = self.steps.pop(0) if self.steps else {"text": "好的。", "tokens": 10}
        text = step.get("text", "")
        if text and on_text:
            on_text(text)
        return {"text": text, "tool_calls": step.get("tool_calls", []), "tokens": step.get("tokens")}


def usage_total(events):
    totals = [e.get("total") for e in events if e.get("type") == "usage"]
    return totals[-1] if totals else None


class ClerkUsage(ProjectCase):
    def setUp(self):
        super().setUp()
        self.make_card(state="开发中", title="改按钮")

    def talk(self, client, desk=None, cid="", text="进度怎么样"):
        desk = desk or ClerkDesk(lambda: client)
        cid = desk.send(self.project, cid, text)
        events = wait_idle(desk, self.project, cid)
        return desk, cid, events

    def wait_for_usage(self, desk, cid, total):
        # 回答结束后才会另起线程取标题，可能先看到只有回答的用量。
        wait_until(lambda: usage_total(desk.events(self.project, cid)["events"]) == total, 5,
                   f"等不到累计用量 {total} token")

    def test_one_answer_adds_every_round_and_the_title(self):
        client = CountingClient([{"tool_calls": [tool_call("list_cards")], "tokens": 1200},
                                 {"text": "有一张卡在做", "tokens": 800}])
        desk, cid, _ = self.talk(client)
        self.wait_for_usage(desk, cid, 1200 + 800 + 7)

    def test_second_answer_keeps_adding(self):
        client = CountingClient([{"text": "第一句", "tokens": 100}, {"text": "第二句", "tokens": 250}])
        desk, cid, _ = self.talk(client)
        self.wait_for_usage(desk, cid, 100 + 7)
        self.talk(client, desk=desk, cid=cid, text="再说一句")
        self.wait_for_usage(desk, cid, 100 + 7 + 250)

    def test_total_survives_reopening(self):
        client = CountingClient([{"text": "好", "tokens": 321}])
        desk, cid, _ = self.talk(client)
        total = 321 + 7
        self.wait_for_usage(desk, cid, total)
        reopened = ClerkDesk(lambda: client)
        events = reopened.events(self.project, cid)["events"]
        self.assertEqual(usage_total(events), total)
        self.assertFalse([e for e in events if e.get("type") in ("message", "user") and "usage" in str(e.get("text"))],
                         "用量不能当成一条消息显示")
        # 重开后接着聊，从原来的数往上加
        client.steps = [{"text": "又一句", "tokens": 50}]
        self.talk(client, desk=reopened, cid=cid, text="还在吗")
        self.wait_for_usage(reopened, cid, total + 50)

    def test_usage_is_not_sent_to_the_model(self):
        client = CountingClient([{"text": "好", "tokens": 321}])
        desk, cid, _ = self.talk(client)
        reopened = ClerkDesk(lambda: client)
        conv = reopened._conversation(self.project, cid)
        self.assertTrue(all(m.get("role") in ("user", "assistant", "system", "tool") for m in conv.messages))
        self.assertFalse([m for m in conv.messages if "321" in str(m.get("content"))])

    def test_no_usage_reported_means_no_number(self):
        client = CountingClient([{"text": "好"}], title_tokens=None)
        desk, cid, events = self.talk(client)
        self.assertIsNone(usage_total(desk.events(self.project, cid)["events"]), "服务商没报用量就不显示数，不写 0")


@needs_node()
class HeaderText(unittest.TestCase):
    def test_node_rules(self):
        out = run_node("require('./tests/token_b_chat_check.js')")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
