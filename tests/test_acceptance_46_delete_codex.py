"""46 号单：Codex 模式删除主对话。只能删列表里的；连着删记住的列表；失败时列表不动。用假 Codex，不花额度。"""
import json
import os
import sys
from pathlib import Path

from acceptance_helpers import ServerCase, wait_until
from desktop.chat import ChatHub
from desktop.dispatchers.codex import CodexDispatcher

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_codex_app_server.py")]


def factories():
    return {"codex": lambda project, emit, background, marker: CodexDispatcher(
        project, emit, background=background, marker=marker, command=FAKE)}


class CodexCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.db = Path(self.tmp) / "db.json"
        os.environ["FAKE_CODEX_DB"] = str(self.db)
        os.environ.pop("FAKE_CODEX_NO_DELETE", None)
        self.hub = ChatHub(factories=factories())
        self.addCleanup(self.hub.close_all)
        self.server.hub = self.hub
        self.collab.mkdir(exist_ok=True)

    def open_new(self):
        session = self.hub.open(self.project, "codex", mode="fresh", with_opening=False)
        wait_until(lambda: any(e["type"] == "status" and e["text"].startswith("已连上") for e in session.events_since(0, 0)),
                   message="假 Codex 没连上")
        return session

    def talk(self, session, text):
        session.send(text)
        wait_until(lambda: any(e["type"] == "turn_done" for e in session.events_since(0, 0)) and not session.busy,
                   message="假 Codex 没回完")
        return session.adapter.thread_id

    def threads(self):
        return json.loads(self.db.read_text(encoding="utf-8"))["threads"]

    def listed(self):
        return [t["id"] for t in self.hub.thread_list(self.project)["threads"]]

    def two(self):
        first = self.talk(self.open_new(), "第一段")
        session = self.open_new()
        return first, self.talk(session, "第二段"), session


class Delete(CodexCase):
    def test_other_thread(self):
        first, second, _ = self.two()
        self.hub.delete_thread(self.project, first)
        self.assertNotIn(first, self.threads())
        self.assertEqual(self.listed(), [second])

    def test_current_thread(self):
        first, second, _ = self.two()
        self.hub.delete_thread(self.project, second)
        self.assertNotIn(second, self.threads())
        self.assertEqual(self.listed(), [first])

    def test_new_chat_after_deleting_current_works(self):
        first, second, _ = self.two()
        self.hub.delete_thread(self.project, second)
        third = self.talk(self.open_new(), "第三段")
        self.assertNotEqual(third, second)
        self.assertEqual(sorted(self.listed()), sorted([first, third]))

    def test_saved_list_forgets_it_after_restart(self):
        first, second, _ = self.two()
        self.hub.thread_list(self.project)
        self.hub.delete_thread(self.project, first)
        self.hub.close_all()
        saved = ChatHub(factories=factories()).thread_list(self.project)
        self.assertTrue(saved["cached"])
        self.assertEqual([t["id"] for t in saved["threads"]], [second])

    def test_deleting_the_last_one_also_empties_the_saved_list(self):
        only = self.talk(self.open_new(), "唯一一段")
        self.hub.thread_list(self.project)
        self.hub.delete_thread(self.project, only)
        self.hub.close_all()
        saved = ChatHub(factories=factories()).thread_list(self.project)
        self.assertEqual((saved["threads"], saved["current"]), ([], None))

    def test_only_threads_in_the_list(self):
        self.talk(self.open_new(), "第一段")
        db = json.loads(self.db.read_text(encoding="utf-8"))
        db["threads"]["别处的"] = {"cwd": "/别的项目", "turns": [{"id": "x", "items": []}], "updated": 1}
        self.db.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(LookupError):
            self.hub.delete_thread(self.project, "别处的")
        self.assertIn("别处的", self.threads())

    def test_replying_is_refused(self):
        session = self.open_new()
        current = self.talk(session, "第一段")
        session.send("数一数")
        wait_until(lambda: session.busy, message="假 Codex 没开始回复")
        item = next(t for t in self.hub.thread_list(self.project)["threads"] if t["id"] == current)
        self.assertTrue(item["busy"])
        with self.assertRaises(RuntimeError):
            self.hub.delete_thread(self.project, current)
        self.assertIn(current, self.threads())
        session.stop()

    def test_idle_threads_are_not_marked_busy(self):
        self.talk(self.open_new(), "第一段")
        self.assertFalse(self.hub.thread_list(self.project)["threads"][0]["busy"])

    def test_old_codex_fails_plainly_and_nothing_changes(self):
        first, second, _ = self.two()
        os.environ["FAKE_CODEX_NO_DELETE"] = "1"
        session = self.open_new()   # 假 Codex 启动时读开关
        third = self.talk(session, "第三段")
        with self.assertRaises(RuntimeError) as caught:
            self.hub.delete_thread(self.project, first)
        words = str(caught.exception)
        self.assertIn("没能删除", words)
        for jargon in ("thread/delete", "method not found", "Codex 出错"):
            self.assertNotIn(jargon, words)
        self.assertIn(first, self.threads())
        self.assertEqual(sorted(self.listed()), sorted([first, second, third]))


class Service(CodexCase):
    def post_delete(self, thread, token="口令"):
        return self.post("/api/chat/delete", {"project": self.project, "thread_id": thread}, token=token)

    def test_delete(self):
        first, second, _ = self.two()
        self.assertEqual(self.post_delete(first), (200, {"ok": True}))
        self.assertEqual(self.listed(), [second])

    def test_needs_the_password(self):
        first, _, _ = self.two()
        self.assertEqual(self.post_delete(first, token="错的")[0], 403)
        self.assertIn(first, self.threads())

    def test_not_connected(self):
        status, answer = self.post_delete("t1")
        self.assertEqual(status, 409)
        self.assertIn("还没连上", answer["error"])

    def test_not_in_the_list(self):
        self.two()
        status, answer = self.post_delete("没有这个")
        self.assertEqual(status, 404)
        self.assertIn("已经不在了", answer["error"])
