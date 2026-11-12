"""流程修理第 13、14 条（试跑记录 ①）：每个岗位各自选模型和思考强度。

68b 前用户配团队时发现：同一家工具当几个岗位，模型和强度只有一份，改一行别的跟着变；调度员没法单独调低。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab.agents import AgentResult
from collab.config import load_role_models, models_for, run_models, save_role_models, save_team
from collab.dispatch import run_card
from collab.judge import PASS, Verdict
from collab.state import Card

TEAM = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "codex"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()
        (self.project / ".collab" / "配置.json").write_text(json.dumps(
            {"team": TEAM, "codex": "gpt-6.1-sol", "codex_effort": "xhigh", "cursor": "auto"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()


class Storage(Base):
    def test_old_config_fills_every_role(self):
        got = load_role_models(self.project)
        self.assertEqual(got["dispatcher"], {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"})
        self.assertEqual(got["developer"], {"tool": "cursor", "model": "auto"})

    def test_each_role_keeps_its_own(self):
        save_role_models(self.project, {
            "dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
            "tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"},
            "reviewer": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"},
        })
        got = load_role_models(self.project)
        self.assertEqual(got["dispatcher"]["effort"], "medium")
        self.assertEqual(got["tester"]["effort"], "high")
        self.assertEqual(got["reviewer"]["effort"], "xhigh")
        self.assertEqual(models_for(self.project, "tester", "codex")["codex_effort"], "high")
        self.assertEqual(models_for(self.project, "reviewer", "codex")["codex_effort"], "xhigh")

    def test_setting_for_another_tool_is_ignored(self):
        save_role_models(self.project, {"tester": {"tool": "codex", "model": "gpt-6-luna", "effort": "low"}})
        save_team(self.project, dict(TEAM, tester="cursor"))
        self.assertEqual(load_role_models(self.project)["tester"], {"tool": "cursor", "model": "auto"})

    def test_bad_effort_is_refused(self):
        from collab.config import ConfigError
        with self.assertRaises(ConfigError):
            save_role_models(self.project, {"tester": {"tool": "codex", "model": "x", "effort": "very-high"}})
        with self.assertRaises(ConfigError):
            save_role_models(self.project, {"tester": {"tool": "codex", "model": "  ", "effort": "high"}})

    def test_other_settings_kept(self):
        save_role_models(self.project, {"tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}})
        data = json.loads((self.project / ".collab" / "配置.json").read_text(encoding="utf-8"))
        self.assertEqual(data["team"], TEAM)
        self.assertEqual(data["codex_effort"], "xhigh", "老的整家设置不动")


class Dispatch(Base):
    def test_run_models_by_role(self):
        save_role_models(self.project, {"tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"},
                                        "reviewer": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}})
        got = run_models(self.project, {"test": "codex", "dev": "cursor", "review": "codex"})
        self.assertEqual(got["测试员"]["codex_effort"], "high")
        self.assertEqual(got["复查员"]["codex_effort"], "xhigh")
        self.assertEqual(got["开发员"]["cursor"], "auto")

    def test_each_worker_gets_its_own_models(self):
        wt = self.project / "wt"
        wt.mkdir()
        cdir = self.project / ".collab" / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
        seen = {}

        def ask_fn(adapter, prompt, cwd, models=None, **kw):
            role = "复查员" if "只读复查" in prompt else ("测试员" if "编写自动化测试" in prompt else "开发员")
            seen[role] = models["codex_effort"]
            return AgentResult(ok=True, output="", attempts=1, final="结论：通过" if role == "复查员" else "好了")
        role_models = {"测试员": {"codex": "gpt-6.1-sol", "codex_effort": "high", "cursor": "auto", "agy": "x"},
                       "复查员": {"codex": "gpt-6.1-sol", "codex_effort": "xhigh", "cursor": "auto", "agy": "x"}}
        run_card(Card("CARD-001", state="待派发"), self.project / ".collab", wt, "true", "cursor", "codex",
                 ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                 models={"codex": "m", "codex_effort": "low", "cursor": "auto", "agy": "x"}, role_models=role_models)
        self.assertEqual(seen["测试员"], "high")
        self.assertEqual(seen["复查员"], "xhigh")
        self.assertEqual(seen["开发员"], "low", "没给岗位设置的用整体那份")


class Planner(Base):
    def test_plan_uses_dispatcher_setting(self):
        from collab.planner import plan_models
        save_role_models(self.project, {"dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "medium"}})
        self.assertEqual(plan_models(self.project)["codex_effort"], "medium")


class Check(Base):
    def test_same_tool_different_setting_checked_separately(self):
        from collab import check
        calls = []

        def fake(tool, models, timeout=None):
            calls.append((tool, models.get("codex_effort")))
            return {"ok": True, "status": "ok", "reason": "通了", "how": "", "seconds": 0}
        role_models = {"tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"},
                       "reviewer": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"},
                       "dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}}
        with mock.patch.object(check, "check_tool", side_effect=fake):
            out = check.check_team(self.project, team=TEAM, roles=["dispatcher", "tester", "reviewer"],
                                   role_models=role_models)
        self.assertEqual(sorted(calls), [("codex", "high"), ("codex", "xhigh")], "设置一样的只测一次")
        shared = {r["role"]: r.get("shared_with") for r in out}
        self.assertEqual(shared["tester"], "dispatcher")
        self.assertIsNone(shared["reviewer"])


if __name__ == "__main__":
    unittest.main()
