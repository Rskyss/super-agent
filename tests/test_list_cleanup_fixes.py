"""第五批验收时查出的问题：列表取不到时不能清空、记住的列表不能被一次失败抹掉、提议的规则只写一处。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

from acceptance_helpers import ROOT, STATIC, ProjectCase, ScriptedClient, ServerCase, needs_node, run_node, wait_idle, wait_until
from collab.flow import read_flow
from collab.merge import _Stop, remove_copy
from desktop.chat import ChatHub, THREAD_LIST_FILE, ChatSession
from desktop.clerk import tasks as clerk_tasks
from desktop.clerk.desk import ClerkDesk
from desktop.dispatchers.codex import CodexDispatcher

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_codex_app_server.py")]


class Page(ProjectCase):
    def fetch_threads(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        body = js[js.index("function fetchThreads"):]
        return body[:body.index("function threadLoop")]

    def test_buttons_go_away_when_the_connection_drops(self):
        """连着时有删除图标，切到终端后列表变成记住的那份，图标要跟着消失。"""
        body = self.fetch_threads()
        self.assertIn("drawThreads()", body[:body.index("if (d.cached && threads.length) return;")])

    def test_archived_group_can_be_folded_again(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("showArchivedOpen = !showArchivedOpen", js)


class SavedList(ServerCase):
    def setUp(self):
        super().setUp()
        os.environ["FAKE_CODEX_DB"] = str(Path(self.tmp) / "db.json")
        self.hub = ChatHub(factories={"codex": lambda project, emit, background, marker: CodexDispatcher(
            project, emit, background=background, marker=marker, command=FAKE)})
        self.addCleanup(self.hub.close_all)
        self.collab.mkdir(exist_ok=True)

    def talk(self, text):
        session = self.hub.open(self.project, "codex", mode="fresh", with_opening=False)
        wait_until(lambda: any(e["type"] == "status" and e["text"].startswith("已连上") for e in session.events_since(0, 0)))
        session.send(text)
        wait_until(lambda: any(e["type"] == "turn_done" for e in session.events_since(0, 0)) and not session.busy)
        return session.adapter.thread_id

    def test_a_failed_listing_does_not_wipe_what_is_remembered(self):
        first = self.talk("第一段")
        self.hub.thread_list(self.project)
        session = self.hub.get(self.project)
        with mock.patch.object(session.adapter, "list_threads", side_effect=RuntimeError("Codex 出错")):
            listed = self.hub.thread_list(self.project)
        self.assertTrue(listed["cached"], "取不到就显示记住的，不能当成真的没有")
        self.assertEqual([t["id"] for t in listed["threads"]], [first])

    def test_an_empty_answer_never_wipes_what_is_remembered(self):
        """取回来是空，可能是 30 段全被外来对话占满，也可能 Codex 正好断了。一律不信，也不给删除。"""
        only = self.talk("唯一一段")
        self.hub.thread_list(self.project)
        session = self.hub.get(self.project)
        with mock.patch.object(session.adapter, "list_threads", return_value=[]):
            listed = self.hub.thread_list(self.project)
        self.assertTrue(listed["cached"])
        self.assertEqual([t["id"] for t in listed["threads"]], [only])
        saved = json.loads((Path(self.project) / THREAD_LIST_FILE).read_text(encoding="utf-8"))
        self.assertEqual([t["id"] for t in saved["threads"]], [only])

    def test_many_foreign_threads_do_not_wipe_the_list(self):
        """复查复现的场景：同一文件夹在 Codex 桌面版又开了 30 段以上，取回来的最新 30 段全是外来的。"""
        mine = self.talk("第一段")
        self.hub.thread_list(self.project)
        session = self.hub.get(self.project)
        db_path = Path(os.environ["FAKE_CODEX_DB"])
        db = json.loads(db_path.read_text(encoding="utf-8"))
        db["threads"][mine]["updated"] = 1
        for number in range(40):
            db["threads"][f"外来{number}"] = {
                "cwd": str(Path(self.project).resolve()), "updated": 1000 + number, "originator": "Codex Desktop",
                "turns": [{"id": "x", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "外来的话"}]}]}]}
        db_path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        session.adapter.thread_id = "还没存档的新对话"   # 当前对话也不在这 30 段里
        listed = self.hub.thread_list(self.project)
        self.assertTrue(listed["cached"], "没有删除图标")
        self.assertEqual([t["id"] for t in listed["threads"]], [mine])
        saved = json.loads((Path(self.project) / THREAD_LIST_FILE).read_text(encoding="utf-8"))
        self.assertEqual([t["id"] for t in saved["threads"]], [mine])

    def test_deleting_the_last_one_stays_deleted(self):
        only = self.talk("唯一一段")
        self.hub.thread_list(self.project)
        self.hub.delete_thread(self.project, only)
        self.assertEqual(self.hub.thread_list(self.project)["threads"], [])
        self.assertEqual(ChatHub(factories={}).thread_list(self.project)["threads"], [])


class OneRule(ProjectCase):
    def test_open_proposals_are_worked_out_in_one_place(self):
        folder = self.collab / "行政提议"
        folder.mkdir(parents=True)
        for pid, cid, state in (("p1", "a", "open"), ("p2", "b", "cancelled"), ("p3", "c", "open")):
            (folder / f"{pid}.json").write_text(json.dumps({"id": pid, "conversation": cid, "state": state}), encoding="utf-8")
        (folder / "坏的.json").write_text("{", encoding="utf-8")
        self.assertEqual(clerk_tasks.conversations_with_open(self.project), {"a", "c"})

    def test_desk_does_not_scan_the_folder_itself(self):
        desk = (ROOT / "desktop" / "clerk" / "desk.py").read_text(encoding="utf-8")
        self.assertNotIn("_open_in_conversation", desk)
        self.assertNotIn("clerk_tasks.FOLDER", desk)
        self.assertIn("clerk_tasks.cancel_open", desk)


class PlainWords(ServerCase):
    def test_archive_failure_is_in_plain_words(self):
        self.make_card(state="已合并")
        with mock.patch("desktop.server.set_archived", side_effect=OSError(13, "Permission denied")):
            status, answer = self.post("/api/clerk/card/archive", {"project": self.project, "card": "CARD-001", "archived": True})
        self.assertEqual(status, 409)
        self.assertNotIn("Permission", answer["error"])
        self.assertNotIn("Errno", answer["error"])


class ReviewCodex(SavedList):
    """独立复查查出的 Codex 模式问题。"""

    def add_foreign(self, marker):
        """同一个文件夹里，在 Codex 独立客户端聊的对话：内容提到了超级助手，所以会进列表。"""
        rollout = Path(self.tmp) / "外来.jsonl"
        rollout.write_text('{"name": "super-agent"}\n', encoding="utf-8")
        db_path = Path(os.environ["FAKE_CODEX_DB"])
        db = json.loads(db_path.read_text(encoding="utf-8"))
        db["threads"]["外来的"] = {
            "cwd": str(Path(self.project).resolve()), "updated": 1, "originator": "Codex Desktop", "path": str(rollout),
            "turns": [{"id": "x", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "在 Codex 里聊的"}]}]}]}
        db_path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        return db_path

    def test_connection_lost_means_not_connected(self):
        first = self.talk("第一段")
        self.hub.thread_list(self.project)
        session = self.hub.get(self.project)
        session.adapter.proc.kill()
        session.adapter.proc.wait()
        listed = self.hub.thread_list(self.project)
        self.assertTrue(listed["cached"], "连接断了要显示记住的列表，这样不会有删除图标")
        self.assertEqual([t["id"] for t in listed["threads"]], [first])
        with self.assertRaises(RuntimeError) as caught:
            self.hub.delete_thread(self.project, first)
        self.assertIn("还没连上", str(caught.exception))

    def test_a_failed_listing_is_not_called_gone(self):
        first = self.talk("第一段")
        session = self.hub.get(self.project)
        with mock.patch.object(session.adapter, "list_threads", side_effect=RuntimeError("Codex 出错")):
            with self.assertRaises(RuntimeError) as caught:
                self.hub.delete_thread(self.project, first)
        self.assertIn("没能删除", str(caught.exception))
        self.assertNotIn("Codex 出错", str(caught.exception))

    def test_foreign_thread_shows_but_cannot_be_deleted(self):
        mine = self.talk("第一段")
        db_path = self.add_foreign(self.hub.get(self.project).adapter.marker)
        rows = {t["id"]: t for t in self.hub.thread_list(self.project)["threads"]}
        self.assertTrue(rows[mine]["deletable"])
        self.assertFalse(rows["外来的"]["deletable"])
        with self.assertRaises(LookupError):
            self.hub.delete_thread(self.project, "外来的")
        self.assertIn("外来的", json.loads(db_path.read_text(encoding="utf-8"))["threads"])

    def test_foreign_thread_stays_safe_after_opening_it(self):
        """打开成当前对话后也不能删。自动接上最近一段时，用户甚至不用点。"""
        self.talk("第一段")
        db_path = self.add_foreign(self.hub.get(self.project).adapter.marker)
        session = self.hub.open(self.project, "codex", mode="continue", with_opening=False, thread_id="外来的")
        wait_until(lambda: getattr(session.adapter, "thread_id", None) == "外来的", message="没能打开外来对话")
        row = next(t for t in self.hub.thread_list(self.project)["threads"] if t["id"] == "外来的")
        self.assertTrue(row["current"])
        self.assertFalse(row["deletable"])
        with self.assertRaises(LookupError):
            self.hub.delete_thread(self.project, "外来的")
        self.assertIn("外来的", json.loads(db_path.read_text(encoding="utf-8"))["threads"])

    def test_unknown_source_cannot_be_deleted(self):
        from desktop.dispatchers.codex import deletable_thread
        self.assertTrue(deletable_thread({"originator": "super-agent"}))
        for source in (None, "", "Codex Desktop", "codex-tui", "super-agent-spike"):
            self.assertFalse(deletable_thread({"originator": source}), source)

    def test_old_answer_cannot_put_it_back(self):
        first, second = self.talk("第一段"), self.talk("第二段")
        self.hub.thread_list(self.project)
        session = self.hub.get(self.project)
        old = session.list_threads()
        self.hub.delete_thread(self.project, first)
        with mock.patch.object(session.adapter, "list_threads", return_value=old):
            listed = self.hub.thread_list(self.project)
        self.assertEqual([t["id"] for t in listed["threads"]], [second])
        saved = json.loads((Path(self.project) / THREAD_LIST_FILE).read_text(encoding="utf-8"))
        self.assertEqual([t["id"] for t in saved["threads"]], [second])

    def test_service_says_not_connected(self):
        first = self.talk("第一段")
        self.server.hub = self.hub
        session = self.hub.get(self.project)
        session.adapter.proc.kill()
        session.adapter.proc.wait()
        status, answer = self.post("/api/chat/delete", {"project": self.project, "thread_id": first})
        self.assertEqual(status, 409)
        self.assertIn("还没连上", answer["error"])


class ReviewClerk(ProjectCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient()
        self.desk = ClerkDesk(lambda: self.client)

    def talk(self):
        cid = self.desk.send(self.project, "", "私密主题")
        wait_idle(self.desk, self.project, cid)
        return cid

    def folder(self):
        return self.collab / "行政对话"

    def test_late_writes_leave_nothing(self):
        cid = self.talk()
        conv = self.desk._conversation(self.project, cid)
        self.desk.delete(self.project, cid)
        self.desk._keep_reply(conv, "迟到的回答")
        self.desk._name(conv, self.client, "迟到的回答")
        self.assertFalse((self.folder() / f"{cid}.jsonl").exists())
        self.assertNotIn(cid, json.loads((self.folder() / "索引.json").read_text(encoding="utf-8")))

    def test_card_conversation_is_safe_even_if_the_index_breaks(self):
        self.make_card(state="开发中", plan=True)
        cid = self.desk.open_card(self.project, "CARD-001")["conversation"]
        self.desk.send(self.project, cid, "这张卡的话")
        wait_idle(self.desk, self.project, cid)
        (self.folder() / "索引.json").write_text("{坏的", encoding="utf-8")
        fresh = ClerkDesk(lambda: self.client)
        self.assertEqual(fresh.list(self.project)["threads"], [], "卡片的对话不能混进主对话")
        with self.assertRaises(LookupError):
            fresh.delete(self.project, cid)
        self.assertTrue((self.folder() / f"{cid}.jsonl").exists())

    def test_opening_while_deleting_leaves_no_live_copy(self):
        """打开和删除撞在一起：读完文件发现已经删了，就不能放回内存。"""
        cid = self.talk()
        fresh = ClerkDesk(lambda: self.client)
        real = fresh._read_lines

        def delete_midway(path):
            lines = real(path)
            (self.folder() / f"{cid}.jsonl").unlink()
            return lines

        with mock.patch.object(fresh, "_read_lines", side_effect=delete_midway):
            self.assertIsNone(fresh._conversation(self.project, cid))
        self.assertFalse(fresh.notify(self.project, cid, "迟到的通知"))
        self.assertFalse((self.folder() / f"{cid}.jsonl").exists())


class ReviewCopy(ServerCase):
    def test_slow_git_is_noted_and_does_not_raise(self):
        cdir = self.make_card(state="已放弃")
        self.make_copy()
        with mock.patch("collab.merge._git", side_effect=_Stop("git 超时")):
            remove_copy(self.project, "CARD-001")
        self.assertTrue(any("改好的版本没能删掉" in e.get("summary", "") for e in read_flow(cdir)))

    def test_refused_undo_keeps_the_copy(self):
        self.make_card(state="待验收", plan=True)
        copy = self.make_copy()
        with mock.patch("desktop.server.undo_interrupted", side_effect=RuntimeError("没能退回")):
            status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "discard"})
        self.assertNotEqual(status, 200)
        self.assertTrue(copy.exists())
        self.assertEqual(self.state(), "待验收")


@needs_node()
class ReviewRules(ProjectCase):
    def check(self, body):
        proc = run_node("const C = require('./desktop/static/chat.js'); const N = require('./desktop/static/nav.js'); const assert = require('assert');" + body)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_after_thread_deleted(self):
        self.check("""
        const f = (s) => C.afterThreadDeleted('a', s);
        assert.deepStrictEqual(f({ clerk: true, current: 'a', main: 'a', viewingCard: false }), { clearMain: true, open: 'blank' });
        assert.deepStrictEqual(f({ clerk: true, current: 'card', main: 'a', viewingCard: true }), { clearMain: true, open: null }, '看卡时不打断');
        assert.deepStrictEqual(f({ clerk: true, current: 'b', main: 'b', viewingCard: false }), { clearMain: false, open: null });
        assert.deepStrictEqual(f({ clerk: false, current: 'a' }), { clearMain: false, open: 'fresh' });
        assert.deepStrictEqual(f({ clerk: false, current: 'b' }), { clearMain: false, open: null });
        """)

    def test_answer_rules(self):
        self.check("""
        const ids = l => l.map(t => t.id);
        assert.strictEqual(N.threadsFromAnswer({}, {}, null), null, '没有列表就不动');
        assert.deepStrictEqual(ids(N.threadsFromAnswer({ threads: [{ id: 'a' }, { id: 'b' }] }, { a: true }, null)), ['b'], '迟到的回答带不回删掉的');
        const marked = N.threadsFromAnswer({ threads: [{ id: 'a' }, { id: 'b' }], current: 'a' }, {}, 'b');
        assert.deepStrictEqual(marked.map(t => !!t.current), [false, true], '刚点的先标上');
        """)

    def test_one_at_a_time(self):
        proc = run_node("""
        const N = require('./desktop/static/nav.js'); const assert = require('assert');
        (async () => {
          const locks = {}; let runs = 0;
          let finish; const slow = () => { runs++; return new Promise(ok => { finish = ok; }); };
          const first = N.oneAtATime(locks, 'k', slow);
          assert.strictEqual(N.oneAtATime(locks, 'k', slow), null, '进行中再点要被挡住');
          assert.strictEqual(runs, 1);
          finish('好'); assert.strictEqual(await first, '好');
          assert.ok(!locks.k, '成功后能再点');
          await assert.rejects(N.oneAtATime(locks, 'k', () => Promise.reject(new Error('坏了'))), /坏了/);
          assert.ok(!locks.k, '失败后能再点');
          const held = N.oneAtATime(locks, 'h', () => Promise.resolve(1), 30);
          await held; assert.ok(locks.h, '冷却期内还挡着');
          await new Promise(ok => setTimeout(ok, 60)); assert.ok(!locks.h, '冷却完能再点');
          console.log('OK');
        })().catch(e => { console.error(e); process.exit(1); });
        """)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_cannot_delete_a_foreign_thread(self):
        self.check("""
        assert.strictEqual(N.canDelete({ id: 'a', deletable: false }, false), false);
        assert.strictEqual(N.canDelete({ id: 'a', deletable: true }, false), true);
        """)


class ReviewPage(ProjectCase):
    def nav(self):
        return (STATIC / "nav.js").read_text(encoding="utf-8")

    def test_blank_chat_after_deleting_never_spends_quota(self):
        """方案写的是空白对话。项目说明没写时，带开场白会自动发一句话。"""
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        body = chat[chat.index("threadDeleted:"):]
        body = body[:body.index("openThread:")]
        self.assertIn('switchSession("fresh", false, null)', body)
        self.assertIn("ChatCore.afterThreadDeleted", body)

    def test_service_unreachable_is_in_plain_words(self):
        self.assertIn("没连上本机服务", self.nav())

    def test_clicks_go_through_the_guard(self):
        js = self.nav()
        self.assertIn('NavCore.oneAtATime(locks, "archive"', js)
        self.assertIn("NavCore.oneAtATime(locks, gone", js)
        self.assertIn("if (locks[gone]) return;", js, "确认框前也要挡，不然会弹第二个")

    def test_search_shows_a_plain_title_not_a_fold_button(self):
        js = self.nav()
        self.assertIn('q.trim() ? \'<div class="gh">', js)

    def test_row_without_action_keeps_its_time(self):
        css = (STATIC / "nav.css").read_text(encoding="utf-8")
        self.assertIn(".row:has(.rowact):hover .tm", css)
        self.assertNotIn(".row:hover .tm", css)

    def test_time_label_follows_the_date(self):
        self.assertIn("toDateString()", self.nav()[self.nav().index("function drawThreads"):self.nav().index("function drawTabs")])

    def test_fold_row_says_its_state(self):
        self.assertEqual(self.nav().count("aria-expanded"), 2)
