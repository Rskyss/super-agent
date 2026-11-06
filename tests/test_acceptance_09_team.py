"""09 号单：行政提议改团队设置，确认后才生效，不偷偷运行检测。"""
import json

from acceptance_helpers import STATIC, ScriptedClient, ServerCase, tool_call, wait_idle, write_script
from collab.config import load_team
from collab.workspace import init_collab
from desktop.clerk.tools import TOOLS, run_tool


def proposing(role="developer", tool="codex"):
    return [{"tool_calls": [tool_call("propose_team_change", {"role": role, "tool": tool})]}, {"text": "你看这样行吗"}]


class TeamTest(ServerCase):
    def setUp(self):
        super().setUp()
        init_collab(self.project)
        self.client = ScriptedClient(proposing())
        self.server.clerk = self.client
        self.desk = self.server.desk

    def propose(self):
        cid = self.desk.send(self.project, "", "写代码换成 Codex")
        events = wait_idle(self.desk, self.project, cid)
        return cid, events

    def decide(self, cid, pid, decision):
        return self.post("/api/clerk/proposal", {"project": self.project, "conversation": cid, "id": pid, "decision": decision})

    def test_tools_exist(self):
        names = {t["function"]["name"] for t in TOOLS}
        self.assertTrue({"list_installed_tools", "propose_team_change"} <= names)

    def test_installed_tools_are_found_without_running_them(self):
        marker = self.bin / "ran"
        write_script(self.bin / "agy", f'touch "{marker}"\n')
        answer = run_tool(self.project, "list_installed_tools", {})
        for words in ("Codex", "Cursor", "agy"):
            self.assertIn(words, answer)
        self.assertFalse(marker.exists())
        self.assertFalse(self.codex_log.exists())

    def test_nothing_changes_until_confirmed(self):
        before = load_team(self.project)["developer"]
        cid, events = self.propose()
        shown = next(e for e in events if e["type"] == "proposal")
        self.assertEqual((shown["kind"], shown["role"], shown["tool"], shown["before"]), ("team", "developer", "codex", before))
        self.assertEqual(load_team(self.project)["developer"], before)

    def test_confirm_saves_through_the_team_settings(self):
        cid, events = self.propose()
        pid = next(e["id"] for e in events if e["type"] == "proposal")
        status, answer = self.decide(cid, pid, "confirm")
        self.assertEqual(status, 200, answer)
        self.assertEqual(load_team(self.project)["developer"], "codex")
        _, shown = self.get("/api/team", project=self.project, token="口令")
        self.assertEqual(shown["team"]["developer"], "codex")

    def test_cancel_saves_nothing(self):
        before = load_team(self.project)["developer"]
        cid, events = self.propose()
        pid = next(e["id"] for e in events if e["type"] == "proposal")
        self.decide(cid, pid, "cancel")
        self.assertEqual(load_team(self.project)["developer"], before)

    def test_bad_choice_is_refused_at_proposal(self):
        for role, tool in (("developer", "none"), ("老板", "codex"), ("developer", "写代码的人")):
            self.client.steps = proposing(role, tool)
            cid = self.desk.send(self.project, "", "换人")
            events = wait_idle(self.desk, self.project, cid)
            self.assertEqual([e for e in events if e["type"] == "proposal" and e["role"] == role], [])

    def test_reviewer_can_be_turned_off(self):
        self.client.steps = proposing("reviewer", "none")
        cid, events = self.propose()
        pid = next(e["id"] for e in events if e["type"] == "proposal")
        self.decide(cid, pid, "confirm")
        self.assertEqual(load_team(self.project)["reviewer"], "none")

    def test_no_connection_check_is_run(self):
        cid, events = self.propose()
        pid = next(e["id"] for e in events if e["type"] == "proposal")
        self.decide(cid, pid, "confirm")
        self.assertEqual(self.server.checks, {})
        self.assertFalse(self.codex_log.exists())

    def test_page_and_rules(self):
        self.propose()
        self.assertIn("团队没配好", self.client.calls[0][0][0]["content"])
        self.assertIn("改团队设置？", (STATIC / "chat.js").read_text(encoding="utf-8"))
