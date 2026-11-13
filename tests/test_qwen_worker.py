"""团队里临时加“千问”（用户 11-13）：Codex 的壳，接千问 Token Plan 的地址，用那里的模型干活（默认 deepseek-v4.1-flash）。

密钥放在本机一个只有自己能读的文件里（不在项目、不进代码记录）；模型以后在团队设置里换。
11-13 Claude 已实测：Codex 壳接这个地址（responses 接法）跑“建文件 + 写测试 + 跑通”63 秒通过。
"""
import os
import tempfile
import unittest
from pathlib import Path

from collab import agents, config
from collab.check import check_tool


class Setting(unittest.TestCase):
    def test_qwen_is_a_worker_with_models(self):
        self.assertIn("qwen", config.WORKERS)
        self.assertEqual(config.DEFAULT_MODELS["qwen"], "deepseek-v4.1-flash")
        values = [o["value"] for o in config.MODEL_OPTIONS["qwen"]]
        self.assertIn("deepseek-v4.1-flash", values)
        self.assertEqual(config.model_label("qwen", {"qwen": "deepseek-v4.1-flash"}), "DeepSeek V4.1 Flash")

    def test_team_can_pick_qwen_for_each_worker_role(self):
        with tempfile.TemporaryDirectory() as p:
            config.save_team(p, {"dispatcher": "codex", "tester": "codex", "developer": "qwen", "reviewer": "codex"})
            config.save_role_models(p, {"developer": {"tool": "qwen", "model": "qwen3.8-max"}})
            self.assertEqual(config.load_role_models(p)["developer"], {"tool": "qwen", "model": "qwen3.8-max"})
            self.assertEqual(config.models_for(p, "developer", "qwen")["qwen"], "qwen3.8-max")

    def test_page_and_server_offer_qwen(self):
        root = Path(__file__).resolve().parent.parent
        server = (root / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertIn('{"value": "qwen", "label": "千问"}', server)
        team = (root / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        self.assertIn('tool === "qwen"', team)


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = Path(self.tmp.name) / "qwen.key"
        self.key.write_text("sk-fake-123\n", encoding="utf-8")
        self.old = os.environ.get("SUPERAGENT_QWEN_KEY_FILE")
        os.environ["SUPERAGENT_QWEN_KEY_FILE"] = str(self.key)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SUPERAGENT_QWEN_KEY_FILE", None)
        else:
            os.environ["SUPERAGENT_QWEN_KEY_FILE"] = self.old
        os.environ.pop(config.QWEN_KEY_ENV, None)
        self.tmp.cleanup()

    def test_runs_codex_shell_with_qwen_endpoint(self):
        cmd = agents._build("qwen", "干活", {"qwen": "glm-5.3", "codex_effort": "low"})
        self.assertEqual(cmd[:2], ["codex", "exec"])
        joined = " ".join(cmd)
        self.assertIn(config.QWEN_BASE_URL, joined)
        self.assertIn('model_provider="qwtp"', joined)
        self.assertIn('wire_api="responses"', joined)
        self.assertEqual(cmd[cmd.index("-m") + 1], "glm-5.3")
        self.assertEqual(cmd[-1], "干活")

    def test_key_never_in_command_line(self):
        cmd = agents._build("qwen", "干活", {"qwen": "deepseek-v4.1-flash"})
        self.assertNotIn("sk-fake-123", " ".join(cmd), "密钥不能出现在命令行（别人用进程列表能看到）")
        self.assertEqual(os.environ.get(config.QWEN_KEY_ENV), "sk-fake-123")

    def test_plugins_off_like_codex(self):
        old = agents._codex_plugin_names
        agents._codex_plugin_names = lambda: ["playwright"]
        try:
            cmd = agents._build("qwen", "干活", {"qwen": "deepseek-v4.1-flash", "no_plugins": True})
        finally:
            agents._codex_plugin_names = old
        self.assertIn('mcp_servers={"playwright"={enabled=false}}', cmd)

    def test_missing_key_is_said_plainly(self):
        os.environ["SUPERAGENT_QWEN_KEY_FILE"] = str(Path(self.tmp.name) / "没有.key")
        result = check_tool("qwen", {"qwen": "deepseek-v4.1-flash"})
        self.assertEqual(result["status"], "missing")
        self.assertIn("千问", result["reason"])
        self.assertIn("密钥", result["reason"])

    def test_codex_style_reply_and_live_line(self):
        from desktop.view import live_hint
        out = "codex\n先看代码。\nexec\nls\ncodex\n改好了。\ntokens used\n1,234\n改好了。\n"
        self.assertTrue(agents._is_codex_shell("qwen"))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(out, encoding="utf-8")
            self.assertEqual(live_hint(p, tool="qwen"), "改好了。")


if __name__ == "__main__":
    unittest.main()
