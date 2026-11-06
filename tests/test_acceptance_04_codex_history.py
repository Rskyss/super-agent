"""04 号单：旧 Codex 对话只能看，不打开可写连接，不花模型额度。"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from acceptance_helpers import ROOT, STATIC, SWIFT, ServerCase
from desktop.clerk.codex_history import list_history, read_history
from desktop.dispatchers.codex import CodexDispatcher

FAKE = (sys.executable, str(Path(__file__).resolve().parent / "fake_codex_app_server.py"))
MARK = "【超级助手自动交代】"


class HistoryTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.db = Path(self.tmp) / "db.json"
        os.environ["FAKE_CODEX_DB"] = str(self.db)
        done = threading.Event()

        def emit(event):
            if event.get("type") == "turn_done":
                done.set()

        adapter = CodexDispatcher(self.project, emit, command=FAKE, marker=MARK)
        adapter.start()
        adapter.send("帮我看看进度")
        self.assertTrue(done.wait(10))
        self.thread_id = adapter.thread_id
        adapter.close()

    def test_lists_and_reads_old_conversations(self):
        found = list_history(self.project, command=FAKE)
        self.assertEqual([t["id"] for t in found], [self.thread_id])
        self.assertIn("帮我看看进度", found[0]["title"])
        events = read_history(self.project, self.thread_id, command=FAKE)
        self.assertTrue(any(e["type"] == "user" and "帮我看看进度" in e["text"] for e in events))
        self.assertTrue(all(e.get("history") for e in events))

    def test_reading_starts_no_new_conversation(self):
        before = self.db.read_text(encoding="utf-8")
        list_history(self.project, command=FAKE)
        read_history(self.project, self.thread_id, command=FAKE)
        self.assertEqual(json.loads(before), json.loads(self.db.read_text(encoding="utf-8")))

    def test_code_never_asks_for_a_writable_connection(self):
        source = (ROOT / "desktop" / "clerk" / "codex_history.py").read_text(encoding="utf-8")
        for words in ("thread/start", "thread/resume", "turn/start"):
            self.assertNotIn(words, source)

    def test_missing_codex_gives_an_empty_list(self):
        self.assertEqual(list_history(self.project, command=("/不存在/codex",)), [])
        self.assertEqual(read_history(self.project, "x", command=("/不存在/codex",)), [])

    def test_addresses_need_the_token(self):
        status, _ = self.get("/api/clerk/codex/list", project=self.project)
        self.assertEqual(status, 403)
        status, answer = self.get("/api/clerk/codex/list", project=self.project, token="口令")
        self.assertEqual((status, answer), (200, {"threads": []}))  # 假命令不支持：当作没有
        status, answer = self.get("/api/clerk/codex/thread", project=self.project, id="x", token="口令")
        self.assertEqual((status, answer), (200, {"events": []}))

    def test_pages(self):
        nav = (STATIC / "nav.html").read_text(encoding="utf-8") + (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("codexOld", nav)
        self.assertIn("Codex 旧对话", nav)
        self.assertIn("/api/clerk/codex/list", nav)
        self.assertIn("superagent://codex", nav)
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("openCodexHistory", chat)
        self.assertIn("这段只能看，想继续请用终端", chat)
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn('case "codex"', swift)
        self.assertIn("openCodexHistory", swift)
