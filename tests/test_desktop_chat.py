"""聊天会话与聊天用的地址：事件按顺序到达、图片只放行调度员报过的、必须带口令。用“假 Codex”测。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from desktop import chat as chat_module
from desktop.chat import ChatHub, ChatSession
from desktop.dispatchers.codex import CodexDispatcher

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_codex_app_server.py")]


def fake_factories():
    return {"codex": lambda project, emit, background, marker: CodexDispatcher(
        project, emit, background=background, marker=marker, command=FAKE)}


def wait_until(session, pred, timeout=10):
    end, seen = time.time() + timeout, 0
    while time.time() < end:
        for event in session.events_since(seen, timeout=0.2):
            seen = event["seq"]
            if pred(event):
                return event
    raise AssertionError("等不到想要的事件：" + str([e["type"] for e in session.events_since(0, 0)]))


class ChatSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        self.project.mkdir()
        os.environ["FAKE_CODEX_DB"] = str(root / "db.json")
        self.image = root / "猫.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        os.environ["FAKE_CODEX_IMAGE"] = str(self.image)
        self.hub = ChatHub(factories=fake_factories())

    def tearDown(self):
        self.hub.close_all()
        self.tmp.cleanup()

    def test_open_connects_and_sends_the_right_opening(self):
        s = self.hub.open(self.project, "codex")
        wait_until(s, lambda e: e["type"] == "turn_done")
        events = s.events_since(0, 0)
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))  # 编号连续
        opening = [e for e in events if e["type"] == "opening"][0]
        self.assertIn("第一次接入", opening["text"])  # 还没有项目说明 → 第一次的交代
        self.assertTrue(any(e["type"] == "status" and "Codex" in e["text"] for e in events))

    def test_opening_the_app_sends_nothing_once_there_is_a_profile(self):
        """打开软件只连上，不自动发话：每发一次都要花十几万 token。"""
        (self.project / "docs").mkdir()
        (self.project / "docs" / "项目说明.md").write_text("说明", encoding="utf-8")
        s = self.hub.open(self.project, "codex")
        wait_until(s, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        time.sleep(0.3)
        kinds = [e["type"] for e in s.events_since(0, 0)]
        self.assertNotIn("opening", kinds)
        self.assertNotIn("turn_started", kinds)

    def test_switching_back_from_terminal_does_not_repeat_the_opening(self):
        s = self.hub.open(self.project, "codex", with_opening=False)
        wait_until(s, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        self.assertFalse(any(e["type"] == "opening" for e in s.events_since(0, 0)))
        self.assertFalse(s.busy)

    def test_events_since_only_returns_new_ones(self):
        s = self.hub.open(self.project, "codex")
        wait_until(s, lambda e: e["type"] == "turn_done")
        last = s.events_since(0, 0)[-1]["seq"]
        self.assertEqual(s.events_since(last, 0.1), [])
        s.send("你好")
        fresh = s.events_since(last, 5)
        self.assertTrue(fresh)
        self.assertTrue(all(e["seq"] > last for e in fresh))

    def test_images_are_only_served_after_the_dispatcher_reports_them(self):
        s = self.hub.open(self.project, "codex")
        wait_until(s, lambda e: e["type"] == "turn_done")
        self.assertFalse(s.image_allowed(str(self.image)))
        s.send("画一只猫")
        wait_until(s, lambda e: e["type"] == "image")
        self.assertTrue(s.image_allowed(str(self.image)))
        self.assertFalse(s.image_allowed("/etc/passwd"))

    def test_answer_and_stop_go_through_the_session(self):
        s = self.hub.open(self.project, "codex")
        wait_until(s, lambda e: e["type"] == "turn_done")
        s.send("运行一下")
        ask = wait_until(s, lambda e: e["type"] == "approval")
        s.answer(ask["id"], "decline")
        wait_until(s, lambda e: e["type"] == "approval_done")
        wait_until(s, lambda e: e["type"] == "turn_done" and e["seq"] > ask["seq"])
        s.send("从 1 数到 999")
        wait_until(s, lambda e: e["type"] == "delta" and e["text"] == "3\n")
        s.stop()
        done = wait_until(s, lambda e: e["type"] == "turn_done" and e.get("status") == "interrupted")
        self.assertTrue(done)

    def test_same_project_reuses_the_session_and_another_project_replaces_it(self):
        s1 = self.hub.open(self.project, "codex")
        self.assertIs(self.hub.open(self.project, "codex"), s1)
        other = Path(self.tmp.name) / "别的项目"
        other.mkdir()
        s2 = self.hub.open(other, "codex")
        self.assertIsNot(s2, s1)
        self.assertIsNone(self.hub.get(self.project))  # 一次只连一个项目

    def test_unknown_dispatcher_is_refused_in_plain_words(self):
        with self.assertRaises(ValueError) as ctx:
            self.hub.open(self.project, "custom:qwen")
        self.assertIn("终端", str(ctx.exception))

    def test_failed_session_updates_status_and_recovers_when_reopened(self):
        # 先用一个外部连接占住这段对话
        holder = CodexDispatcher(self.project, lambda e: None, command=FAKE, writer_wait=0.1, writer_poll=0.03)
        holder.start("占住", resume=False)
        try:
            fast_hub = ChatHub(factories={"codex": lambda project, emit, background, marker: CodexDispatcher(
                project, emit, background=background, marker=marker, command=FAKE, writer_wait=0.1, writer_poll=0.03)})
            s1 = fast_hub.open(self.project, "codex", with_opening=False)
            wait_until(s1, lambda e: e["type"] == "error")
            statuses = [e["text"] for e in s1.events_since(0, 0) if e["type"] == "status"]
            self.assertIn("正在等对话让出来…", statuses)
            self.assertEqual(statuses[-1], "未连上 Codex")
            self.assertFalse(s1.usable)
        finally:
            holder.close()

        # 对话让出来后，再点“聊天”（重新 open）能建新会话并连上，不再卡在旧报错
        s2 = fast_hub.open(self.project, "codex", with_opening=False)
        self.assertIsNot(s2, s1)
        wait_until(s2, lambda e: e["type"] == "status" and e["text"] == "已连上 Codex")
        fast_hub.close_all()


class BlankNewChatTest(unittest.TestCase):
    """新对话等用户说第一句才开始：不自动发交代，不花额度，也不在列表里留空对话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        (self.project / "docs").mkdir(parents=True)
        (self.project / "docs" / "项目说明.md").write_text("# 项目说明\n\n写好了\n", encoding="utf-8")
        os.environ["FAKE_CODEX_DB"] = str(root / "db.json")
        self.hub = ChatHub(factories=fake_factories())

    def tearDown(self):
        self.hub.close_all()
        self.tmp.cleanup()

    def connected(self, session):
        wait_until(session, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        return session

    def test_new_chat_sends_nothing_until_the_user_speaks(self):
        session = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        time.sleep(0.3)
        kinds = [e["type"] for e in session.events_since(0, 0)]
        self.assertNotIn("opening", kinds)
        self.assertNotIn("turn_started", kinds)
        session.send("第一句话")
        wait_until(session, lambda e: e["type"] == "turn_done")

    def test_first_time_project_still_gets_its_setup_message(self):
        (self.project / "docs" / "项目说明.md").unlink()
        session = self.hub.open(self.project, "codex", mode="fresh")
        wait_until(session, lambda e: e["type"] == "opening")

    def test_list_hides_conversations_the_user_never_spoke_in(self):
        talked = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        talked.send("我说过话")
        wait_until(talked, lambda e: e["type"] == "turn_done")
        blank = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        self.assertEqual([t["title"] for t in blank.list_threads()], ["我说过话"])

    def test_words_that_look_like_markup_still_count_as_the_user_speaking(self):
        for words in ("<div>这个按钮坏了</div>", "# AGENTS.md 这个文件怎么写", "新对话（刚接手项目）"):
            talked = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
            talked.send(words)
            wait_until(talked, lambda e: e["type"] == "turn_done")
            self.assertIn(talked.adapter.thread_id, [t["id"] for t in talked.list_threads()], words)

    def test_system_text_and_user_markup_are_told_apart(self):
        from desktop.dispatchers.codex import _is_real_user_text
        for words in ("<el-button type=\"primary\">提交</el-button>", "<my-app></my-app>", "<van-button>", "<div>"):
            self.assertTrue(_is_real_user_text(words, "M"), words)
        for words in ("<environment_context>\n  <cwd>", "<turn_aborted>", "<in-app-browser-context>", "<heartbeat>",
                      "<guardian_tool_descriptions>", '<image name=[Image #1] path="/tmp/a.png">'):
            self.assertFalse(_is_real_user_text(words, "M"), words)

    def test_a_last_message_with_no_readable_text_does_not_hide_the_conversation(self):
        from desktop.dispatchers.codex import _user_words
        rollout = Path(self.tmp.name) / "记录.jsonl"
        lines = [{"payload": {"type": "message", "role": "user", "content": [{"text": words}]}} for words in ("修复登录", "#")]
        rollout.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
        self.assertEqual(_user_words({"path": str(rollout)}, "M"), "修复登录")

    def test_blank_new_chat_is_never_saved_so_it_cannot_be_reopened(self):
        """和真实 Codex 一样：没说过话的对话没有存档，接不回来，列表里也没有。"""
        blank = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        thread = blank.adapter.thread_id
        self.assertEqual(blank.list_threads(), [])
        self.hub.close_all()
        again = self.hub.open(self.project, "codex", thread_id=thread)
        error = wait_until(again, lambda e: e["type"] == "error")
        self.assertIn("no rollout found", error["text"])

    def test_other_tools_conversations_are_never_picked_on_reopen(self):
        from desktop.dispatchers import codex
        adapter = CodexDispatcher(self.project, lambda e: None, marker="M", command=FAKE)
        self.assertFalse(adapter._talked_in({"id": "x", "originator": "codex_work_desktop", "preview": "别的工具里聊的"}))
        self.assertFalse(codex._is_super_agent_thread({"id": "x", "originator": "codex_work_desktop", "preview": "别的工具里聊的"}, "M"))

    def test_auto_opening_only_conversation_is_hidden_and_skipped_on_reopen(self):
        talked = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        talked.send("真实的问题")
        wait_until(talked, lambda e: e["type"] == "turn_done")
        real = talked.adapter.thread_id
        auto = self.connected(self.hub.open(self.project, "codex", mode="fresh"))
        auto.adapter.send("交代", opening=True)
        wait_until(auto, lambda e: e["type"] == "turn_done")
        self.assertEqual([t["id"] for t in auto.list_threads()], [real])
        self.hub.close_all()
        again = self.hub.open(self.project, "codex", with_opening=False)
        self.connected(again)
        self.assertEqual(again.adapter.thread_id, real)


class FastSwitchTest(unittest.TestCase):
    """连续快速切对话：只能留下一条连接，否则旧连接会一直占着对话，别的地方再也打不开它。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        self.project.mkdir()
        os.environ["FAKE_CODEX_DB"] = str(root / "db.json")
        self.made = []

        def factory(project, emit, background, marker):
            adapter = CodexDispatcher(project, emit, background=background, marker=marker, command=FAKE)
            self.made.append(adapter)
            return adapter

        self.hub = ChatHub(factories={"codex": factory})

    def tearDown(self):
        self.hub.close_all()
        for adapter in self.made:
            adapter.close()
        self.tmp.cleanup()

    def alive(self):
        return [a for a in self.made if a.proc is not None and a.proc.poll() is None]

    def settle(self, want):
        end = time.time() + 10
        while time.time() < end and len(self.alive()) != want:
            time.sleep(0.05)
        time.sleep(0.5)  # 再等一会儿，确认没有迟到的连接冒出来
        return self.alive()

    def test_session_closed_before_it_connects_starts_nothing(self):
        session = ChatSession(self.project, "codex", self.hub.factories["codex"])
        session.close()
        session.open("fresh")
        self.assertEqual(self.settle(0), [])

    def test_dispatcher_closed_before_it_starts_leaves_no_process(self):
        adapter = CodexDispatcher(self.project, lambda event: None, command=FAKE)
        self.made.append(adapter)
        adapter.close()
        with self.assertRaises(RuntimeError):
            adapter.start("")
        self.assertEqual(self.settle(0), [])

    def test_many_quick_switches_leave_exactly_one_connection(self):
        real = chat_module.opening

        def slow_opening(project):
            time.sleep(0.2)  # 把“刚建好会话、还没连”的空档放大，好稳定复现
            return real(project)

        with mock.patch.object(chat_module, "opening", slow_opening):
            workers = [threading.Thread(target=self.hub.open, args=(self.project, "codex", "fresh"))
                       for _ in range(6)]
            for worker in workers:
                worker.start()
                time.sleep(0.02)
            for worker in workers:
                worker.join()
        self.assertEqual(self.settle(1), [self.hub._session.adapter])

    def test_failed_conversation_can_be_reopened_once_it_is_free(self):
        holder = CodexDispatcher(self.project, lambda event: None, command=FAKE)
        self.made.append(holder)
        thread = holder.start("", resume=False)
        holder.send("说过话才有存档")
        time.sleep(0.3)

        def factory(project, emit, background, marker):
            adapter = CodexDispatcher(project, emit, background=background, marker=marker, command=FAKE, writer_wait=0)
            self.made.append(adapter)
            return adapter

        self.hub.factories["codex"] = factory
        first = self.hub.open(self.project, "codex", thread_id=thread)
        error = wait_until(first, lambda e: e["type"] == "error")
        self.assertIn("再点一下", error["text"])
        holder.close()
        second = self.hub.open(self.project, "codex", thread_id=thread)
        self.assertIsNot(second, first)
        wait_until(second, lambda e: e["type"] == "status" and e.get("thread_id") == thread)

    def test_last_click_wins_even_while_the_previous_one_is_closing(self):
        order = []
        slow_close = CodexDispatcher.close

        def close(adapter):
            time.sleep(0.3)  # 关旧连接要时间，这段时间里后面的点击不能排队乱序
            slow_close(adapter)

        self.hub.open(self.project, "codex", "fresh")
        with mock.patch.object(CodexDispatcher, "close", close):
            workers = []
            for name in ("a", "b", "c"):
                def click(name=name):
                    order.append((name, self.hub.open(self.project, "codex", "fresh")))
                workers.append(threading.Thread(target=click))
                workers[-1].start()
                time.sleep(0.05)
            for worker in workers:
                worker.join()
        self.assertIs(self.hub._session, dict(order)["c"])


class ChatServerTest(unittest.TestCase):
    def setUp(self):
        from desktop.server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        self.project.mkdir()
        os.environ["FAKE_CODEX_DB"] = str(root / "db.json")
        self.image = root / "猫.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        os.environ["FAKE_CODEX_IMAGE"] = str(self.image)
        self.hub = ChatHub(factories=fake_factories())
        self.server = make_server(port=0, token="口令123", hub=self.hub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.hub.close_all()
        self.tmp.cleanup()

    def _post(self, path, body, token="口令123"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "X-Chat-Token": urllib.parse.quote(token)})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def _get(self, route, **query):
        query.setdefault("token", "口令123")
        return urllib.request.urlopen(self.base + route + "?" + urllib.parse.urlencode(query), timeout=30)

    def test_chat_needs_the_password(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/chat/open", {"project": str(self.project), "dispatcher": "codex"}, token="猜的")
        self.assertEqual(ctx.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/chat/events", project=str(self.project), since=0, token="")
        self.assertEqual(ctx.exception.code, 403)

    def test_open_send_and_read_events_over_http(self):
        self.assertTrue(self._post("/api/chat/open", {"project": str(self.project), "dispatcher": "codex"})["ok"])
        seen, types = 0, []
        end = time.time() + 15
        sent = False
        while time.time() < end and "image" not in types:
            data = json.loads(self._get("/api/chat/events", project=str(self.project), since=seen, wait=2).read())
            for e in data["events"]:
                seen = e["seq"]
                types.append(e["type"])
            if "turn_done" in types and not sent:
                self._post("/api/chat/send", {"project": str(self.project), "text": "画一只猫"})
                sent = True
        self.assertIn("opening", types)
        self.assertIn("image", types)
        body = self._get("/api/chat/image", project=str(self.project), path=str(self.image)).read()
        self.assertTrue(body.startswith(b"\x89PNG"))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/chat/image", project=str(self.project), path="/etc/hosts")
        self.assertEqual(ctx.exception.code, 404)

    def test_terminal_only_dispatcher_gets_a_clear_answer(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/chat/open", {"project": str(self.project), "dispatcher": "custom:qwen"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("终端", json.loads(ctx.exception.read())["error"])

    def test_read_another_thread_without_switching_to_it(self):
        """切换前先读好内容，点的时候就不用等连接。"""
        s1 = self.hub.open(self.project, "codex", with_opening=False)
        wait_until(s1, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        first = s1.adapter.thread_id
        s1.send("第一段的问题")
        wait_until(s1, lambda e: e["type"] == "turn_done")
        time.sleep(0.05)
        s2 = self.hub.open(self.project, "codex", mode="fresh", with_opening=False)
        wait_until(s2, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        second = s2.adapter.thread_id
        self.assertNotEqual(first, second)

        events = s2.read_thread(first)
        self.assertEqual(s2.adapter.thread_id, second, "只是读，不能切过去")
        self.assertTrue(all(e.get("history") for e in events))
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertIn("第一段的问题", " ".join(str(e.get("text", "")) for e in events))

        data = json.loads(self._get("/api/chat/thread", project=str(self.project), id=first).read())
        self.assertEqual(len(data["events"]), len(events))

    def test_reading_an_unknown_thread_gives_nothing(self):
        s = self.hub.open(self.project, "codex", with_opening=False)
        wait_until(s, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        self.assertEqual(s.read_thread("没有这段"), [])

    def test_list_threads_and_switch_back_to_previous_thread(self):
        s1 = self.hub.open(self.project, "codex", with_opening=False)
        wait_until(s1, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        t1_id = s1.adapter.thread_id
        s1.send("第一段对话：检查项目bug")
        wait_until(s1, lambda e: e["type"] == "turn_done")

        time.sleep(0.05)
        s2 = self.hub.open(self.project, "codex", mode="fresh", with_opening=False)
        wait_until(s2, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        t2_id = s2.adapter.thread_id
        self.assertNotEqual(t1_id, t2_id)
        s2.send("第二段对话：增加新玩法")
        wait_until(s2, lambda e: e["type"] == "turn_done")

        data = json.loads(self._get("/api/chat/threads", project=str(self.project)).read())
        threads = data["threads"]
        self.assertEqual(len(threads), 2)
        self.assertEqual(threads[0]["id"], t2_id)
        self.assertTrue(threads[0]["current"])
        self.assertIn("第二段对话", threads[0]["title"])
        self.assertEqual(threads[1]["id"], t1_id)
        self.assertFalse(threads[1]["current"])
        self.assertIn("第一段对话", threads[1]["title"])

        # 从下拉菜单切回第一段对话
        self._post("/api/chat/open", {"project": str(self.project), "dispatcher": "codex",
                                      "mode": "continue", "opening": False, "thread_id": t1_id})
        s_back = self.hub.get(self.project)
        wait_until(s_back, lambda e: e["type"] == "status" and e["text"].startswith("已连上"))
        self.assertEqual(s_back.adapter.thread_id, t1_id)
        users = [e["text"] for e in s_back.events_since(0, 0) if e["type"] == "user"]
        self.assertIn("第一段对话：检查项目bug", users)


if __name__ == "__main__":
    unittest.main()
