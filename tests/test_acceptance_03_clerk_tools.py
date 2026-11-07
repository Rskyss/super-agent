"""03 号单：行政能查项目和进度，只读，一次一问。"""
import json
import threading
from pathlib import Path

from acceptance_helpers import ProjectCase, ScriptedClient, tool_call, wait_idle, wait_until
from collab.flow import record
from desktop.clerk import desk as desk_module
from desktop.clerk.desk import ClerkDesk
from desktop.clerk.tools import TOOLS, run_tool

FIVE = {"read_project_profile", "read_ledger", "list_cards", "read_card", "read_team"}


def names():
    return {t["function"]["name"] for t in TOOLS}


class ToolsTest(ProjectCase):
    def setUp(self):
        super().setUp()
        docs = Path(self.project) / "docs"
        docs.mkdir()
        (docs / "项目说明.md").write_text("这是一个做视频的项目", encoding="utf-8")
        (docs / "开发进度跟踪.md").write_text("\n".join(f"第{i}行" for i in range(200)), encoding="utf-8")
        (Path(self.project) / ".env").write_text("SECRET=不能被读到", encoding="utf-8")
        self.cdir = self.make_card(state="需Claude介入", title="改按钮")
        record(self.cdir, "转人工", src="程序", dst="调度员", summary="测试一直不过")

    def test_tool_list(self):
        self.assertTrue(FIVE <= names())
        for name in names():
            for bad in ("write", "delete", "run_", "shell", "exec", "push", "merge"):
                self.assertNotIn(bad, name)

    def test_no_tool_takes_a_file_path(self):
        for tool in TOOLS:
            for key in tool["function"].get("parameters", {}).get("properties", {}):
                self.assertNotIn(key.lower(), ("path", "file", "filename", "cwd", "command"))

    def test_profile_and_ledger(self):
        self.assertIn("做视频", run_tool(self.project, "read_project_profile", {}))
        tail = run_tool(self.project, "read_ledger", {"lines": 3})
        self.assertIn("第199行", tail)
        self.assertNotIn("第190行", tail)
        self.assertLessEqual(run_tool(self.project, "read_ledger", {"lines": 9999}).count("第"), 80)

    def test_missing_profile_is_said_plainly(self):
        (Path(self.project) / "docs" / "项目说明.md").unlink()
        self.assertIn("没有", run_tool(self.project, "read_project_profile", {}))

    def test_cards(self):
        listed = run_tool(self.project, "list_cards", {})
        self.assertIn("CARD-001", listed)
        self.assertIn("改按钮", listed)
        detail = run_tool(self.project, "read_card", {"card_id": "CARD-001"})
        self.assertIn("需Claude介入", detail)
        self.assertIn("测试一直不过", detail)

    def test_bad_card_ids_read_nothing(self):
        for card in ("../..", "CARD-999", "a/b", ""):
            self.assertNotIn("SECRET", run_tool(self.project, "read_card", {"card_id": card}))

    def test_unknown_tool_and_bad_arguments_never_raise(self):
        self.assertIn("没有这个工具", run_tool(self.project, "delete_all", {}))
        self.assertIsInstance(run_tool(self.project, "read_card", {}), str)
        self.assertIsInstance(run_tool(self.project, "read_ledger", {"lines": "很多"}), str)

    def test_results_are_limited(self):
        (Path(self.project) / "docs" / "项目说明.md").write_text("字" * 20000, encoding="utf-8")
        self.assertLessEqual(len(run_tool(self.project, "read_project_profile", {})), 6000)

    def test_secrets_never_show(self):
        for name in FIVE:
            self.assertNotIn("不能被读到", run_tool(self.project, name, {"card_id": "CARD-001"}))


class LoopTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.make_card(state="开发中", title="改按钮")

    def talk(self, steps, text="进度怎么样"):
        self.client = ScriptedClient(steps)
        self.desk = ClerkDesk(lambda: self.client)
        cid = self.desk.send(self.project, "", text)
        return cid, wait_idle(self.desk, self.project, cid)

    def test_checks_then_answers(self):
        cid, events = self.talk([{"tool_calls": [tool_call("list_cards")]}, {"text": "有一张卡在做"}])
        self.assertTrue(any(e["type"] == "activity" and e["label"].startswith("读 ") for e in events))
        self.assertTrue(any(e["type"] == "message" and e["text"] == "有一张卡在做" for e in events))
        tool_messages = [m for m in self.client.calls[1][0] if m["role"] == "tool"]
        self.assertEqual(tool_messages[0]["tool_call_id"], "c1")
        self.assertIn("CARD-001", tool_messages[0]["content"])
        assistant = [m for m in self.client.calls[1][0] if m.get("tool_calls")]
        self.assertEqual(assistant[0]["tool_calls"][0]["function"]["name"], "list_cards")

    def test_tools_are_offered(self):
        self.talk([{"text": "你好"}], "你好")
        self.assertTrue(self.client.calls[0][1])

    def test_tool_details_are_not_saved_in_the_conversation(self):
        cid, _ = self.talk([{"tool_calls": [tool_call("list_cards")]}, {"text": "好"}])
        saved = (self.collab / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("CARD-001", saved)

    def test_rounds_are_limited(self):
        steps = [{"tool_calls": [tool_call("list_cards")]} for _ in range(desk_module.MAX_TOOL_ROUNDS)]
        _, events = self.talk(steps + [{"text": "查完了"}])
        self.assertEqual(desk_module.MAX_TOOL_ROUNDS, 5)
        self.assertEqual(len(self.client.calls), 6)
        self.assertFalse(self.client.calls[-1][1])
        self.assertTrue(any(e["type"] == "message" and e["text"] == "查完了" for e in events))

    def test_bad_arguments_are_told_back(self):
        self.talk([{"tool_calls": [tool_call("read_card", "{坏的")]}, {"text": "好"}])
        tool_messages = [m for m in self.client.calls[1][0] if m["role"] == "tool"]
        self.assertIn("参数看不懂", tool_messages[0]["content"])

    def test_stop_ends_the_tool_loop(self):
        entered, release = threading.Event(), threading.Event()

        def second():
            entered.set()
            release.wait(5)
            return {"tool_calls": [tool_call("list_cards")]}

        self.client = ScriptedClient([{"tool_calls": [tool_call("list_cards")]}, second, {"text": "不该出现"}])
        desk = ClerkDesk(lambda: self.client)
        cid = desk.send(self.project, "", "查一下")
        wait_until(entered.is_set)
        desk.stop(self.project, cid)
        release.set()
        import time
        time.sleep(0.3)
        self.assertEqual(len(self.client.calls), 2)
        self.assertFalse(desk.events(self.project, cid)["busy"])


class RulesTest(ProjectCase):
    def test_role_sets_the_rules(self):
        client = ScriptedClient([{"text": "好"}])
        desk = ClerkDesk(lambda: client)
        cid = desk.send(self.project, "", "你好")
        wait_idle(desk, self.project, cid)
        system = client.calls[0][0][0]["content"]
        for words in ("一次只问一个问题", "推荐答案", "不能改代码", "不编造"):
            self.assertIn(words, system)
        self.assertNotIn("现在还看不到", system)
