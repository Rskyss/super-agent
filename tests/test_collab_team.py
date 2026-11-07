"""团队配置：谁当调度员、谁写测试/写代码/复查。存在项目的 .collab/配置.json，协作包、窗口、面板都读这一份。"""
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collab.agents import AgentResult
from collab.cli import main
from collab.config import ConfigError, load_team, run_tools, save_team
from collab.judge import PASS, Verdict
from collab.workspace import init_collab


class TeamConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        init_collab(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_nothing_is_configured(self):
        team = load_team(self.project)
        self.assertEqual((team["tester"], team["developer"], team["reviewer"]), ("codex", "cursor", "agy"))
        self.assertIsNone(team["dispatcher"])  # 调度员必须用户亲手选，没有默认
        self.assertFalse(team["configured"])

    def test_saved_team_is_read_back_and_other_settings_kept(self):
        cfg = self.project / ".collab" / "配置.json"
        cfg.write_text(json.dumps({"codex": "gpt-x", "project_profile_confirmed": True}), encoding="utf-8")
        save_team(self.project, {"dispatcher": "agy", "tester": "agy", "developer": "codex", "reviewer": "none"})
        team = load_team(self.project)
        self.assertEqual(team["dispatcher"], "agy")
        self.assertEqual(team["reviewer"], "none")
        self.assertTrue(team["configured"])
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["codex"], "gpt-x")
        self.assertTrue(data["project_profile_confirmed"])

    def test_custom_command_can_dispatch_but_not_work(self):
        save_team(self.project, {"dispatcher": "custom:qwen --yolo", "tester": "codex", "developer": "cursor", "reviewer": "agy"})
        self.assertEqual(load_team(self.project)["dispatcher"], "custom:qwen --yolo")
        with self.assertRaises(ConfigError):
            save_team(self.project, {"dispatcher": "codex", "tester": "custom:qwen", "developer": "cursor", "reviewer": "agy"})

    def test_claude_is_not_offered(self):
        for role in ("dispatcher", "tester", "developer", "reviewer"):
            team = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy", role: "claude"}
            with self.assertRaises(ConfigError):
                save_team(self.project, team)

    def test_reviewer_may_be_none_but_others_may_not(self):
        with self.assertRaises(ConfigError):
            save_team(self.project, {"dispatcher": "codex", "tester": "none", "developer": "cursor", "reviewer": "agy"})

    def test_broken_saved_value_is_reported(self):
        (self.project / ".collab" / "配置.json").write_text(json.dumps({"team": {"developer": "word"}}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_team(self.project)

    def test_run_tools_prefers_command_line_then_team_then_default(self):
        save_team(self.project, {"dispatcher": "codex", "tester": "agy", "developer": "codex", "reviewer": "none"})
        self.assertEqual(run_tools(self.project), {"test": "agy", "dev": "codex", "review": None})
        self.assertEqual(run_tools(self.project, dev="cursor", review="agy"), {"test": "agy", "dev": "cursor", "review": "agy"})


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RunUsesTeamTest(unittest.TestCase):
    """开工时没写工具参数，就按团队配置派活（假执行者，不会真的启动任何 AI）。"""

    def test_run_dispatches_to_the_configured_team(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            project.mkdir()
            _git(project, "init", "-q")
            _git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
            buf = io.StringIO()

            def cli(*argv, **kw):
                with redirect_stdout(buf), redirect_stderr(buf):
                    return main(list(argv), **kw)

            cli("init", str(project))
            (project / "docs").mkdir()
            (project / "docs" / "项目说明.md").write_text("- 怎么测试：true\n", encoding="utf-8")
            cli("confirm-profile", str(project))
            req = Path(tmp) / "req.md"
            req.write_text("加问候\n\n## 允许改动的范围\n- a.txt\n", encoding="utf-8")
            cli("new-card", str(project), "CARD-001", "--requirement", str(req))
            (project / ".collab" / "cards" / "CARD-001" / "方案.md").write_text("两步", encoding="utf-8")
            cli("approve", str(project), "CARD-001")
            save_team(project, {"dispatcher": "codex", "tester": "agy", "developer": "codex", "reviewer": "cursor"})

            calls = []

            def fake_ask(adapter, prompt, cwd, **kw):
                calls.append(adapter)
                if len(calls) == 1:  # 测试员要真的交出测试
                    (Path(cwd) / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="结论：通过", attempts=1)

            code = cli("run", str(project), "CARD-001", "--test-command", "true",
                       ask_fn=fake_ask, judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"))
            self.assertEqual(code, 0, buf.getvalue())
            self.assertEqual(calls, ["agy", "codex", "cursor"])  # 写测试、写代码、复查


if __name__ == "__main__":
    unittest.main()
