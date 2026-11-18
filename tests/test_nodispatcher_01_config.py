"""去掉调度员 D1：团队配置只有写测试、写代码、复查三个岗位；写方案/备交接资料用专门的模型设置（Codex、强度固定低）。

依据 docs/去掉调度员/方案.md。老配置里残留的 dispatcher 要悄悄忽略，不报错。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.config import (
    ConfigError, clean_role_models, legacy_dispatcher, load_role_models, load_team, plan_models,
    save_legacy_dispatcher, save_role_models, save_team, save_team_member,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data: dict) -> None:
        (self.project / ".collab" / "配置.json").write_text(json.dumps(data), encoding="utf-8")

    def read(self) -> dict:
        return json.loads((self.project / ".collab" / "配置.json").read_text(encoding="utf-8"))


class TeamHasThreeRoles(Base):
    def test_nothing_configured_has_no_dispatcher_and_is_not_configured(self):
        team = load_team(self.project)
        self.assertNotIn("dispatcher", team)
        self.assertEqual((team["tester"], team["developer"], team["reviewer"]), ("codex", "cursor", "agy"))
        self.assertFalse(team["configured"])

    def test_old_config_with_dispatcher_is_read_without_error_and_stays_configured(self):
        self.write({"team": {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}})
        team = load_team(self.project)
        self.assertNotIn("dispatcher", team)
        self.assertTrue(team["configured"], "老项目不用重配")

    def test_old_config_with_only_dispatcher_stays_configured(self):
        # 以前行政帮忙只改过调度员一项的项目，升级后仍算已配置
        self.write({"team": {"dispatcher": "agy"}})
        self.assertTrue(load_team(self.project)["configured"])

    def test_all_three_executors_saved_counts_as_configured(self):
        self.write({"team": {"tester": "codex", "developer": "cursor", "reviewer": "none"}})
        team = load_team(self.project)
        self.assertTrue(team["configured"])
        self.assertEqual(team["reviewer"], "none")

    def test_only_some_executors_saved_is_not_configured(self):
        self.write({"team": {"tester": "agy"}})
        team = load_team(self.project)
        self.assertFalse(team["configured"])
        self.assertEqual(team["tester"], "agy")

    def test_save_team_writes_three_roles_and_keeps_the_old_dispatcher_untouched(self):
        # 老的调度员项只有超级玩家那套“接调度员”检测在读：团队页保存不能把它悄悄删掉，也不能被传进来的值改掉
        self.write({"codex": "gpt-6.1-sol", "team": {"dispatcher": "codex", "tester": "codex"}})
        save_team(self.project, {"dispatcher": "agy", "tester": "agy", "developer": "codex", "reviewer": "none"})
        saved = self.read()
        self.assertEqual(saved["team"], {"tester": "agy", "developer": "codex", "reviewer": "none", "dispatcher": "codex"})
        self.assertEqual(saved["codex"], "gpt-6.1-sol", "其他设置原样保留")
        self.assertNotIn("dispatcher", load_team(self.project), "团队读取仍然看不到它")

    def test_save_team_does_not_invent_a_dispatcher(self):
        save_team(self.project, {"dispatcher": "agy", "tester": "agy", "developer": "codex", "reviewer": "none"})
        self.assertEqual(self.read()["team"], {"tester": "agy", "developer": "codex", "reviewer": "none"})

    def test_save_team_without_dispatcher_key_works(self):
        save_team(self.project, {"tester": "codex", "developer": "cursor", "reviewer": "agy"})
        self.assertTrue(load_team(self.project)["configured"])

    def test_save_team_still_rejects_bad_executor(self):
        with self.assertRaises(ConfigError):
            save_team(self.project, {"tester": "claude", "developer": "cursor", "reviewer": "agy"})

    def test_save_team_member_refuses_dispatcher(self):
        with self.assertRaises(ConfigError):
            save_team_member(self.project, "dispatcher", "codex")
        self.assertFalse((self.project / ".collab" / "配置.json").exists(), "拒绝时什么都不写")

    def test_save_team_member_still_works_for_executors(self):
        save_team_member(self.project, "tester", "agy")
        self.assertEqual(load_team(self.project)["tester"], "agy")


class RoleModelsHaveThreeRoles(Base):
    def test_legacy_dispatcher_entry_is_ignored_when_loading(self):
        self.write({"team": {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"},
                    "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "medium"}}})
        self.assertNotIn("dispatcher", load_role_models(self.project))

    def test_clean_role_models_skips_legacy_dispatcher_but_still_checks_others(self):
        out = clean_role_models({"dispatcher": {"tool": "codex", "model": "x", "effort": "low"},
                                 "tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}})
        self.assertEqual(list(out), ["tester"])
        with self.assertRaises(ConfigError):
            clean_role_models({"tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "bogus"}})
        with self.assertRaises(ConfigError):
            clean_role_models({"nobody": {"tool": "codex", "model": "x"}})

    def test_save_role_models_does_not_store_a_new_legacy_dispatcher(self):
        save_role_models(self.project, {"dispatcher": {"tool": "codex", "model": "x", "effort": "low"},
                                        "tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}})
        self.assertEqual(list(self.read()["role_models"]), ["tester"])

    def test_save_role_models_keeps_an_existing_legacy_dispatcher_model(self):
        # 复查第 2 轮中等 4：超级玩家设置里只存“写代码”一项，不能顺带把老调度员的岗位模型删掉
        self.write({"role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"}}})
        save_role_models(self.project, {"developer": {"tool": "cursor", "model": "auto"}})
        self.assertEqual(self.read()["role_models"]["dispatcher"],
                         {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"})
        self.assertEqual(self.read()["role_models"]["developer"], {"tool": "cursor", "model": "auto"})


class LegacyDispatcherExit(Base):
    """超级玩家那套“接调度员”检测（desktop/dsh_dispatcher.py）还要读写老配置里的调度员：只留这一对出口。"""

    def test_reads_the_tool_and_its_role_model_from_an_old_config(self):
        self.write({"codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                    "team": {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"},
                    "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"}}})
        self.assertEqual(legacy_dispatcher(self.project),
                         ("codex", {"tool": "codex", "model": "gpt-6-astra", "effort": "medium"}))

    def test_role_model_falls_back_to_the_tool_defaults_like_the_other_roles(self):
        self.write({"codex": "gpt-6.1-sol", "codex_effort": "xhigh", "team": {"dispatcher": "codex"}})
        self.assertEqual(legacy_dispatcher(self.project)[1],
                         {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"})

    def test_nothing_saved_or_a_custom_command(self):
        self.assertEqual(legacy_dispatcher(self.project), ("", {}))
        self.write({"team": {"dispatcher": "custom:qwen --yolo"}})
        self.assertEqual(legacy_dispatcher(self.project), ("custom:qwen --yolo", {}))

    def test_save_merges_and_checks(self):
        self.write({"codex": "gpt-x", "team": {"tester": "agy"}})
        save_legacy_dispatcher(self.project, "agy")
        saved = self.read()
        self.assertEqual(saved["team"], {"tester": "agy", "dispatcher": "agy"})
        self.assertEqual(saved["codex"], "gpt-x")
        self.assertNotIn("dispatcher", load_team(self.project), "团队读取仍然看不到它")
        with self.assertRaises(ConfigError):
            save_legacy_dispatcher(self.project, "claude")
        with self.assertRaises(ConfigError):
            save_legacy_dispatcher(self.project, "custom:")


class PlanModels(Base):
    """写方案、备交接资料：固定 Codex、强度固定低；模型跟写测试。"""

    def test_effort_is_low_even_when_overall_default_is_xhigh(self):
        self.write({"codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                    "team": {"tester": "cursor", "developer": "cursor", "reviewer": "agy"}})
        self.assertEqual(plan_models(self.project)["codex_effort"], "low")

    def test_effort_is_low_with_no_config_at_all(self):
        self.assertEqual(plan_models(self.project)["codex_effort"], "low")

    def test_model_follows_tester_when_tester_is_codex(self):
        self.write({"codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                    "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"},
                    "role_models": {"tester": {"tool": "codex", "model": "gpt-6-astra", "effort": "high"}}})
        got = plan_models(self.project)
        self.assertEqual(got["codex"], "gpt-6-astra")
        self.assertEqual(got["codex_effort"], "low", "写测试是高档，写方案也不跟着变高")

    def test_model_is_codex_default_when_tester_is_not_codex(self):
        self.write({"codex": "gpt-6-luna",
                    "team": {"tester": "agy", "developer": "cursor", "reviewer": "none"},
                    "role_models": {"tester": {"tool": "agy", "model": "gemini-4-argon-high"}}})
        got = plan_models(self.project)
        self.assertEqual(got["codex"], "gpt-6-luna", "用 Codex 的整体设置，不会把 agy 的模型当成 Codex 的")
        self.assertEqual(got["codex_effort"], "low")

    def test_legacy_dispatcher_role_setting_no_longer_matters(self):
        self.write({"team": {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"},
                    "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}}})
        self.assertEqual(plan_models(self.project)["codex_effort"], "low")

    def test_broken_team_section_still_gives_low(self):
        self.write({"codex": "gpt-6.1-sol", "team": "oops"})
        self.assertEqual(plan_models(self.project)["codex_effort"], "low")

    def test_does_not_change_the_saved_overall_setting(self):
        self.write({"codex_effort": "xhigh"})
        plan_models(self.project)
        self.assertEqual(self.read()["codex_effort"], "xhigh")

    def test_planner_reexports_the_same_function(self):
        from collab import planner
        self.assertIs(planner.plan_models, plan_models, "别留两份，免得又分叉")


if __name__ == "__main__":
    unittest.main()
