"""Codex 调度员适配器：用“假 Codex”（tests/fake_codex_app_server.py）按真实协议测，不联网、不花额度。"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from desktop.dispatchers.codex import CodexDispatcher, item_events, short_command

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_codex_app_server.py")]
MARK = "【超级助手自动交代】"


class Collector:
    def __init__(self):
        self.events = []
        self.cond = threading.Condition()

    def __call__(self, event):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def wait_for(self, pred, timeout=10):
        end = time.time() + timeout
        with self.cond:
            while True:
                hits = [e for e in self.events if pred(e)]
                if hits:
                    return hits
                left = end - time.time()
                if left <= 0:
                    raise AssertionError(f"等不到想要的事件；已有：{[e['type'] for e in self.events]}")
                self.cond.wait(left)

    def turn_done(self, n=1, timeout=10):
        return self.wait_for(lambda e: e["type"] == "turn_done" and len(
            [x for x in self.events if x["type"] == "turn_done"]) >= n, timeout)


class CodexDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        os.environ["FAKE_CODEX_DB"] = str(Path(self.tmp.name) / "db.json")
        os.environ["FAKE_CODEX_IMAGE"] = str(Path(self.tmp.name) / "猫.png")
        self.opened = []

    def tearDown(self):
        for d in self.opened:
            d.close()
        self.tmp.cleanup()

    def _open(self, opening="报一下进度", resume=True, background="你是调度员"):
        got = Collector()
        d = CodexDispatcher(self.project, got, background=background, marker=MARK, command=FAKE)
        self.opened.append(d)
        d.start(opening, resume=resume)
        return d, got

    def test_new_conversation_shows_opening_then_reply(self):
        d, got = self._open()
        got.turn_done()
        kinds = [e["type"] for e in got.events]
        self.assertEqual(kinds[0], "opening")
        self.assertEqual(got.events[0]["text"], "报一下进度")
        self.assertIn("message", kinds)
        self.assertEqual(d.thread_id, "t1")
        self.assertFalse(d.busy)
        # 调度员守则作为“背景交代”交给 Codex，不显示成用户的话
        import json
        db = json.loads(Path(os.environ["FAKE_CODEX_DB"]).read_text(encoding="utf-8"))
        self.assertEqual(db["threads"]["t1"]["instructions"], "你是调度员")
        # 用户的 Codex 可能设了“让 AI 替你审批”；在超级助手里，“允许吗？”一律问用户本人
        self.assertEqual(db["threads"]["t1"]["reviewer"], "user")

    def test_reply_pieces_add_up_to_the_full_message(self):
        d, got = self._open()
        got.turn_done()
        pieces = "".join(e["text"] for e in got.events if e["type"] == "delta")
        final = [e for e in got.events if e["type"] == "message"][-1]["text"]
        self.assertEqual(pieces, final)
        self.assertGreater(len([e for e in got.events if e["type"] == "delta"]), 1)

    def test_drawn_image_comes_with_its_file(self):
        d, got = self._open(opening="")
        d.send("画一只猫")
        image = got.wait_for(lambda e: e["type"] == "image")[0]
        self.assertEqual(image["path"], os.environ["FAKE_CODEX_IMAGE"])
        self.assertIn("橘猫", image["prompt"])
        self.assertTrue(any(e["type"] == "activity" and e["label"] == "画图中…" for e in got.events))

    def test_risky_command_asks_first_and_decline_stops_it(self):
        d, got = self._open(opening="")
        d.send("运行一下")
        ask = got.wait_for(lambda e: e["type"] == "approval")[0]
        self.assertEqual(ask["kind"], "command")
        self.assertEqual(ask["command"], "touch a.txt")
        self.assertEqual(ask["reason"], "需要新建文件")
        d.answer(ask["id"], "decline")
        got.turn_done()
        done = [e for e in got.events if e["type"] == "activity" and e["status"] == "declined"]
        self.assertTrue(done)
        self.assertIn("被拒绝", [e for e in got.events if e["type"] == "message"][-1]["text"])
        self.assertEqual([e["decision"] for e in got.events if e["type"] == "approval_done"], ["decline"])

    def test_accepting_lets_the_command_run(self):
        d, got = self._open(opening="")
        d.send("运行一下")
        ask = got.wait_for(lambda e: e["type"] == "approval")[0]
        d.answer(ask["id"], "accept")
        got.turn_done()
        self.assertTrue(any(e["type"] == "activity" and e["status"] == "done" for e in got.events))

    def test_same_question_cannot_be_answered_twice(self):
        d, got = self._open(opening="")
        d.send("运行一下")
        ask = got.wait_for(lambda e: e["type"] == "approval")[0]
        d.answer(ask["id"], "accept")
        with self.assertRaises(KeyError):
            d.answer(ask["id"], "accept")
        with self.assertRaises(ValueError):
            d.answer("999", "随便")

    def test_stop_in_the_middle(self):
        d, got = self._open(opening="")
        d.send("从 1 数到 999")
        got.wait_for(lambda e: e["type"] == "delta" and e["text"] == "5\n")
        d.interrupt()
        done = got.turn_done()[0]
        self.assertEqual(done["status"], "interrupted")
        self.assertFalse(d.busy)

    def test_reading_a_file_is_shown_as_reading(self):
        d, got = self._open(opening="")
        d.send("读一下说明")
        got.turn_done()
        labels = [e["label"] for e in got.events if e["type"] == "activity"]
        self.assertIn("读 README.md", labels)

    def test_cannot_send_while_still_working(self):
        d, got = self._open(opening="")
        d.send("从 1 数到 999")
        got.wait_for(lambda e: e["type"] == "delta")
        with self.assertRaises(RuntimeError):
            d.send("再来一句")
        d.interrupt()
        got.turn_done()

    def test_reopening_continues_the_last_conversation_with_history(self):
        d1, got1 = self._open(opening="第一次交代")
        got1.turn_done()
        d1.send("你好")
        got1.turn_done(2)
        d1.close()

        d2, got2 = self._open(opening="再报一次")
        got2.turn_done()
        self.assertEqual(d2.thread_id, d1.thread_id)
        history = [e for e in got2.events if e.get("history")]
        self.assertEqual([e["type"] for e in history if e["type"] in ("opening", "user")], ["opening", "user"])
        self.assertEqual([e["text"] for e in history if e["type"] == "user"], ["你好"])
        live = [e for e in got2.events if not e.get("history")]
        self.assertEqual(live[0], {"type": "opening", "text": "再报一次"})

    def test_new_conversation_when_asked(self):
        d1, got1 = self._open()
        got1.turn_done()
        d1.close()
        d2, got2 = self._open(resume=False)
        got2.turn_done()
        self.assertNotEqual(d2.thread_id, d1.thread_id)
        self.assertFalse(any(e.get("history") for e in got2.events))

    def test_lost_connection_is_reported_in_plain_words(self):
        d, got = self._open(opening="")
        try:
            d.send("崩")
        except RuntimeError:
            pass
        err = got.wait_for(lambda e: e["type"] == "error")[0]
        self.assertIn("连接断了", err["text"])

    def test_waits_for_previous_writer_to_release_before_resuming(self):
        d1, got1 = self._open(opening="第一轮")
        got1.turn_done()
        # 模拟终端稍后才松手：0.15 秒后关掉 d1
        threading.Timer(0.15, d1.close).start()

        got2 = Collector()
        d2 = CodexDispatcher(self.project, got2, background="你是调度员", marker=MARK, command=FAKE,
                             writer_wait=2.0, writer_poll=0.05)
        self.opened.append(d2)
        d2.start("", resume=True)
        self.assertEqual(d2.thread_id, d1.thread_id)
        statuses = [e["text"] for e in got2.events if e["type"] == "status"]
        self.assertIn("正在等对话让出来…", statuses)

    def test_reports_clear_error_and_closes_process_when_writer_never_releases(self):
        d1, got1 = self._open(opening="占着不放")
        got1.turn_done()

        got2 = Collector()
        d2 = CodexDispatcher(self.project, got2, background="你是调度员", marker=MARK, command=FAKE,
                             writer_wait=0.12, writer_poll=0.04)
        self.opened.append(d2)
        with self.assertRaises(RuntimeError) as ctx:
            d2.start("", resume=True)
        self.assertIn("别处打开", str(ctx.exception))
        # 连接失败后，刚启动的子进程必须已经关干净，不留在后台
        self.assertIsNotNone(d2.proc.poll())


class TranslationTest(unittest.TestCase):
    def test_shell_wrapper_is_hidden(self):
        self.assertEqual(short_command("/bin/zsh -lc 'npm test'"), "npm test")
        self.assertEqual(short_command("ls -la"), "ls -la")

    def test_opening_is_recognised_in_history(self):
        item = {"type": "userMessage", "content": [{"type": "text", "text": MARK + "先读交接"}]}
        self.assertEqual(item_events(item, MARK), [{"type": "opening", "text": "先读交接"}])

    def test_file_changes_are_summarised(self):
        item = {"type": "fileChange", "id": "f", "status": "completed",
                "changes": [{"path": "a/需求卡.md"}, {"path": "b/方案.md"}]}
        self.assertEqual(item_events(item, MARK)[0]["label"], "改 2 个文件：需求卡.md、方案.md")

    def test_thinking_steps_are_not_shown(self):
        self.assertEqual(item_events({"type": "reasoning", "id": "r"}, MARK), [])

    def test_only_super_agent_threads_are_kept_and_system_prompts_ignored_in_title(self):
        from desktop.dispatchers.codex import _is_super_agent_thread, _thread_title
        old_codex = {"id": "old", "originator": "codex_work_desktop", "preview": "以前在Codex客户端聊的"}
        sa_chat = {"id": "sa1", "originator": "super-agent", "preview": "# AGENTS.md instructions\n<INSTRUCTIONS>"}
        self.assertFalse(_is_super_agent_thread(old_codex, MARK))
        self.assertTrue(_is_super_agent_thread(sa_chat, MARK))
        self.assertEqual(_thread_title(sa_chat, MARK), "新对话（刚接手项目）")


if __name__ == "__main__":
    unittest.main()
