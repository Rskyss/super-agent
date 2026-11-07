"""05 号单：“变成任务？”确认框。模型只能提议，点确认才建卡。"""
import json
import threading
from pathlib import Path

from acceptance_helpers import STATIC, ProjectCase, ScriptedClient, ServerCase, needs_node, run_node, tool_call, wait_idle, wait_until
from collab.tasks import TaskError, new_card
from desktop.clerk.desk import ClerkDesk

TASK = {"title": "按钮改蓝色", "do": "首页按钮改蓝", "not_do": "不改别的页面", "done_when": "打开首页按钮是蓝的"}


def proposing(**changes):
    return [{"tool_calls": [tool_call("propose_task", dict(TASK, **changes))]}, {"text": "你看这样行吗"}]


class ProposeTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient(proposing())
        self.desk = ClerkDesk(lambda: self.client)

    def proposals(self):
        folder = self.collab / "行政提议"
        return [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")] if folder.exists() else []

    def test_proposal_shows_and_makes_no_card(self):
        cid = self.desk.send(self.project, "", "把按钮改蓝")
        events = wait_idle(self.desk, self.project, cid)
        shown = [e for e in events if e["type"] == "proposal"]
        self.assertEqual(len(shown), 1)
        for key, value in TASK.items():
            self.assertEqual(shown[0][key], value)
        self.assertEqual(shown[0]["state"], "open")
        self.assertEqual(self.proposals()[0]["conversation"], cid)
        self.assertFalse((self.collab / "cards").exists() and list((self.collab / "cards").iterdir()))

    def test_missing_field_asks_first(self):
        self.client.steps = proposing(not_do=" ")
        cid = self.desk.send(self.project, "", "把按钮改蓝")
        events = wait_idle(self.desk, self.project, cid)
        self.assertEqual([e for e in events if e["type"] == "proposal"], [])
        tool_messages = [m for m in self.client.calls[1][0] if m["role"] == "tool"]
        self.assertIn("还缺", tool_messages[0]["content"])

    def test_new_proposal_replaces_the_open_one(self):
        self.client.steps = proposing() + proposing(title="第二个")
        cid = self.desk.send(self.project, "", "改")
        wait_idle(self.desk, self.project, cid)
        self.desk.send(self.project, cid, "换一个")
        wait_idle(self.desk, self.project, cid)
        self.assertEqual(sorted(p["state"] for p in self.proposals()), ["open", "replaced"])

    def test_proposal_survives_reopening_and_stays_in_place(self):
        cid = self.desk.send(self.project, "", "把按钮改蓝")
        wait_idle(self.desk, self.project, cid)
        fresh = ClerkDesk(lambda: self.client)
        events = fresh.events(self.project, cid, 0, wait=0)["events"]
        order = [e["type"] for e in events]
        self.assertLess(order.index("user"), order.index("proposal"))
        self.assertLess(order.index("proposal"), order.index("message"))

    def test_proposal_line_is_not_sent_to_the_model(self):
        cid = self.desk.send(self.project, "", "把按钮改蓝")
        wait_idle(self.desk, self.project, cid)
        fresh_client = ScriptedClient([{"text": "好"}])
        fresh = ClerkDesk(lambda: fresh_client)
        fresh.send(self.project, cid, "继续")
        wait_idle(fresh, self.project, cid)
        self.assertEqual({m["role"] for m in fresh_client.calls[0][0]} - {"system", "user", "assistant"}, set())

    def test_role_explains_proposals(self):
        cid = self.desk.send(self.project, "", "把按钮改蓝")
        wait_idle(self.desk, self.project, cid)
        self.assertIn("propose_task", json.dumps(self.client.calls[0][1], ensure_ascii=False))
        self.assertIn("用户点确认才算", self.client.calls[0][0][0]["content"])


class DecisionTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient(proposing())
        self.server.clerk = self.client
        self.desk = self.server.desk
        self.cid = self.desk.send(self.project, "", "把按钮改蓝")
        events = wait_idle(self.desk, self.project, self.cid)
        self.pid = next(e["id"] for e in events if e["type"] == "proposal")

    def decide(self, decision, pid=None):
        return self.post("/api/clerk/proposal", {"project": self.project, "conversation": self.cid,
                                                 "id": pid or self.pid, "decision": decision})

    def cards(self):
        folder = self.collab / "cards"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_cancel_makes_nothing(self):
        status, _ = self.decide("cancel")
        self.assertEqual(status, 200)
        self.assertEqual(self.cards(), [])
        events = self.desk.events(self.project, self.cid)["events"]
        self.assertTrue(any(e["type"] == "proposal_done" and e["state"] == "cancelled" for e in events))
        self.assertTrue(any(e["type"] == "message" and "先不做" in e["text"] for e in events))

    def test_confirm_makes_the_card_without_the_model(self):
        calls = len(self.client.calls)
        status, answer = self.decide("confirm")
        self.assertEqual(status, 200)
        self.assertEqual(self.cards(), ["CARD-001"])
        self.assertEqual(len(self.client.calls), calls)
        cdir = self.collab / "cards" / "CARD-001"
        text = (cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 按钮改蓝色\n"))
        for heading in ("## 要做什么", "## 不做什么", "## 怎么算做完"):
            self.assertIn(heading, text)
        self.assertEqual(json.loads((cdir / "来源.json").read_text(encoding="utf-8")), {"conversation": self.cid})
        self.assertEqual(json.loads((cdir / "已汇报.json").read_text(encoding="utf-8")), {"code": "new"})
        self.assertEqual(self.state(), "待对齐")
        self.assertEqual(self.flow_kinds()[0], "需求")
        events = self.desk.events(self.project, self.cid)["events"]
        self.assertTrue(any(e["type"] == "proposal_done" and e.get("card") == "CARD-001" for e in events))
        self.assertTrue(any(e["type"] == "message" and "CARD-001" in e["text"] for e in events))

    def test_double_confirm_makes_one_card(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.decide("confirm"))) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(self.cards(), ["CARD-001"])

    def test_cancelled_cannot_be_confirmed(self):
        self.decide("cancel")
        status, _ = self.decide("confirm")
        self.assertEqual(status, 409)
        self.assertEqual(self.cards(), [])

    def test_unknown_and_unauthorised_requests(self):
        status, _ = self.decide("confirm", pid="0" * 32)
        self.assertIn(status, (404, 409))
        status, _ = self.post("/api/clerk/proposal", {"project": self.project, "conversation": self.cid, "id": self.pid,
                                                      "decision": "confirm"}, token="错的")
        self.assertEqual(status, 403)
        self.assertEqual(self.cards(), [])

    def test_folder_without_git_keeps_the_proposal_open(self):
        import shutil
        shutil.rmtree(Path(self.project) / ".git")
        status, answer = self.decide("confirm")
        self.assertEqual(status, 409)
        self.assertIn("不是 git 项目", answer["error"])
        status, _ = self.decide("cancel")
        self.assertEqual(status, 200)


class NewCardTest(ProjectCase):
    def test_numbers_follow_on(self):
        first = new_card(self.project, "甲", "做", "不做", "完成")
        self.assertEqual(first, "CARD-001")
        self.make_card("CARD-007")
        self.assertEqual(new_card(self.project, "乙", "做", "不做", "完成"), "CARD-008")

    def test_connects_the_project_when_needed(self):
        self.assertFalse(self.collab.exists())
        new_card(self.project, "甲", "做", "不做", "完成")
        self.assertTrue((self.collab / "任务板.md").exists())

    def test_no_duplicate_numbers(self):
        found = []
        threads = [threading.Thread(target=lambda: found.append(new_card(self.project, "甲", "做", "不做", "完成")))
                   for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(set(found)), 6)

    def test_needs_git(self):
        import shutil
        shutil.rmtree(Path(self.project) / ".git")
        with self.assertRaises(TaskError):
            new_card(self.project, "甲", "做", "不做", "完成")


class PageTest(ProjectCase):
    @needs_node()
    def test_chat_model_tracks_the_proposal(self):
        script = """
        const C = require('./desktop/static/chat.js'); const m = new C.Model();
        m.apply({seq:1,type:'proposal',id:'p',kind:'task',title:'<b>标题</b>',do:'做',not_do:'不做',done_when:'完成',state:'open',card:''});
        const b = m.blocks.find(x => x.kind === 'proposal');
        if (!b || b.state !== 'open') throw new Error('没有确认框');
        m.apply({seq:2,type:'proposal_done',id:'p',state:'confirmed',card:'CARD-001'});
        if (b.state !== 'confirmed' || b.card !== 'CARD-001') throw new Error('状态没更新');
        console.log('OK');
        """
        proc = run_node(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_page_has_the_buttons(self):
        js = (STATIC / "chat.js").read_text(encoding="utf-8")
        for words in ("变成任务？", "/api/clerk/proposal", "先不做", "怎么算做完"):
            self.assertIn(words, js)
