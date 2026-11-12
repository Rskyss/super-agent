"""流程修理：Codex 独立复查第 2 轮查出的问题，逐条复现后补的测试。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab.dispatch import cursor_permissions
from collab.flow import record
from collab.phase import card_phase
from collab.state import Card
from test_flowfix_14_review_round1 import CMD, GitBase, git

ROOT = Path(__file__).resolve().parent.parent
TEAM = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "codex"}


class NewProductSubfolder(GitBase):
    def test_new_subfolder_under_product_folder_is_undone(self):
        """N1：写测试的在 src/ 下新建子文件夹放产品代码，也要撤"""
        def tester(wt):
            (wt / "src" / "newpkg").mkdir()
            (wt / "src" / "newpkg" / "rogue.py").write_text("x = 1\n", encoding="utf-8")
            (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
        self.run_with(tester)
        self.assertFalse((self.p / "src" / "newpkg" / "rogue.py").exists())
        self.assertNotIn("rogue", (self.cdir / "测试文件.json").read_text(encoding="utf-8"))


class ExistingCursorSettingUnstaged(GitBase):
    def test_project_own_setting_restored_in_index_too(self):
        """N3：项目自己提交过 .cursor/cli.json，临时放行后被暂存，还原时暂存区也要还原"""
        (self.p / ".cursor").mkdir()
        (self.p / ".cursor" / "cli.json").write_text('{"permissions": {"allow": ["Shell(ls)"]}}', encoding="utf-8")
        git(self.p, "add", ".cursor/cli.json")
        git(self.p, "commit", "-qm", "own setting")
        restore = cursor_permissions(self.p, CMD)
        git(self.p, "add", ".cursor/cli.json")
        restore()
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.p, capture_output=True,
                                text=True).stdout
        self.assertNotIn(".cursor", staged)


class SaveAllOrNothing(unittest.TestCase):
    def test_bad_tool_model_saves_nothing(self):
        """N4：整家模型写错（强度不认识）时也不能把团队存一半"""
        from test_flowfix_15_team_round1 import Server
        case = Server("test_draft_kept_in_project")
        case.setUp()
        try:
            status, _ = case.post("/api/team/save", {"project": str(case.project), "team": dict(TEAM, developer="agy"),
                                                     "models": {"codex_effort": "very-high"}})
            self.assertEqual(status, 400)
            data = json.loads((case.project / ".collab" / "配置.json").read_text(encoding="utf-8"))
            self.assertEqual(data["team"]["developer"], "cursor")
        finally:
            case.tearDown()


class DefaultCheckUsesRoleModels(unittest.TestCase):
    def test_check_without_overrides_uses_saved_role_models(self):
        """N5（本轮改出的回退）：不传任何模型时，检测要用存着的岗位设置"""
        from collab import check
        from collab.config import save_role_models
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / ".collab").mkdir()
            (p / ".collab" / "配置.json").write_text(json.dumps(
                {"team": TEAM, "codex": "generic-model", "codex_effort": "high"}), encoding="utf-8")
            save_role_models(p, {"tester": {"tool": "codex", "model": "role-model", "effort": "low"}})
            seen = []

            def fake(tool, models, timeout=None):
                seen.append((models.get("codex"), models.get("codex_effort")))
                return {"ok": True, "status": "ok", "reason": "通了", "how": "", "seconds": 0}
            with mock.patch.object(check, "check_tool", side_effect=fake):
                check.check_team(p, team=TEAM, roles=["tester"])
        self.assertEqual(seen, [("role-model", "low")])


class OldCardsKeepAcceptButton(GitBase):
    def test_old_review_stop_without_marker(self):
        """N6（回退）：更新前就因复查不通过停下的卡，没有新标记，按旧的原话认"""
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", dst="调度员", summary="复查员（codex）复查不通过，需要调度员看原因")
        self.assertIn("accept_issues", card_phase(self.p, "CARD-001")["actions"])


class PageRaceGuards(unittest.TestCase):
    def test_team_and_title_line_guards(self):
        """N7、N8、N9：慢请求和乱序"""
        team = (ROOT / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        restore = team.split("function restoreDraft()")[1].split("window.SuperAgentTeam")[0]
        self.assertIn("rowNow(r) === rowAtLoad(r)", restore, "N7：恢复前用户已经改过的行不动")
        self.assertIn("draftChain", team, "N8：暂存一次一次排队写，后写的不会被先写的盖掉")
        chat = (ROOT / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertIn("nowOpen", chat.split("function showNow()")[1].split("showNow();")[0],
                      "N9：同一张卡重新打开，旧那次的结果也要丢")


if __name__ == "__main__":
    unittest.main()
