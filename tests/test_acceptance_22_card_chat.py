"""14 号单：每张卡有自己的对话，和主对话完全分开。用户只和行政说话。"""
import hashlib
import json
import os
from pathlib import Path

from acceptance_helpers import STATIC, SWIFT, ScriptedClient, needs_node, run_node, tool_call, wait_idle, wait_until, write_script
from collab.flow import record
from desktop.clerk.desk import ClerkDesk
from desktop.clerk.tasks import TaskWatcher
from test_acceptance_11_fixes import Case

TASK_ARGS = {"title": "新任务", "do": "做", "not_do": "不做", "done_when": "完成"}


class ChatCase(Case):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient()
        self.server.clerk = self.client
        self.desk = self.server.desk

    def card(self, state="开发中", **kwargs):
        cdir = self.make_card(state=state, plan=True, title="改按钮", **kwargs)
        (cdir / "开工.json").write_text('{"test_command": "true"}', encoding="utf-8")
        need = cdir / "需求卡.md"
        need.write_text("# 改按钮\n\n## 要做什么\n改颜色\n\n## 不做什么\n不改别处\n\n## 怎么算做完\n变蓝\n\n"
                        "## 允许改动的范围\n- src/app.py\n", encoding="utf-8")
        return cdir

    def building(self, **kwargs):
        cdir = self.card(state="开发中", **kwargs)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=os.getpid())
        return cdir

    def open(self, card="CARD-001"):
        status, answer = self.post("/api/clerk/card/open", {"project": self.project, "card": card})
        self.assertEqual(status, 200, answer)
        return answer["conversation"]

    def say(self, cid, text):
        cid = self.desk.send(self.project, cid, text)
        return cid, wait_idle(self.desk, self.project, cid)

    def boxes(self, events, kind=None):
        return [e for e in events if e["type"] == "proposal" and (kind is None or e["kind"] == kind)]

    def events(self, cid):
        return self.desk.events(self.project, cid, 0, wait=0)["events"]

    def words(self, cid):
        return [e.get("text") for e in self.events(cid) if e["type"] == "message"]

    def tool_names(self, call):
        return {t["function"]["name"] for t in call[1] or []}

    def pending(self):
        path = self.collab / "cards" / "CARD-001" / "待转达.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def decide(self, cid, pid, decision):
        return self.post("/api/clerk/proposal", {"project": self.project, "conversation": cid, "id": pid, "decision": decision})

    def propose_revise(self, text="按钮再大一点"):
        self.client.steps = [{"text": "", "tool_calls": [tool_call("propose_revise", {"text": text})]}, {"text": "好的。"}]


class Opening(ChatCase):
    def test_same_card_same_conversation(self):
        self.card()
        cid = self.open()
        self.assertEqual(cid, hashlib.md5("card:CARD-001".encode()).hexdigest())
        self.assertEqual(self.open(), cid)

    def test_answer_carries_the_task_information(self):
        self.card()
        _, answer = self.post("/api/clerk/card/open", {"project": self.project, "card": "CARD-001"})
        self.assertEqual((answer["title"], answer["do"], answer["not_do"], answer["done_when"]),
                         ("改按钮", "改颜色", "不改别处", "变蓝"))
        self.assertEqual(answer["phase"]["code"], self.phase()["code"])

    def test_bad_and_missing_cards(self):
        self.card()
        self.assertEqual(self.post("/api/clerk/card/open", {"project": self.project, "card": "../外面"})[0], 400)
        self.assertEqual(self.post("/api/clerk/card/open", {"project": self.project, "card": "CARD-404"})[0], 404)

    def test_needs_the_password(self):
        self.card()
        self.assertEqual(self.post("/api/clerk/card/open", {"project": self.project, "card": "CARD-001"}, token="错的")[0], 403)

    def test_card_chat_is_not_in_the_main_list(self):
        self.card()
        self.open()
        main, _ = self.say("", "主对话的话")
        ids = [t["id"] for t in self.desk.list(self.project)["threads"]]
        self.assertEqual(ids, [main])

    def test_survives_a_restart(self):
        self.card()
        cid = self.open()
        self.say(cid, "这张卡怎么样了")
        fresh = ClerkDesk(lambda: self.client)
        kinds = [e.get("text") for e in fresh.events(self.project, cid, 0, wait=0)["events"]]
        self.assertIn("这张卡怎么样了", kinds)


class Separated(ChatCase):
    def test_words_do_not_cross(self):
        self.card()
        main, _ = self.say("", "主对话的话")
        cid = self.open()
        self.say(cid, "任务对话的话")
        self.say(main, "主对话第二句")
        mine = [json.dumps(c[0], ensure_ascii=False) for c in self.client.calls]
        self.assertNotIn("主对话的话", mine[1])
        self.assertIn("任务对话的话", mine[1])
        self.assertNotIn("任务对话的话", mine[2])
        self.assertNotIn("任务对话的话", json.dumps(self.events(main), ensure_ascii=False))
        self.assertNotIn("主对话", json.dumps(self.events(cid), ensure_ascii=False))

    def test_card_chat_knows_its_card_and_the_rule(self):
        self.building()
        cid = self.open()
        self.say(cid, "现在怎么样")
        system = self.client.calls[0][0][0]["content"]
        for words in ("CARD-001", "改按钮", "进行中", "不能直接指挥"):
            self.assertIn(words, system)

    def test_main_chat_is_unchanged(self):
        self.card()
        self.say("", "你好")
        self.assertNotIn("不能直接指挥", self.client.calls[0][0][0]["content"])

    def test_each_chat_has_its_own_tools(self):
        self.card()
        main, _ = self.say("", "主")
        self.say(self.open(), "卡")
        self.assertIn("propose_task", self.tool_names(self.client.calls[0]))
        self.assertNotIn("propose_revise", self.tool_names(self.client.calls[0]))
        self.assertIn("propose_revise", self.tool_names(self.client.calls[1]))
        self.assertNotIn("propose_task", self.tool_names(self.client.calls[1]))
        self.assertNotIn("propose_team_change", self.tool_names(self.client.calls[1]))

    def test_tool_of_the_other_chat_does_nothing(self):
        self.card(state="待验收")
        self.client.steps = [{"text": "", "tool_calls": [tool_call("propose_task", TASK_ARGS)]}, {"text": "好"}]
        _, events = self.say(self.open(), "再做一个")
        self.assertEqual(self.boxes(events), [])
        self.client.steps = [{"text": "", "tool_calls": [tool_call("propose_revise", {"text": "改"})]}, {"text": "好"}]
        _, events = self.say("", "改一下")
        self.assertEqual(self.boxes(events), [])

    def test_proposals_do_not_replace_each_other(self):
        self.card(state="待验收")
        self.client.steps = [{"text": "", "tool_calls": [tool_call("propose_task", TASK_ARGS)]}, {"text": "好"}]
        main, events = self.say("", "新任务")
        main_box = self.boxes(events)[0]
        self.propose_revise()
        self.say(self.open(), "还要改")
        self.assertEqual(self.decide(main, main_box["id"], "cancel")[0], 200)


class WhileBuilding(ChatCase):
    def test_question_is_answered_right_away(self):
        self.building()
        self.client.steps = [{"text": "正在写代码。"}]
        cid = self.open()
        self.say(cid, "到哪了")
        self.assertIn("正在写代码。", self.words(cid))
        self.assertEqual(self.state(), "开发中")

    def test_change_is_noted_and_does_not_interrupt(self):
        self.building()
        self.propose_revise()
        _, events = self.say(self.open(), "按钮再大一点")
        self.assertEqual(self.boxes(events), [])
        self.assertEqual(self.pending(), ["按钮再大一点"])
        self.assertEqual(self.state(), "开发中")
        tool_answer = [m for m in self.client.calls[1][0] if m["role"] == "tool"][0]["content"]
        self.assertIn("已记下", tool_answer)

    def test_note_survives_a_restart(self):
        self.building()
        self.propose_revise()
        self.say(self.open(), "改")
        self.assertEqual(self.pending(), ["按钮再大一点"])
        self.assertEqual(ClerkDesk(lambda: self.client).list(self.project)["threads"], [])


class AfterTheRound(ChatCase):
    def noted(self):
        cdir = self.building()
        self.propose_revise()
        cid = self.open()
        self.say(cid, "改")
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 0}), encoding="utf-8")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        self.watcher = TaskWatcher(self.desk)
        return cdir, cid

    def test_box_appears_once_in_the_card_chat(self):
        _, cid = self.noted()
        self.watcher.tick(self.project)
        self.watcher.tick(self.project)
        boxes = self.boxes(self.events(cid), "revise")
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["text"], "按钮再大一点")

    def test_confirm_starts_the_new_round(self):
        _, cid = self.noted()
        self.watcher.tick(self.project)
        box = self.boxes(self.events(cid), "revise")[0]
        self.assertEqual(self.decide(cid, box["id"], "confirm")[0], 200)
        self.assertEqual(self.pending(), [])
        self.assertIn("按钮再大一点", (self.collab / "cards" / "CARD-001" / "需求卡.md").read_text(encoding="utf-8"))
        self.assertIn("已交给开发，做完再告诉你。", self.words(cid))
        wait_until(lambda: "追加要求" in self.flow_kinds(), 5)

    def test_cancel_clears_it(self):
        _, cid = self.noted()
        self.watcher.tick(self.project)
        box = self.boxes(self.events(cid), "revise")[0]
        self.decide(cid, box["id"], "cancel")
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.state(), "待验收")
        self.watcher.tick(self.project)
        self.assertEqual(len(self.boxes(self.events(cid), "revise")), 1)

    def test_nothing_is_offered_while_still_building(self):
        self.building()
        self.propose_revise()
        cid = self.open()
        self.say(cid, "改")
        TaskWatcher(self.desk).tick(self.project)
        self.assertEqual(self.boxes(self.events(cid)), [])


class AtReview(ChatCase):
    def test_box_right_away(self):
        self.card(state="待验收")
        self.propose_revise()
        cid = self.open()
        _, events = self.say(cid, "还要改")
        self.assertEqual(len(self.boxes(events, "revise")), 1)
        self.assertEqual(self.state(), "待验收", "确认前不动")

    def test_plan_stage_box_rewrites_the_plan(self):
        self.plan()
        self.card(state="待对齐")
        self.propose_revise("不要改配色")
        cid = self.open()
        _, events = self.say(cid, "方案改一下")
        self.assertEqual(self.decide(cid, self.boxes(events, "revise")[0]["id"], "confirm")[0], 200)
        self.assertIn("已让 Codex 重写方案。", self.words(cid))
        wait_until(lambda: self.flow_kinds()[-1] == "方案", 10, "方案没有重写")

    def test_confirm_after_the_stage_changed_is_refused(self):
        cdir = self.card(state="待验收")
        self.propose_revise()
        cid = self.open()
        _, events = self.say(cid, "还要改")
        box = self.boxes(events, "revise")[0]
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "已合并", "rework": 0}), encoding="utf-8")
        self.assertEqual(self.decide(cid, box["id"], "confirm")[0], 409)
        self.assertEqual(self.state(), "已合并")

    def test_box_stays_in_place_after_reopening(self):
        self.card(state="待验收")
        self.propose_revise()
        cid = self.open()
        self.say(cid, "还要改")
        again = ClerkDesk(lambda: self.client).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual(self.boxes(again, "revise")[0]["text"], "按钮再大一点")


class Progress(ChatCase):
    def test_only_news_from_after_opening(self):
        cdir = self.building()
        record(cdir, "判定", src="程序", summary="打开前的判定")
        cid = self.open()
        record(cdir, "开跑", src="调度员", dst="程序", summary="第二次开工", pid=os.getpid())
        record(cdir, "派活", src="调度员", dst="开发员", summary="内部派活")
        record(cdir, "判定", src="程序", summary="第 1 轮：测试全部通过")
        watcher = TaskWatcher(self.desk)
        watcher.tick(self.project)
        watcher.tick(self.project)
        said = self.words(cid)
        self.assertEqual(said, ["第二次开工", "第 1 轮：测试全部通过"])

    def test_restart_does_not_repeat(self):
        cdir = self.building()
        cid = self.open()
        record(cdir, "判定", src="程序", summary="一条进展")
        TaskWatcher(self.desk).tick(self.project)
        fresh = ClerkDesk(lambda: self.client)
        TaskWatcher(fresh).tick(self.project)
        self.assertEqual([e.get("text") for e in fresh.events(self.project, cid, 0, wait=0)["events"]].count("一条进展"), 1)

    def test_never_opened_card_is_left_alone(self):
        cdir = self.building()
        record(cdir, "判定", src="程序", summary="进展")
        TaskWatcher(self.desk).tick(self.project)
        self.assertFalse((cdir / "已播报.json").exists())
        self.assertEqual(self.desk.list(self.project)["threads"], [])

    def test_main_chat_only_gets_the_one_line(self):
        main, _ = self.say("", "主对话")
        cdir = self.building(source=main, reported="running")
        cid = self.open()
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 0}), encoding="utf-8")
        record(cdir, "判定", src="程序", summary="第 1 轮：测试全部通过")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="施工结束")
        TaskWatcher(self.desk).tick(self.project)
        self.assertIn("「改按钮」做完了，等你验收。", self.words(main))
        self.assertNotIn("第 1 轮：测试全部通过", self.words(main))
        self.assertIn("第 1 轮：测试全部通过", self.words(cid))


class Order(ChatCase):
    def script(self):
        self.client.steps = [{"text": "我先整理一下。", "tool_calls": [tool_call("propose_task", TASK_ARGS)]},
                             {"text": "你看下面的确认框。"}]

    def sequence(self, events):
        keep = []
        for event in events:
            if event["type"] == "proposal":
                keep.append("框")
            elif event["type"] == "message":
                keep.append(event["text"])
        return keep

    def test_box_stays_between_the_words_live(self):
        self.script()
        _, events = self.say("", "帮我做个事")
        self.assertEqual(self.sequence(events), ["我先整理一下。", "框", "你看下面的确认框。"])
        items = [e["item"] for e in events if e["type"] == "message"]
        self.assertNotEqual(items[0], items[1])

    def test_box_stays_between_the_words_after_reopening(self):
        self.script()
        cid, _ = self.say("", "帮我做个事")
        again = ClerkDesk(lambda: self.client).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual(self.sequence(again), ["我先整理一下。", "框", "你看下面的确认框。"])


class Window(Case):
    def test_window_opens_card_chat_only_with_the_clerk(self):
        swift = SWIFT.read_text(encoding="utf-8")
        for words in ("/api/clerk/card/open", "SuperAgentChat.openCard", "SuperAgentChat.closeCard"):
            self.assertIn(words, swift)
        start = swift.index("func selectCard")
        self.assertIn("clerkReady", swift[start:start + 800])

    def test_page_parts(self):
        html = (STATIC / "chat.html").read_text(encoding="utf-8")
        script = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn('id="cardHead"', html)
        self.assertIn('id="cardInfo"', html)
        for words in ("openCard", "closeCard", "还要修改？"):
            self.assertIn(words, script)

    @needs_node()
    def test_task_information_rows(self):
        done = run_node("const C = require(" + json.dumps(str(STATIC / "chat.js")) + ");" + """
          const rows = C.cardInfoRows({ do: '改颜色', not_do: '', done_when: '变蓝' });
          const want = [{ label: '要做什么', text: '改颜色' }, { label: '怎么算做完', text: '变蓝' }];
          if (JSON.stringify(rows) !== JSON.stringify(want)) throw new Error(JSON.stringify(rows));
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
