"""行政人员的对话：说话、逐字回复、保存、切换、停止、失败不丢话。用假模型测，不花额度。"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_model_server import FakeModel  # noqa: E402

from desktop.clerk.client import ModelClient  # noqa: E402
from desktop.clerk.desk import ClerkDesk  # noqa: E402


def wait_idle(desk, project, conversation, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        answer = desk.events(project, conversation, 0, wait=0)
        if not answer["busy"]:
            return answer["events"]
        time.sleep(0.02)
    raise AssertionError("一直没有回完")


class ClerkDeskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = str(Path(self.tmp.name) / "项目")
        Path(self.project).mkdir()
        self.fake = FakeModel(slow_seconds=1)
        self.client = ModelClient(self.fake.base, "m", "good-key", timeout=5)
        self.desk = ClerkDesk(lambda: self.client)

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def kinds(self, events):
        return [e["type"] for e in events]

    def test_talking_gives_a_streamed_reply_in_order(self):
        cid = self.desk.send(self.project, "", "你好")
        events = wait_idle(self.desk, self.project, cid)
        kinds = self.kinds(events)
        self.assertEqual(kinds[:2], ["user", "turn_started"])
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-2:], ["message", "turn_done"])
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(next(e for e in events if e["type"] == "message")["text"], "好的，收到：你好")

    def test_conversation_comes_back_after_reopening(self):
        cid = self.desk.send(self.project, "", "第一句")
        wait_idle(self.desk, self.project, cid)
        again = ClerkDesk(lambda: self.client)
        events = again.events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual([e["type"] for e in events], ["user", "message"])
        self.assertTrue(all(e["history"] for e in events))
        self.assertEqual(events[1]["text"], "好的，收到：第一句")

    def test_model_sees_the_earlier_conversation_and_its_role(self):
        cid = self.desk.send(self.project, "", "第一句")
        wait_idle(self.desk, self.project, cid)
        self.desk.send(self.project, cid, "第二句")
        wait_idle(self.desk, self.project, cid)
        sent = [m for m in self.fake.requests if m["messages"][-1]["content"] == "第二句"][-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("行政", sent[0]["content"])
        self.assertEqual([m["content"] for m in sent[1:]], ["第一句", "好的，收到：第一句", "第二句"])

    def test_list_is_newest_first_with_titles_and_blank_chats_leave_nothing(self):
        self.assertEqual(self.desk.list(self.project)["threads"], [])
        first = self.desk.send(self.project, "", "第一段的话")
        wait_idle(self.desk, self.project, first)
        time.sleep(1.1)
        second = self.desk.send(self.project, "", "第二段的话")
        wait_idle(self.desk, self.project, second)
        threads = self.desk.list(self.project)["threads"]
        self.assertEqual([t["id"] for t in threads], [second, first])
        self.assertTrue(all(t["title"] for t in threads))

    def test_title_is_named_from_the_first_exchange(self):
        cid = self.desk.send(self.project, "", "帮我看看这个项目现在怎么样")
        wait_idle(self.desk, self.project, cid)
        end = time.time() + 3
        while time.time() < end and self.desk.list(self.project)["threads"][0]["title"].startswith("帮我看看"):
            time.sleep(0.05)
        title = self.desk.list(self.project)["threads"][0]["title"]
        self.assertTrue(title.startswith("好的，收到"), title)  # 假模型的回答当作起的名字
        self.assertLessEqual(len(title), 24)

    def test_focus_marks_the_selected_conversation(self):
        cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, cid)
        self.desk.focus(self.project, cid)
        self.assertEqual(self.desk.list(self.project)["current"], cid)
        self.assertTrue(self.desk.list(self.project)["threads"][0]["current"])
        self.desk.focus(self.project, "")
        self.assertIsNone(self.desk.list(self.project)["current"])

    def test_two_conversations_can_reply_at_once(self):
        first = self.desk.send(self.project, "", "慢一点")
        second = self.desk.send(self.project, "", "你好")
        self.assertEqual(self.kinds(wait_idle(self.desk, self.project, second))[-1], "turn_done")
        self.assertTrue(self.desk.events(self.project, first, 0, wait=0)["busy"])
        wait_idle(self.desk, self.project, first)

    def test_sending_while_replying_is_refused_and_the_words_are_not_lost(self):
        cid = self.desk.send(self.project, "", "慢一点")
        with self.assertRaises(RuntimeError):
            self.desk.send(self.project, cid, "别急")
        wait_idle(self.desk, self.project, cid)

    def test_stop_ends_right_away_and_a_late_reply_is_ignored(self):
        cid = self.desk.send(self.project, "", "慢一点")
        self.desk.stop(self.project, cid)
        events = self.desk.events(self.project, cid, 0, wait=0)
        self.assertFalse(events["busy"])
        self.assertEqual(self.kinds(events["events"])[-1], "turn_done")
        time.sleep(1.3)
        self.assertNotIn("message", self.kinds(self.desk.events(self.project, cid, 0, wait=0)["events"]))

    def test_failure_keeps_the_words_and_shows_a_plain_message(self):
        cid = self.desk.send(self.project, "", "额度")
        events = wait_idle(self.desk, self.project, cid)
        self.assertIn("额度", next(e for e in events if e["type"] == "error")["text"])
        self.assertEqual(self.kinds(events)[-1], "turn_done")
        again = ClerkDesk(lambda: self.client).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual(again[0]["text"], "额度")

    def test_broken_reply_keeps_what_already_arrived(self):
        cid = self.desk.send(self.project, "", "半截")
        wait_idle(self.desk, self.project, cid)
        again = ClerkDesk(lambda: self.client).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual([e["type"] for e in again], ["user", "message"])

    def test_no_key_yet_says_so(self):
        desk = ClerkDesk(lambda: None)
        with self.assertRaises(RuntimeError) as caught:
            desk.send(self.project, "", "你好")
        self.assertIn("行政", str(caught.exception))

    def test_unknown_or_unsafe_conversation_id_is_safe(self):
        answer = self.desk.events(self.project, "../../etc/passwd", 0, wait=0)
        self.assertEqual(answer, {"events": [], "open": False, "busy": False})
        with self.assertRaises(RuntimeError):
            self.desk.send(self.project, "../bad", "你好")

    def test_save_failure_does_not_lock_the_conversation(self):
        cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, cid)
        folder = Path(self.project) / ".collab" / "行政对话"
        folder.chmod(0o500)
        (folder / f"{cid}.jsonl").chmod(0o400)
        try:
            with self.assertRaises(RuntimeError) as caught:
                self.desk.send(self.project, cid, "第二句")
            self.assertIn("保存", str(caught.exception))
        finally:
            folder.chmod(0o700)
            (folder / f"{cid}.jsonl").chmod(0o600)
        self.desk.send(self.project, cid, "第三句")
        self.assertIn("第三句", [e.get("text") for e in wait_idle(self.desk, self.project, cid)])

    def test_first_message_that_cannot_be_saved_leaves_no_broken_entry(self):
        folder = Path(self.project) / ".collab"
        folder.mkdir()
        folder.chmod(0o500)
        try:
            with self.assertRaises(RuntimeError):
                self.desk.send(self.project, "", "你好")
        finally:
            folder.chmod(0o700)
        self.assertEqual(self.desk.list(self.project)["threads"], [])

    def test_damaged_index_is_rebuilt_from_the_conversation_files(self):
        first = self.desk.send(self.project, "", "第一段的话")
        wait_idle(self.desk, self.project, first)
        second = self.desk.send(self.project, "", "第二段的话")
        wait_idle(self.desk, self.project, second)
        time.sleep(0.3)
        (Path(self.project) / ".collab" / "行政对话" / "索引.json").write_text("坏了", encoding="utf-8")
        threads = self.desk.list(self.project)["threads"]
        self.assertEqual({t["id"] for t in threads}, {first, second})
        self.assertTrue(all(t["title"] for t in threads))
        third = self.desk.send(self.project, "", "第三段的话")
        wait_idle(self.desk, self.project, third)
        self.assertEqual(len(self.desk.list(self.project)["threads"]), 3)

    def test_entry_whose_file_is_gone_is_hidden_and_stops_polling(self):
        cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, cid)
        (Path(self.project) / ".collab" / "行政对话" / f"{cid}.jsonl").unlink()
        fresh = ClerkDesk(lambda: self.client)
        self.assertEqual(fresh.list(self.project)["threads"], [])
        self.assertFalse(fresh.events(self.project, cid, 0, wait=0)["open"])

    def test_damaged_saved_lines_are_skipped(self):
        cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, cid)
        path = Path(self.project) / ".collab" / "行政对话" / f"{cid}.jsonl"
        with path.open("ab") as handle:
            handle.write(b"\xff\xfe bad\n{not json\n")
        again = ClerkDesk(lambda: self.client).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual(len(again), 2)


if __name__ == "__main__":
    unittest.main()
