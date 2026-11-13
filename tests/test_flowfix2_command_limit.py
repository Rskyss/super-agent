"""流程修理第二轮 ㊹：干活的人自己跑的命令卡住，只掐那一条，告诉它超时了，它接着干；不整个停掉它。

69 里 agy 写了一段验证小脚本，自己把自己锁死（CPU 0%），agy 一直等它，6 分半后 Claude 手动掐掉。
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import watched_runner

# 假装是干活的工具：起一个会卡住的子命令，等它结束（被掐）后照常说话、正常退出
TOOL = r"""
import subprocess, sys
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
code = p.wait()
print("子命令结束了，退出码", code, flush=True)
print("接着干完了", flush=True)
"""

TOOL_WITH_TEST = r"""
import subprocess, sys
p = subprocess.Popen(["/bin/bash", "-c", "sleep 4; echo test-done # RUN_CARD_TESTS"])
p.wait()
print("测试跑完了", flush=True)
"""


class CommandLimit(unittest.TestCase):
    def _run(self, script, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "tool.py"
            tool.write_text(script, encoding="utf-8")
            live = Path(tmp) / "live.log"
            started = time.time()
            code, out = watched_runner([sys.executable, str(tool)], tmp, 60, poll=0.3, live_path=live, helper_grace=0, **kw)
            return code, out, time.time() - started

    def test_stuck_command_is_stopped_and_worker_goes_on(self):
        code, out, took = self._run(TOOL, command_limit=2, test_limit=30)
        self.assertEqual(code, 0, out)
        self.assertIn("接着干完了", out)
        self.assertLess(took, 15, "不能等到 60 秒")
        self.assertIn("这条命令跑了", out, "在实时输出里记一笔，看得见")

    def test_card_tests_get_longer_limit(self):
        code, out, took = self._run(TOOL_WITH_TEST, command_limit=2, test_limit=30,
                                    test_command="sleep 4; echo test-done # RUN_CARD_TESTS")
        self.assertEqual(code, 0, out)
        self.assertIn("测试跑完了", out)
        self.assertNotIn("这条命令跑了", out, "本卡测试 4 秒没超过它自己的时限，不能掐")


class OnlyWhenDispatching(unittest.TestCase):
    def test_default_runner_does_not_watch_commands(self):
        """体检、能力运行也用这个看门人；它们不归这条规矩管"""
        import inspect
        self.assertEqual(inspect.signature(watched_runner).parameters["command_limit"].default, 0)

    def test_dispatch_turns_it_on(self):
        import inspect
        from collab import dispatch
        src = inspect.getsource(dispatch._dispatch)
        self.assertIn('extra["watch_commands"] = True', src)


if __name__ == "__main__":
    unittest.main()


class NoPlugins(unittest.TestCase):
    """㉘ ㊶：派活时 Codex 不带用户配的插件（只这一次，不改用户设置）；agy 没有这样的开关，交代里写明不用。"""

    def test_codex_turns_each_plugin_off(self):
        from collab import agents
        old = agents._codex_plugin_names
        agents._codex_plugin_names = lambda: ["playwright", "Figma MCP PRO", "tavily"]
        try:
            cmd = agents._build("codex", "干活", {"codex": "gpt-6.1-sol", "codex_effort": "low", "no_plugins": True})
            plain = agents._build("codex", "干活", {"codex": "gpt-6.1-sol", "codex_effort": "low"})
        finally:
            agents._codex_plugin_names = old
        self.assertIn('mcp_servers={"playwright"={enabled=false},"Figma MCP PRO"={enabled=false},'
                      '"tavily"={enabled=false}}', cmd)
        self.assertEqual(cmd[-1], "干活")
        self.assertFalse([c for c in plain if "mcp_servers" in c], "没要求时不动")

    def test_dispatch_asks_for_no_plugins_and_tells_agy(self):
        import inspect
        from collab import dispatch
        src = inspect.getsource(dispatch._dispatch)
        self.assertIn("no_plugins=True", src)
        self.assertIn("不要打开浏览器", dispatch._READ_RULES + dispatch._NO_PLUGINS_NOTE)


class ReviewRound1C(unittest.TestCase):
    """Codex 复查第 1 轮 C 块。"""

    def test_stop_blocks_new_tools_until_allowed(self):
        from collab import agents
        agents.kill_current_tool_process_group()
        try:
            res = agents.ask("codex", "x", tempfile.gettempdir(), runner=agents.watched_runner)
            self.assertFalse(res.ok)
            self.assertIn("已停工", res.output)
        finally:
            agents.allow_tools()
        self.assertFalse(agents._stopping["on"])

    def _table(self, rows):
        return {pid: (ppid, age, cmd) for pid, ppid, age, cmd in rows}

    def test_plugin_children_are_not_commands(self):
        from collab.agents import _stuck_commands
        table = self._table([(10, 1, 400, "agy-real -p 干活"),
                             (11, 10, 300, "npm exec @playwright/mcp"),
                             (12, 11, 300, "node /x/plugins/server.js")])
        self.assertEqual(_stuck_commands(table, 10, 400, (120, 300), "", 15), [])

    def test_early_shell_command_is_still_watched(self):
        from collab.agents import _stuck_commands
        table = self._table([(10, 1, 160, "agy-real -p 干活"),
                             (20, 10, 155, "python3 -c import time; time.sleep(999)")])
        self.assertEqual([p for p, _ in _stuck_commands(table, 10, 160, (120, 300), "", 15)], [20])

    def test_env_prefix_test_command_gets_test_limit(self):
        from collab.agents import _stuck_commands
        test = "PYTHONPATH=. python3 -m pytest tests/test_x.py"
        table = self._table([(10, 1, 400, "agy-real -p 干活"),
                             (30, 10, 180, "python3 -m pytest tests/test_x.py")])
        self.assertEqual(_stuck_commands(table, 10, 400, (120, 300), test, 15), [])
        table[30] = (10, 320, "python3 -m pytest tests/test_x.py")
        self.assertEqual([p for p, _ in _stuck_commands(table, 10, 400, (120, 300), test, 15)], [30])

    def test_plugin_names_in_every_spelling(self):
        from collab.agents import _names_in_config
        text = ('[mcp_servers.github]\ncommand = "x"\n[mcp_servers."Figma MCP PRO"]\n'
                "[mcp_servers.'single q']  # 注释\n[mcp_servers.github.env]\nA = 1\n")
        self.assertEqual(_names_in_config(text), ["github", "Figma MCP PRO", "single q"])

    def test_real_discovery_reads_settings_file(self):
        import os
        from collab import agents
        with tempfile.TemporaryDirectory() as home:
            Path(home, "config.toml").write_text('[mcp_servers.playwright]\ncommand = "npx"\n', encoding="utf-8")
            old_env, old_cache, old_path = os.environ.get("CODEX_HOME"), list(agents._plugin_cache), os.environ["PATH"]
            os.environ["CODEX_HOME"], os.environ["PATH"] = home, "/usr/bin:/bin"  # 问不到 codex：只按文件
            agents._plugin_cache.clear()
            try:
                self.assertEqual(agents._codex_plugin_names(), ["playwright"])
            finally:
                agents._plugin_cache[:] = old_cache
                os.environ["PATH"] = old_path
                if old_env is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_env


class ReviewRound2C(ReviewRound1C):
    """Codex 复查第 2 轮 C 块。"""

    def test_early_node_command_is_watched(self):
        from collab.agents import _stuck_commands
        table = self._table([(10, 1, 160, "agy-real -p 干活"),
                             (20, 10, 155, "node -e setInterval(()=>{},1000)")])
        self.assertEqual([p for p, _ in _stuck_commands(table, 10, 160, (120, 300), "", 15)], [20])

    def test_quoted_env_value_test_command(self):
        from collab.agents import _stuck_commands
        test = "PYTEST_ADDOPTS='-q --disable-warnings' python3 -m pytest tests/test_x.py"
        table = self._table([(10, 1, 400, "agy-real -p 干活"),
                             (30, 10, 180, "python3 -m pytest tests/test_x.py")])
        self.assertEqual(_stuck_commands(table, 10, 400, (120, 300), test, 15), [])

    def test_inline_plugin_settings(self):
        from collab.agents import _names_in_config
        text = 'mcp_servers = { playwright = { command = "npx", env = { A = "1" } }, "a b" = {command="x"} }\n'
        self.assertEqual(_names_in_config(text), ["playwright", "a b"])

    def test_stop_and_start_do_not_interleave(self):
        import inspect
        from collab import agents
        src = inspect.getsource(agents.watched_runner)
        self.assertIn("with _tools_lock:", src)
        self.assertIn("with _tools_lock:", inspect.getsource(agents.kill_current_tool_process_group))


class ReviewRound3C(ReviewRound1C):
    def test_lock_is_reentrant_for_stop_signal(self):
        from collab import agents
        with agents._tools_lock:
            with agents._tools_lock:  # 停工信号打在起进程时：同一线程再拿一次不能卡死
                pass

    def test_npx_check_command_is_watched(self):
        from collab.agents import _stuck_commands
        table = self._table([(10, 1, 400, "agy-real -p 干活"),
                             (40, 10, 180, "/bin/zsh -c npx tsc --noEmit"),
                             (41, 40, 180, "node /x/node_modules/.bin/tsc --noEmit")])
        self.assertEqual([p for p, _ in _stuck_commands(table, 10, 400, (120, 300), "", 15)], [40])

    def test_dotted_inline_plugin(self):
        from collab.agents import _names_in_config
        self.assertEqual(_names_in_config('mcp_servers.playwright = { command = "npx", args = ["@playwright/mcp"] }\n'),
                         ["playwright"])

