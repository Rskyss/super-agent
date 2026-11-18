"""去掉调度员 D3、D4：团队检测、本机服务的团队接口、右栏取数、行政的团队工具与提示词里不再有“调度员”这个岗位。

老项目（配置里还留着 dispatcher）必须照常能读、能存、能显示。
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import ScriptedClient, ServerCase, tool_call, wait_idle
from collab import check
from collab.config import load_team
from collab.workspace import init_collab
from desktop.clerk import desk as clerk_desk, tasks as clerk_tasks
from desktop.clerk.tools import TOOLS, run_tool, team_incomplete_hint
from desktop.view import card_view

OLD_TEAM = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}


def fake_check(tool, models, timeout=None):
    return {"ok": True, "status": "ok", "reason": "通了", "how": "", "seconds": 0}


class CheckHasThreeRoles(unittest.TestCase):
    def test_role_lists_have_no_dispatcher(self):
        self.assertEqual(tuple(check.ROLE_ORDER), ("tester", "developer", "reviewer"))
        self.assertNotIn("dispatcher", check.ROLE_NAMES)

    def test_check_team_reports_three_roles_and_ignores_a_legacy_dispatcher(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(check, "check_tool", side_effect=fake_check):
            out = check.check_team(tmp, team=dict(OLD_TEAM))
        self.assertEqual([r["role"] for r in out], ["tester", "developer", "reviewer"])


class Base(ServerCase):
    def write_config(self, data: dict) -> None:
        init_collab(self.project)
        (self.collab / "配置.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TeamEndpoint(Base):
    def test_fresh_project_has_three_roles_options_and_is_not_configured(self):
        init_collab(self.project)
        status, data = self.get("/api/team", project=self.project, token="口令")
        self.assertEqual(status, 200)
        self.assertNotIn("dispatcher", data["team"])
        self.assertNotIn("dispatcher", data["options"])
        self.assertTrue({"worker", "reviewer"} <= set(data["options"]))
        self.assertFalse(data["team"]["configured"])

    def test_old_project_with_dispatcher_loads_and_stays_configured(self):
        self.write_config({"team": OLD_TEAM})
        status, data = self.get("/api/team", project=self.project, token="口令")
        self.assertEqual(status, 200)
        self.assertNotIn("dispatcher", data["team"])
        self.assertTrue(data["team"]["configured"])
        self.assertEqual(data["problem"], "")

    def test_broken_team_section_falls_back_without_a_dispatcher_key(self):
        self.write_config({"team": "oops"})
        status, data = self.get("/api/team", project=self.project, token="口令")
        self.assertEqual(status, 200)
        self.assertTrue(data["problem"])
        self.assertNotIn("dispatcher", data["team"])
        self.assertFalse(data["team"]["configured"])

    def test_save_three_roles_works_and_the_old_dispatcher_is_kept_and_not_overwritten(self):
        self.write_config({"team": OLD_TEAM, "role_models": {
            "dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "low"}}})
        body = {"project": self.project,
                "team": {"dispatcher": "agy", "tester": "codex", "developer": "agy", "reviewer": "none"},
                "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "low"},
                                "tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}}}
        status, answer = self.post("/api/team/save", body)
        self.assertEqual(status, 200, answer)
        saved = json.loads((self.collab / "配置.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["team"], {"tester": "codex", "developer": "agy", "reviewer": "none", "dispatcher": "codex"})
        self.assertEqual(saved["role_models"]["dispatcher"], {"tool": "codex", "model": "gpt-6.1-sol", "effort": "low"},
                         "老调度员的设置原样留着，传进来的值不起作用")
        self.assertEqual(saved["role_models"]["tester"]["effort"], "high")

    def test_save_without_dispatcher_in_the_request_is_fine(self):
        init_collab(self.project)
        status, answer = self.post("/api/team/save", {
            "project": self.project, "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"}})
        self.assertEqual(status, 200, answer)
        self.assertTrue(load_team(self.project)["configured"])


class RightPanelWithOldConfig(Base):
    def test_card_view_does_not_break_on_an_old_config_or_a_legacy_dispatcher_argument(self):
        self.write_config({"team": OLD_TEAM})
        for kwargs in ({}, {"dispatcher": "codex"}):
            view = card_view(self.project, **kwargs)
            self.assertTrue(view["stations"])

    def test_card_view_with_a_card_and_old_config(self):
        self.write_config({"team": OLD_TEAM})
        self.make_card()
        view = card_view(self.project, "CARD-001")
        self.assertEqual(view["card"]["id"], "CARD-001")


class ClerkHasNoDispatcher(Base):
    def test_read_team_lists_three_roles_only(self):
        self.write_config({"team": OLD_TEAM})
        answer = run_tool(self.project, "read_team", {})
        self.assertNotIn("调度员", answer)
        for words in ("写测试", "写代码", "复查"):
            self.assertIn(words, answer)

    def test_incomplete_team_hint_never_mentions_the_dispatcher(self):
        init_collab(self.project)
        self.assertNotIn("调度员", team_incomplete_hint(self.project))

    def test_team_change_tool_description_has_no_dispatcher(self):
        spec = next(t for t in TOOLS if t["function"]["name"] == "propose_team_change")
        self.assertNotIn("dispatcher", json.dumps(spec, ensure_ascii=False))

    def test_proposing_to_change_the_dispatcher_is_refused(self):
        init_collab(self.project)
        client = ScriptedClient([{"tool_calls": [tool_call("propose_team_change",
                                                           {"role": "dispatcher", "tool": "codex"})]},
                                 {"text": "好的"}])
        self.server.clerk = client
        desk = self.server.desk
        cid = desk.send(self.project, "", "把调度员换成 Codex")
        events = wait_idle(desk, self.project, cid)
        self.assertEqual([e for e in events if e["type"] == "proposal"], [])
        refusal = json.dumps(client.calls[1][0][-1], ensure_ascii=False)
        self.assertNotIn("调度员", refusal, "拒绝的话里也不再把调度员当成一个岗位")

    def test_clerk_prompts_do_not_name_a_dispatcher(self):
        self.assertNotIn("调度员", clerk_desk.ROLE)
        self.assertNotIn("调度员", clerk_tasks._TASK_SHOWN)
        self.assertIn("点确认就建卡", clerk_desk.ROLE)
        self.assertIn("点确认就建卡", clerk_tasks._TASK_SHOWN)


if __name__ == "__main__":
    unittest.main()


class DshSingleRoleSaveKeepsTheLegacyDispatcher(unittest.TestCase):
    """复查第 2 轮中等 4：超级玩家设置页只存一个执行员，老调度员的工具和岗位模型不能跟着丢。"""

    def test_saving_one_role_leaves_the_legacy_dispatcher_alone(self):
        import tempfile
        from collab.config import legacy_dispatcher
        from desktop import dsh_settings
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            (project / ".collab" / "配置.json").write_text(json.dumps({
                "codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                "team": {"dispatcher": "codex", "tester": "codex", "developer": "agy", "reviewer": "codex"},
                "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"}}}),
                encoding="utf-8")
            before = legacy_dispatcher(project)
            dsh_settings.save_role(project, "developer", "codex", "gpt-6.1-sol", "high")
            self.assertEqual(legacy_dispatcher(project), before)
            self.assertEqual(before, ("codex", {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"}))
