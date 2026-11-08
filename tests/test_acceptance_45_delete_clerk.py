"""45 号单：行政模式删除主对话。真删，删了不能回来；正在回复的不能删；卡片自己的对话不能删。"""
import json
import threading

from acceptance_helpers import (STATIC, SWIFT, ProjectCase, ScriptedClient, ServerCase, needs_node, run_node,
                                tool_call, wait_idle)
from desktop.clerk.desk import ClerkDesk

TASK = {"title": "按钮改蓝色", "do": "首页按钮改蓝", "not_do": "不改别的页面", "done_when": "打开首页按钮是蓝的"}


def proposing():
    return [{"tool_calls": [tool_call("propose_task", TASK)]}, {"text": "你看这样行吗"}]


class Hold:
    """让假模型停在回复中，测“正在回复”。"""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self):
        self.entered.set()
        self.release.wait(5)
        return {"text": "说完了"}


class DeskCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient()
        self.desk = ClerkDesk(lambda: self.client)

    def talk(self, text="你好", cid=""):
        cid = self.desk.send(self.project, cid, text)
        wait_idle(self.desk, self.project, cid)
        return cid

    def ids(self, desk=None):
        return [t["id"] for t in (desk or self.desk).list(self.project)["threads"]]

    def folder(self):
        return self.collab / "行政对话"


class Delete(DeskCase):
    def test_gone_from_the_list_and_the_disk(self):
        keep, gone = self.talk("留着"), self.talk("删掉")
        self.desk.delete(self.project, gone)
        self.assertEqual(self.ids(), [keep])
        self.assertFalse((self.folder() / f"{gone}.jsonl").exists())
        self.assertTrue((self.folder() / f"{keep}.jsonl").exists())
        index = json.loads((self.folder() / "索引.json").read_text(encoding="utf-8"))
        self.assertNotIn(gone, index)

    def test_does_not_come_back_after_restart(self):
        gone = self.talk("删掉")
        self.desk.delete(self.project, gone)
        self.assertEqual(self.ids(ClerkDesk(lambda: self.client)), [])

    def test_does_not_come_back_after_talking_elsewhere(self):
        gone, other = self.talk("删掉"), self.talk("别的")
        self.desk.delete(self.project, gone)
        self.talk("接着聊", other)
        self.assertEqual(self.ids(), [other])
        self.assertFalse((self.folder() / f"{gone}.jsonl").exists())

    def test_late_notice_does_not_write_it_back(self):
        gone = self.talk("删掉")
        self.desk.delete(self.project, gone)
        self.assertFalse(self.desk.notify(self.project, gone, "「改按钮」做完了，等你确认。"))
        self.assertFalse((self.folder() / f"{gone}.jsonl").exists())
        self.assertEqual(self.ids(), [])

    def test_late_title_does_not_bring_it_back(self):
        gone = self.talk("删掉")
        self.desk.delete(self.project, gone)
        self.desk._save_index(self.project, gone, title="迟到的标题")
        self.assertEqual(self.ids(ClerkDesk(lambda: self.client)), [])

    def test_opening_it_again_finds_nothing(self):
        gone = self.talk("删掉")
        self.desk.delete(self.project, gone)
        self.assertFalse(self.desk.events(self.project, gone, 0, wait=0)["open"])
        with self.assertRaises(RuntimeError):
            self.desk.send(self.project, gone, "还在吗")
        self.assertFalse((self.folder() / f"{gone}.jsonl").exists())

    def test_current_mark_is_cleared(self):
        gone = self.talk("删掉")
        self.desk.focus(self.project, gone)
        self.desk.delete(self.project, gone)
        self.assertIsNone(self.desk.list(self.project)["current"])

    def test_unknown_or_bad_id(self):
        for cid in ("0" * 32, "../../etc/passwd", ""):
            with self.assertRaises(LookupError):
                self.desk.delete(self.project, cid)

    def test_replying_cannot_be_deleted(self):
        hold = Hold()
        self.client.steps = [hold]
        cid = self.desk.send(self.project, "", "说点什么")
        self.assertTrue(hold.entered.wait(5))
        with self.assertRaises(RuntimeError):
            self.desk.delete(self.project, cid)
        self.assertTrue((self.folder() / f"{cid}.jsonl").exists())
        self.assertTrue(self.desk.list(self.project)["threads"][0]["busy"])
        hold.release.set()
        wait_idle(self.desk, self.project, cid)
        self.assertFalse(self.desk.list(self.project)["threads"][0]["busy"])
        self.desk.delete(self.project, cid)
        self.assertEqual(self.ids(), [])

    def test_card_conversation_cannot_be_deleted(self):
        self.make_card(state="开发中", plan=True)
        cid = self.desk.open_card(self.project, "CARD-001")["conversation"]
        self.talk("这张卡的话", cid)
        with self.assertRaises(LookupError):
            self.desk.delete(self.project, cid)
        self.assertTrue((self.folder() / f"{cid}.jsonl").exists())

    def test_card_still_shows_after_its_source_is_deleted(self):
        from desktop.view import list_cards
        gone = self.talk("提需求")
        self.make_card(state="开发中", plan=True, source=gone)
        self.desk.delete(self.project, gone)
        self.assertEqual([c["id"] for c in list_cards(self.project)], ["CARD-001"])
        self.assertEqual(self.state(), "开发中")


class Proposals(DeskCase):
    def proposal(self, cid):
        for path in (self.collab / "行政提议").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("conversation") == cid:
                return data
        return None

    def test_open_proposal_is_flagged_and_cancelled(self):
        self.client.steps = proposing()
        cid = self.talk("把按钮改蓝")
        self.assertTrue(self.desk.list(self.project)["threads"][0]["pending"])
        self.desk.delete(self.project, cid)
        self.assertEqual(self.proposal(cid)["state"], "cancelled")

    def test_no_proposal_is_not_flagged(self):
        self.talk("聊天")
        self.assertFalse(self.desk.list(self.project)["threads"][0]["pending"])

    def test_confirmed_proposal_and_its_card_stay(self):
        self.client.steps = proposing()
        cid = self.talk("把按钮改蓝")
        pid = self.proposal(cid)["id"]
        card = self.desk.decide_proposal(self.project, cid, pid, "confirm")["card"]
        self.assertFalse(self.desk.list(self.project)["threads"][0]["pending"])
        self.desk.delete(self.project, cid)
        self.assertEqual(self.proposal(cid)["state"], "confirmed")
        self.assertTrue((self.collab / "cards" / card).is_dir())


class Service(ServerCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient()
        self.server.clerk = self.client
        self.desk = self.server.desk

    def talk(self):
        cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, cid)
        return cid

    def test_delete(self):
        cid = self.talk()
        status, answer = self.post("/api/clerk/delete", {"project": self.project, "conversation": cid})
        self.assertEqual((status, answer), (200, {"ok": True}))
        self.assertEqual(self.get("/api/clerk/list", project=self.project, token="口令")[1]["threads"], [])

    def test_needs_the_password(self):
        cid = self.talk()
        self.assertEqual(self.post("/api/clerk/delete", {"project": self.project, "conversation": cid}, token="错的")[0], 403)
        self.assertEqual(len(self.desk.list(self.project)["threads"]), 1)

    def test_bad_requests_have_plain_messages(self):
        self.assertEqual(self.post("/api/clerk/delete", {"project": "/没有这个", "conversation": "a"})[0], 400)
        status, answer = self.post("/api/clerk/delete", {"project": self.project, "conversation": "0" * 32})
        self.assertEqual(status, 404)
        self.assertIn("已经不在了", answer["error"])

    def test_replying_is_refused(self):
        hold = Hold()
        self.client.steps = [hold]
        cid = self.desk.send(self.project, "", "说点什么")
        self.assertTrue(hold.entered.wait(5))
        status, answer = self.post("/api/clerk/delete", {"project": self.project, "conversation": cid})
        hold.release.set()
        self.assertEqual(status, 409)
        self.assertIn("还在回复", answer["error"])
        wait_idle(self.desk, self.project, cid)


@needs_node()
class PageRules(ProjectCase):
    def check(self, body):
        proc = run_node("const N = require('./desktop/static/nav.js'); const assert = require('assert');" + body)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_can_delete(self):
        self.check("""
        assert.strictEqual(N.canDelete({ id: 'a' }, false), true);
        assert.strictEqual(N.canDelete({ id: 'a', busy: true }, false), false, '正在回复');
        assert.strictEqual(N.canDelete({ id: 'a' }, true), false, '还没连上，列表是上次记住的');
        """)

    def test_confirm_words(self):
        self.check("""
        var base = N.deleteConfirm({ id: 'a' }, true);
        assert.ok(base.indexOf('删除后找不回来') >= 0 && base.indexOf('已提出的任务不受影响') >= 0);
        assert.ok(base.indexOf('Codex') < 0 && base.indexOf('提议') < 0);
        assert.ok(N.deleteConfirm({ id: 'a', pending: true }, true).indexOf('没确认的提议') >= 0);
        assert.ok(N.deleteConfirm({ id: 'a' }, false).indexOf('Codex 里的这段记录也会一起删除') >= 0);
        """)

    def test_deleted_never_comes_back_from_an_old_answer(self):
        self.check("""
        var list = [{ id: 'a' }, { id: 'b' }];
        assert.deepStrictEqual(N.withoutDeleted(list, { a: true }), [{ id: 'b' }]);
        assert.deepStrictEqual(N.withoutDeleted(list, {}), list);
        """)


class Wiring(ProjectCase):
    """页面和窗口接上了。真正好不好用要在真实窗口里走查。"""

    def test_nav(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        for words in ("/api/clerk/delete", "/api/chat/delete", "superagent://thread-deleted", "window.confirm",
                      "NavCore.deleteConfirm", "NavCore.canDelete", "NavCore.withoutDeleted", "X-Chat-Token",
                      "data-delete", "删除这段对话"):
            self.assertIn(words, js)

    def test_delete_button_is_not_inside_the_row_button(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        row = js[js.index("data-thread="):]
        row = row[:row.index("join(")]
        self.assertGreater(row.index("data-delete"), row.index("</button>"), "按钮里不能再放按钮")

    def test_window_tells_the_chat_page(self):
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn('case "thread-deleted":', swift)
        self.assertIn("SuperAgentChat.threadDeleted", swift)
        self.assertIn("threadDeleted:", (STATIC / "chat.js").read_text(encoding="utf-8"))

    def test_hover_shows_the_button(self):
        css = (STATIC / "nav.css").read_text(encoding="utf-8")
        self.assertIn(".rowact", css)
        self.assertIn(":focus-within", css, "键盘切到这一行也要能看到")
