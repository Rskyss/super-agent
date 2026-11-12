"""68c 追加验收；保留原验收不动，CLI、HOME 和成果均使用临时夹具。

运行：python3 -m unittest discover -s tests -p "test_acceptance_68c*"
页面测试由本文件调用 cli_models_68c_page_check.js，无第三方 Node 依赖。
"""
import copy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from collab import abilities, abilities_run, blocks, blocks_check
import test_acceptance_68c_cli_models as original


class StrictFamilyStatusTest(original.CliCase):
    def assert_unusable(self, result, reason):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "fail", result)
        self.assertEqual(result["reason"], reason, "失败原因沿用旧说法")
        self.assertNotIn("已接上", result["reason"])
        self.assertNotIn("这家有", result["reason"])
        self.assertTrue(result["how"])

    def test_tool_reported_models_do_not_hide_runtime_or_missing_file_failures(self):
        cases = [
            ("oserror", "工具运行不起来"),
            ("command-not-found", "工具运行不起来"),
            ("timeout", "超时：规定时间内没做完"),
            ("no-file-zero", "命令跑完了，但没建出文件"),
            ("no-file-error", "命令跑完了，但没建出文件"),
            ("login", "没登录"),
        ]
        for name in ("agy", "cursor"):
            self.fake.save(output=original.AGY_OUTPUT if name == "agy" else
                           "Available models\n\ncomposer-2.5 - Composer (current, default)\nauto - Auto\n")
            for failure, reason in cases:
                with self.subTest(tool=name, failure=failure):
                    def runner(args, folder, timeout):
                        if failure == "oserror":
                            raise OSError("fake executable cannot start")
                        if failure == "timeout":
                            raise subprocess.TimeoutExpired(args, timeout)
                        return {"command-not-found": (127, "command not found"),
                                # 即使成功退出或输出提到额度，也不能掩盖没建文件。
                                "no-file-zero": (0, "quota exceeded"),
                                "no-file-error": (1, "finished without output file"),
                                "login": (1, "not logged in; please login")}[failure]

                    result = blocks_check.check_cli(blocks.builtin_card(name), root=self.root,
                                                    path=str(self.fake.bin), runner=runner)
                    self.assertEqual(result.get("models_from"), "tool")
                    self.assert_unusable(result, reason)
                    self.assertEqual(blocks.last_record(name, self.root, type="check")["status"], "fail")

    def test_builtin_models_cannot_prove_model_failure_is_connected(self):
        for name in ("agy", "cursor", "codex"):
            with self.subTest(tool=name):
                self.fake.save(list_code=1, probe="fail")
                result = self.check(name)
                self.assertEqual(result.get("models_from"), "builtin")
                self.assert_unusable(result, "模型名不对")

    def test_codex_cache_cannot_prove_model_failure_is_connected(self):
        self.fake.cache([{"slug": "gpt-6.1-sol", "visibility": "list"}])
        self.fake.save(probe="fail")
        result = self.check("codex")
        self.assertEqual(result.get("models"), ["gpt-6.1-sol"])
        self.assert_unusable(result, "模型名不对")

    def test_cursor_live_models_allow_a_model_specific_failure(self):
        self.fake.save(output="Available models\n\ncomposer-2.5 - Composer (current, default)\nauto - Auto\n",
                       probe="fail")
        result = self.check("cursor")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "listed", result)
        self.assertEqual(result["models_from"], "tool")
        self.assertIn("已接上", result["reason"])
        self.assertIn("模型名不对", result["reason"])

    def test_explicit_quota_still_connects_with_builtin_or_codex_cache(self):
        for source in ("builtin", "cache"):
            with self.subTest(source=source):
                if source == "cache":
                    self.fake.cache([{"slug": "gpt-6.1-sol", "visibility": "list"}])
                self.fake.save(probe="quota")
                result = self.check("codex")
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "listed", result)
                self.assertIn("额度不足", result["reason"])


class TrialAbilityExecutionTest(original.CliCase):
    def setUp(self):
        super().setUp()
        blocks.save_card(blocks.builtin_card("agy"), self.root)
        card = copy.deepcopy(abilities.BUILTIN_ABILITIES["draw-image"])
        card.update(id="trial-note", block="agy", model=original.FLASH)
        card["how"].update(prompt="写下 {scene}，存成输出.txt", output_file="输出.txt")
        card["output"] = {"type": "text", "check": {}}
        abilities.save_ability(card, self.root)
        self.executions = []

    def run_note(self):
        def runner(args, folder, timeout):
            self.executions.append(args)
            (Path(folder) / "输出.txt").write_text("试通后的能力成果", encoding="utf-8")
            return 0, "OK"

        return abilities_run.run("trial-note", {"scene": "测试"}, self.root,
                                 runner=runner, path=str(self.fake.bin),
                                 codex_images=self.base / "unused-images")

    def test_successful_model_trial_unblocks_real_ability_execution(self):
        failed_check = self.known(status="fail", reason="默认模型没通")
        self.assertTrue(self.trial()["ok"])
        # 另一模型随后失败，不应抹掉试通过的整块状态。
        blocks.add_record("agy", dict(type="model_check", model=original.ARGON,
                                     ok=False, status="fail", reason="另一个没通"), self.root)
        result = self.run_note()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"], "试通后的能力成果")
        self.assertEqual(result["model"], original.FLASH)
        self.assertEqual(len(self.executions), 1)
        self.assertIn(original.FLASH, self.executions[0])
        self.assertEqual(blocks.last_record("agy", self.root, type="check"), failed_check)
        folder = abilities.result_folder("trial-note", result["run_id"], self.root)
        self.assertEqual((folder / "结果.txt").read_text(encoding="utf-8"), "试通后的能力成果")

    def test_failed_trial_does_not_unblock_ability(self):
        self.known(status="fail", reason="默认没通")
        self.fake.save(probe="fail")
        self.assertFalse(self.trial()["ok"])
        self.assertEqual(self.run_note()["status"], "block")
        self.assertEqual(self.executions, [])

    def test_new_failed_check_invalidates_older_success_even_in_same_second(self):
        for record in [
            dict(type="check", ok=False, status="fail"),
            dict(type="model_check", ok=True, status="ok", model=original.FLASH),
            dict(type="check", ok=False, status="fail"),
        ]:
            blocks.append_jsonl(blocks._record_path("agy", self.root),
                                dict(record, time="2026-11-12T10:00:00", reason="测试原因"))
        self.assertEqual(self.run_note()["status"], "block")
        self.assertEqual(self.executions, [])


class ClerkFailedCheckPreservationTest(original.BlocksCase):
    def setUp(self):
        super().setUp()
        self.cli = original.FakeCli(self, Path(self.tmp))
        self.server.terminal_path = lambda: str(self.cli.bin)
        p = patch.object(blocks_check, "watched_runner", self.cli.runner)
        p.start()
        self.addCleanup(p.stop)

    def test_clerk_cannot_save_failed_preset_with_builtin_list_or_codex_cache(self):
        self.cli.save(probe="fail", list_code=1)
        self.cli.cache([{"slug": "gpt-6.1-sol", "visibility": "list"}])
        for name in ("agy", "codex"):
            with self.subTest(tool=name):
                cid, pid = self.proposal("propose_connect_cli", {"template": name})
                status, data = self.confirm(cid, pid)
                self.assertEqual(status, 200, data)
                job = self.job(data["job"])
                self.assertFalse(job["result"]["ok"])
                status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
                self.assertEqual(status, 409, saved)
                self.assertFalse(blocks._card_path(name, self.home).exists())
                self.assertEqual(job["result"]["status"], "fail", job)


class AdditionalPageBehaviorTest(unittest.TestCase):
    def run_page_case(self, scenario):
        node = shutil.which("node")
        self.assertIsNotNone(node, "页面验收需要 Node")
        script = Path(__file__).with_name("cli_models_68c_page_check.js")
        done = subprocess.run([node, str(script), scenario], capture_output=True,
                              text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_missing_primary_selects_block_default(self):
        self.run_page_case("default-model")

    def test_changing_backup_saves_default_instead_of_first_model(self):
        self.run_page_case("save-backup")

    def test_explicit_primary_still_takes_precedence(self):
        self.run_page_case("explicit-model")

    def test_switching_blocks_preserves_each_pending_trial(self):
        self.run_page_case("parallel-trials")


if __name__ == "__main__":
    unittest.main()
