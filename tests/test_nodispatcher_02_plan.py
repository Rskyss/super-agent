"""去掉调度员 D2：写方案、备交接资料不再读“调度员岗”的设置，固定 Codex、强度低、模型跟写测试。"""
import json
import os
import shutil
from pathlib import Path
from unittest import mock

from acceptance_helpers import GOOD_PLAN, ProjectCase


class PlanAndHandoverUseFixedLowEffort(ProjectCase):
    def write_config(self, data: dict) -> None:
        (self.collab / "配置.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def plan_args(self) -> list:
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        return json.loads(self.codex_log.read_text(encoding="utf-8").splitlines()[-1])

    def setUp(self):
        super().setUp()
        self.make_card(allowed=False)
        (Path(self.project) / "docs").mkdir()   # 有项目说明，方案里就不用再起草
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")

    def test_plan_effort_is_low_without_any_dispatcher_setting(self):
        self.write_config({"codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                           "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"}})
        args = self.plan_args()
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertEqual(args[args.index("-m") + 1], "gpt-6.1-sol")

    def test_plan_effort_is_low_with_no_config_file_content(self):
        args = self.plan_args()
        self.assertIn('model_reasoning_effort="low"', args, "整体默认是极高，不能落到它")

    def test_plan_model_follows_tester_but_effort_stays_low(self):
        self.write_config({"team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"},
                           "role_models": {"tester": {"tool": "codex", "model": "gpt-6-astra", "effort": "high"}}})
        args = self.plan_args()
        self.assertEqual(args[args.index("-m") + 1], "gpt-6-astra")
        self.assertIn('model_reasoning_effort="low"', args)

    def test_legacy_dispatcher_setting_in_old_config_is_ignored(self):
        self.write_config({"team": {"dispatcher": "codex", "tester": "codex", "developer": "cursor",
                                    "reviewer": "agy"},
                           "role_models": {"dispatcher": {"tool": "codex", "model": "gpt-6-luna",
                                                          "effort": "xhigh"}}})
        args = self.plan_args()
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertNotEqual(args[args.index("-m") + 1], "gpt-6-luna")

    def test_handover_worker_uses_the_same_low_setting(self):
        from collab.cli import _handover_worker
        self.write_config({"codex_effort": "xhigh",
                           "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"}})
        env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_NO_HANDOVER"}
        with mock.patch.dict(os.environ, env, clear=True):
            worker = _handover_worker(self.project)
        self.assertIsNotNone(worker)
        self.assertEqual(worker[0], "codex")
        self.assertEqual(worker[1]["codex_effort"], "low")

    def test_handover_worker_is_off_without_codex(self):
        from collab.cli import _handover_worker
        env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_NO_HANDOVER"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(_handover_worker(self.project))

    def test_handover_dispatch_itself_carries_low_effort_and_read_only(self):
        """复查第 1 轮轻微 4：不只看配置工厂，要看交接资料最后真正派出去的参数。"""
        from types import SimpleNamespace
        from collab.cli import _handover_worker
        from collab.dispatch import HANDOVER, _make_handover
        self.write_config({"codex_effort": "xhigh",
                           "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"},
                           "role_models": {"tester": {"tool": "codex", "model": "gpt-6-astra", "effort": "high"}}})
        env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_NO_HANDOVER"}
        with mock.patch.dict(os.environ, env, clear=True):
            adapter, models = _handover_worker(self.project)
        seen = {}

        def ask_fn(tool, prompt, cwd, models=None, **kw):
            seen.update(tool=tool, models=dict(models or {}))
            return SimpleNamespace(ok=True, final="## 要改的文件\n无", output="")

        cdir = self.collab / "cards" / "CARD-001"
        _make_handover(ask_fn, cdir, adapter, models, cdir / "需求卡.md", self.project)
        self.assertEqual(seen["tool"], "codex")
        self.assertEqual(seen["models"]["codex_effort"], "low", "最终派出去的强度是低，没被别处覆盖")
        self.assertEqual(seen["models"]["codex"], "gpt-6-astra", "模型跟写测试")
        self.assertEqual(seen["models"]["codex_sandbox"], "read-only")
        self.assertTrue(seen["models"]["no_plugins"])
        self.assertTrue((cdir / HANDOVER).is_file())
