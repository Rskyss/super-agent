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
        self.assertIn('shell_environment_policy.exclude=["SUPERAGENT_QWEN_KEY"]', cmd, "千问跑的命令拿不到密钥")

    def test_key_only_goes_to_that_one_process(self):
        """复查：密钥放进本程序环境会被之后的每个工具、每次测试继承，可能印进测试结果和交接"""
        seen = {}

        def runner(cmd, cwd, timeout, env=None, **_):
            seen["env"] = env
            return 0, "codex\n好了\ntokens used\n1\n好了\n"

        res = agents.ask("qwen", "干活", self.tmp.name, runner=runner, models={"qwen": "deepseek-v4.1-flash"})
        self.assertTrue(res.ok)
        self.assertEqual(seen["env"][config.QWEN_KEY_ENV], "sk-fake-123")
        self.assertNotIn(config.QWEN_KEY_ENV, os.environ, "本程序自己的环境里不能有")
        agents.ask("codex", "干活", self.tmp.name, runner=runner, models={"codex": "x", "codex_effort": "low"})
        self.assertIsNone(seen["env"], "别的工具拿不到")

    def test_codex_extras_off_for_qwen(self):
        """Qwen4 Max Preview 不认 Codex 打包附加功能的工具格式，整条请求被拒（11-13 抓包）：接千问时关掉"""
        cmd = agents._build("qwen", "干活", {"qwen": "qwen4-max-preview"})
        for f in ("multi_agent", "apps", "computer_use", "plugins", "browser_use", "in_app_browser"):
            self.assertIn(f"features.{f}=false", cmd)
        self.assertFalse([c for c in agents._build("codex", "干活", {"codex": "x", "codex_effort": "low"})
                          if c.startswith("features.")], "Codex 自己干活时不动")

    def test_rejected_request_is_not_called_not_logged_in(self):
        from collab.check import explain
        out = ("ERROR rmcp: AuthRequired(AuthRequiredError { www_authenticate_header: ... })\n"
               "ERROR: stream disconnected: <400> InternalError.Algo: [Invalid request parameters.]")
        reason, _ = explain(out, "qwen")
        self.assertEqual(reason, "这个模型不接受这样的请求")
        # 复查：插件启动日志里有参数错误、最后真正的失败是没登录 → 照旧说没登录
        reason2, _ = explain("mcp: Invalid request parameters\nERROR: 401 Unauthorized: not logged in", "codex")
        self.assertEqual(reason2, "没登录")

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
