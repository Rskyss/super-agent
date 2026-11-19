"""腾讯云接进团队（用户 2026-11-19：“行政那个腾讯云的 key 里有 deepseek，同步到测试、代码、复查的选项里，我自己选写代码是谁”）。

做法和千问一样：Codex 的壳，模型来源换成腾讯云 TokenHub（responses 接法，11-19 实测通）。
密钥用行政那一把（钥匙串 local.superagent.clerk / tencent），不用再填；测试一律用文件换掉，不碰真钥匙串。
"""
import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class Options(unittest.TestCase):
    def test_tencent_is_a_worker_with_models(self):
        from collab.config import DEFAULT_MODELS, MODEL_OPTIONS, WORKERS
        self.assertIn("tencent", WORKERS)
        self.assertEqual(DEFAULT_MODELS["tencent"], "hy4")   # 11-19 DeepSeek 两个的免费额度用完，默认改混元
        values = [m["value"] for m in MODEL_OPTIONS["tencent"]]
        for model in ("deepseek-v4.1-flash", "deepseek-v4-pro", "hy4", "hy4-flash"):
            self.assertIn(model, values)

    def test_names_everywhere(self):
        from collab.check import TOOL_NAMES as CHECK_NAMES
        from desktop.view import TOOL_NAMES as VIEW_NAMES
        self.assertEqual(CHECK_NAMES["tencent"], "腾讯云")
        self.assertEqual(VIEW_NAMES["tencent"], "腾讯云")

    def test_team_page_lists_it(self):
        from desktop.server import _WORKER_TOOLS
        self.assertIn({"value": "tencent", "label": "腾讯云"}, _WORKER_TOOLS)
        js = (ROOT / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        self.assertIn('tencent: "hy4"', js)
        self.assertIn('tool === "tencent"', js)

    def test_command_line_accepts_it(self):
        from collab import cli
        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        src = (ROOT / "collab" / "cli.py").read_text(encoding="utf-8")
        for flag in ("--dev-tool", "--test-tool", "--review-tool"):
            line = next(l for l in src.splitlines() if f'"{flag}"' in l)
            self.assertIn('"tencent"', line, flag)
        self.assertIsNone(parser) if parser is None else None


class Command(unittest.TestCase):
    def test_codex_shell_pointing_at_tokenhub(self):
        from collab.agents import _is_codex_shell, tencent_command
        self.assertTrue(_is_codex_shell("tencent"))
        cmd = " ".join(tencent_command("写代码", "deepseek-v4-pro"))
        self.assertIn("tokenhub.tencentmaas.com/v1", cmd)
        self.assertIn('env_key="SUPERAGENT_TENCENT_KEY"', cmd)
        self.assertIn('wire_api="responses"', cmd)
        self.assertIn("request_max_retries=8", cmd)
        self.assertIn("deepseek-v4-pro", cmd)
        self.assertNotIn("SUPERAGENT_TENCENT_KEY=", cmd, "命令行里不出现密钥")

    def test_build_uses_the_chosen_model(self):
        from collab.agents import _build
        from collab.config import DEFAULT_MODELS
        cmd = " ".join(_build("tencent", "写代码", dict(DEFAULT_MODELS, tencent="hy4")))
        self.assertIn("hy4", cmd)


class Key(unittest.TestCase):
    def test_key_from_file_when_given(self):
        from collab.config import tencent_key
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k"
            f.write_text("sk-test\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SUPERAGENT_TENCENT_KEY_FILE": str(f)}), \
                    mock.patch("subprocess.run") as run:
                self.assertEqual(tencent_key(), "sk-test")
                run.assert_not_called()

    def test_tests_never_touch_the_real_keychain(self):
        from collab.config import tencent_key
        with mock.patch.dict(os.environ, {"SUPERAGENT_TENCENT_KEY_FILE": "/nonexistent/k"}), \
                mock.patch("subprocess.run") as run:
            self.assertEqual(tencent_key(), "")
            run.assert_not_called()

    def test_without_file_reads_the_clerk_key_from_keychain(self):
        from collab import config
        done = mock.Mock(returncode=0, stdout="sk-clerk\n")
        env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_TENCENT_KEY_FILE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(config, "TENCENT_KEY_FILE", Path("/nonexistent/k")), \
                mock.patch("subprocess.run", return_value=done) as run:
            self.assertEqual(config.tencent_key(), "sk-clerk")
        args = run.call_args[0][0]
        self.assertIn("local.superagent.clerk", args)
        self.assertIn("tencent", args)

    def test_ask_hands_the_key_only_to_that_process(self):
        from collab import agents
        seen = {}

        def runner(cmd, cwd, timeout, env=None, **kw):
            seen["env"] = env
            return 0, "好"

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k"
            f.write_text("sk-test", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SUPERAGENT_TENCENT_KEY_FILE": str(f)}):
                agents.ask("tencent", "回一个字", tmp, runner=runner)
                self.assertNotIn("SUPERAGENT_TENCENT_KEY", os.environ)
        self.assertEqual(seen["env"].get("SUPERAGENT_TENCENT_KEY"), "sk-test")

    def test_check_says_missing_key_plainly(self):
        from collab.check import check_tool
        from collab.config import DEFAULT_MODELS
        with mock.patch.dict(os.environ, {"SUPERAGENT_TENCENT_KEY_FILE": "/nonexistent/k"}):
            got = check_tool("tencent", DEFAULT_MODELS)
        self.assertEqual(got["status"], "missing")
        self.assertIn("行政", got["how"])


class SameAsCodexShell(unittest.TestCase):
    def test_usage_and_last_reply_read_like_codex(self):
        for rel in ("collab/token_usage.py", "collab/dispatch.py", "desktop/view.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("tencent", src, f"{rel} 里千问当 Codex 壳的地方，腾讯云也要一样")


if __name__ == "__main__":
    unittest.main()
