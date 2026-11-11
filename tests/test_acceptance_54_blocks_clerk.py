"""54 号单：积木库行政。

同一个行政，把积木库资料夹当成它的“项目”：在那里只拿到积木库专用的 8 个工具，说明换成单独一份文件；
项目窗口的行政不变。工具只能查环境、查安装、读帮助、看基础层、看核实名单，
以及三种提议（安装、接入 CLI、接入大模型 API）；确认之后真正去装、去检测是 55 号单（本机服务）的事。
聊天里贴了密钥：发给模型前就打码，并提醒用户作废。
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from acceptance_helpers import ProjectCase, ScriptedClient, tool_call, wait_idle, write_script
from collab import blocks
from collab.providers import TENCENT_BASE_URL
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from desktop.clerk.desk import ClerkDesk
from desktop.clerk.blocks_tools import is_blocks_home as is_home

KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
EXPECTED_TOOLS = {"scan_environment", "check_installed", "read_help", "list_blocks", "install_options",
                  "propose_install", "propose_connect_cli", "propose_connect_api",
                  # 沙盒接入 64 号单按已批准的方案加的四样（用户 2026-11-09 批准）：上网搜、读网页、网上安装卡、接联网搜索
                  "web_search", "read_page", "propose_web_install", "propose_connect_search",
                  # 积木库管家方案（2026-11-10，用户授权）：规矩拆成手册，按需翻开
                  "read_manual",
                  # 2026-11-10 用户真实接腾讯云混元时管家猜错模型名：加“查这家真有哪些模型”
                  "list_models"}


class BlocksCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "积木库"
        self.home.mkdir()
        os.environ["SUPER_AGENT_BLOCKS_HOME"] = str(self.home)
        self.client = ScriptedClient()
        self.desk = ClerkDesk(lambda: self.client)
        # 积木库出卡后这一轮就结束（用户 2026-11-10），出卡工具的回话不会再交给模型：测试从这里看
        self.card_replies = []
        for name in ("propose_install", "propose_connect_cli", "propose_connect_api",
                     "propose_web_install", "propose_connect_search"):
            def wrapped(*a, _orig=getattr(clerk_tasks, name), **k):
                reply = _orig(*a, **k)
                self.card_replies.append(reply)
                return reply
            patcher = mock.patch.object(clerk_tasks, name, wrapped)
            patcher.start()
            self.addCleanup(patcher.stop)

    def talk(self, text="你好", cid="", where=None):
        where = where or str(self.home)
        cid = self.desk.send(where, cid, text)
        events = wait_idle(self.desk, where, cid)
        return cid, events

    def tools_sent(self, index=0):
        return {t["function"]["name"] for t in self.client.calls[index][1] or []}

    def tool_results(self, index=1):
        return [m["content"] for m in self.client.calls[index][0] if m.get("role") == "tool"]

    def proposals(self, events):
        return [e for e in events if e.get("type") == "proposal"]

    def call(self, name, args, text="好的"):
        self.client.steps = [{"tool_calls": [tool_call(name, args)]}, {"text": text}]
        self.card_replies = []
        cid, events = self.talk("帮我办")
        seen = self.tool_results(len(self.client.calls) - 1)  # 同一个测试里调多次，取最后这一轮
        if self.proposals(events) and is_home(self.home):  # 出了卡这一轮就结束，模型没再被问：看出卡工具的回话
            return cid, events, self.card_replies
        return cid, events, seen  # 没出成卡：只认真正交给模型的那句（Codex 复查第 3 条）


class ModeTest(BlocksCase):
    def test_blocks_home_gets_only_block_tools(self):
        self.talk()
        self.assertEqual(self.tools_sent(), EXPECTED_TOOLS)

    def test_project_window_unchanged(self):
        self.talk(where=self.project)
        names = self.tools_sent()
        self.assertIn("propose_task", names)
        self.assertNotIn("scan_environment", names)

    def test_role_comes_from_file(self):
        path = Path(blocks_tools.ROLE_FILE)
        self.assertTrue(path.is_file(), "行政说明要存成单独一份文件")
        self.assertIn("积木库", path.read_text(encoding="utf-8"))
        self.talk()
        system = self.client.calls[0][0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn(path.read_text(encoding="utf-8").strip()[:30], system["content"])
        self.assertNotIn("超级助手的行政人员，用户的前台和行政", system["content"], "不用项目窗口那份说明")

    def test_role_file_rules(self):
        # 积木库管家方案：基本规矩 + 三本手册合起来看
        text = Path(blocks_tools.ROLE_FILE).read_text(encoding="utf-8") + "".join(
            blocks_tools.manual_text(n) for n in blocks_tools.MANUALS)
        for word in ("体检", "核实名单", "密钥框", "确认", "不能"):
            self.assertIn(word, text)
        self.assertNotIn("Claude Code", text, "Claude 订阅不参与")

    def test_is_blocks_home(self):
        self.assertTrue(blocks_tools.is_blocks_home(str(self.home)))
        self.assertFalse(blocks_tools.is_blocks_home(self.project))


class ReadToolsTest(BlocksCase):
    def test_scan_environment(self):
        write_script(self.bin / "node", 'echo "v22.1.0"\n')
        _, _, results = self.call("scan_environment", {})
        self.assertIn("Node", results[0])
        self.assertIn("v22.1.0", results[0])
        self.assertIn("Codex", results[0])
        self.assertIsNotNone(blocks.last_record("env", root=self.home, type="env"), "体检要留记录")

    def test_check_installed(self):
        write_script(self.bin / "demo-cli", 'echo "demo 1.2.3"\n')
        _, _, results = self.call("check_installed", {"command": "demo-cli"})
        self.assertIn("1.2.3", results[0])
        _, _, results = self.call("check_installed", {"command": "not-here-cli"})
        self.assertIn("没", results[0])

    def test_read_help(self):
        ran = Path(self.tmp) / "help_args.txt"
        write_script(self.bin / "demo-cli", f'echo "$@" > "{ran}"; echo "Usage: demo-cli -p PROMPT --model M"; '
                                            f'echo "token {KEY}"\n')
        _, _, results = self.call("read_help", {"command": "demo-cli"})
        self.assertIn("Usage: demo-cli", results[0])
        self.assertNotIn(KEY, results[0], "帮助里像密钥的字要打码")
        self.assertEqual(ran.read_text().strip(), "--help", "只许跑 --help")

    def test_read_help_refuses_dangerous_or_odd(self):
        marker = Path(self.tmp) / "bash_ran"
        write_script(self.bin / "bash", f'touch "{marker}"\n')
        for command in ("bash", "../demo", "demo cli", "rm"):
            _, _, results = self.call("read_help", {"command": command})
            self.assertRegex(results[0], r"[一-鿿]")
        self.assertFalse(marker.exists(), "危险命令连帮助都不跑")

    def test_read_help_missing(self):
        _, _, results = self.call("read_help", {"command": "not-here-cli"})
        self.assertIn("找不到", results[0])

    def test_list_blocks(self):
        blocks.save_card(blocks.builtin_card("codex"), root=self.home)
        blocks.add_record("codex", {"type": "check", "ok": True, "reason": "通了"}, root=self.home)
        _, _, results = self.call("list_blocks", {})
        self.assertIn("Codex", results[0])
        self.assertIn("通了", results[0])

    def test_install_options(self):
        _, _, results = self.call("install_options", {"tool": "codex"})
        self.assertIn("codex-npm", results[0])
        self.assertIn("npm install -g @openai/codex", results[0])
        _, _, results = self.call("install_options", {"tool": "cursor"})
        self.assertIn("官网", results[0], "名单外的要用户自己装")

    def test_bad_arguments_never_crash(self):
        self.client.steps = [{"tool_calls": [tool_call("read_help", "不是 JSON")]}, {"text": "好"}]
        self.talk("帮我办")
        self.assertIn("看不懂", self.tool_results(1)[0])


class InstallProposalTest(BlocksCase):
    def test_propose_install(self):
        cid, events, results = self.call("propose_install", {"install_id": "codex-npm"})
        props = self.proposals(events)
        self.assertEqual(len(props), 1)
        prop = props[0]
        self.assertEqual(prop["kind"], "install")
        self.assertEqual(prop["install"], "codex-npm")
        self.assertEqual(prop["command"], "npm install -g @openai/codex")
        self.assertTrue(prop["source"].startswith("https://"))
        answer = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertEqual(answer["kind"], "install")
        self.assertEqual(answer["install"], "codex-npm")
        self.assertEqual(answer["state"], "confirmed")

    def test_unknown_install_refused(self):
        _, events, results = self.call("propose_install", {"install_id": "npm install evil"})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("核实名单", results[0])

    def test_cancel(self):
        cid, events, _ = self.call("propose_install", {"install_id": "node-brew"})
        answer = clerk_tasks.decide(str(self.home), cid, self.proposals(events)[0]["id"], "cancel")
        self.assertEqual(answer["state"], "cancelled")


class ConnectCliTest(BlocksCase):
    def test_template_is_verified(self):
        cid, events, _ = self.call("propose_connect_cli", {"template": "codex"})
        prop = self.proposals(events)[0]
        self.assertEqual(prop["kind"], "connect")
        self.assertTrue(prop["verified"])
        self.assertEqual(prop["name"], "Codex")
        answer = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertEqual(answer["kind"], "connect")
        self.assertEqual(answer["block"]["command"], "codex")
        self.assertIsNone(answer["approved"], "核实过的不用确认串")

    def test_custom_card_shows_full_command(self):
        write_script(self.bin / "demo-cli", "exit 0\n")
        card = {"id": "demo", "kind": "cli", "name": "演示", "command": "demo-cli", "run": ["-p", "{prompt}"],
                "source": "builtin"}
        cid, events, _ = self.call("propose_connect_cli", {"card": card})
        prop = self.proposals(events)[0]
        self.assertFalse(prop["verified"])
        self.assertIn(str(self.bin / "demo-cli"), prop["preview"], "没核实的要亮出完整命令和完整路径")
        self.assertIn("新建一个文件", prop["preview"])
        answer = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertEqual(answer["block"]["source"], "clerk", "行政起草的卡不能自称内置")
        self.assertEqual(answer["approved"], prop["preview"], "确认的就是给用户看的那一串")

    def test_dangerous_card_refused(self):
        card = {"id": "x", "kind": "cli", "name": "坏", "command": "bash", "run": ["-c", "{prompt}"]}
        _, events, results = self.call("propose_connect_cli", {"card": card})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("bash", results[0])

    # “连续 3 次就停”按一段对话算（复查后改，原来按编号算，换编号能绕过、预置的会永久锁死），
    # 测试在 test_blocks_clerk_fixes.ThreeStrikesFixes

    def test_success_resets_failure_count(self):
        for ok in (False, False, False, True):
            blocks.add_record("demo", {"type": "check", "ok": ok}, root=self.home)
        write_script(self.bin / "demo-cli", "exit 0\n")
        card = {"id": "demo", "kind": "cli", "name": "演示", "command": "demo-cli", "run": ["-p", "{prompt}"]}
        _, events, _ = self.call("propose_connect_cli", {"card": card})
        self.assertEqual(len(self.proposals(events)), 1)


class ConnectApiTest(BlocksCase):
    def test_known_provider(self):
        cid, events, _ = self.call("propose_connect_api", {"provider": "tencent", "model": "deepseek/deepseek-flash",
                                                           "name": "腾讯云 DeepSeek"})
        prop = self.proposals(events)[0]
        self.assertEqual(prop["kind"], "connect_api")
        answer = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        card = answer["block"]
        self.assertEqual(card["kind"], "llm_api")
        self.assertEqual(card["base_url"], TENCENT_BASE_URL)
        self.assertEqual(card["key_account"], "tencent")
        blocks.validate_card(card)

    def test_other_provider_needs_https_address(self):
        _, events, results = self.call("propose_connect_api", {"provider": "other", "model": "m-1", "name": "某家"})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("地址", results[0])
        _, events, _ = self.call("propose_connect_api", {"provider": "other", "model": "m-1", "name": "某家",
                                                         "base_url": "http://x.example/v1"})
        self.assertEqual(self.proposals(events), [])
        _, events, _ = self.call("propose_connect_api", {"provider": "other", "model": "m-1", "name": "某家",
                                                         "base_url": "https://x.example/v1"})
        self.assertEqual(len(self.proposals(events)), 1)

    def test_key_in_arguments_refused(self):
        _, events, results = self.call("propose_connect_api", {"provider": "tencent", "model": "m", "name": "x",
                                                               "key": KEY})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("密钥框", results[0])


class PastedKeyTest(BlocksCase):
    def test_key_never_reaches_model_or_disk(self):
        cid, events = self.talk(f"我的密钥是 {KEY} 帮我接通义")
        sent = json.dumps(self.client.calls, ensure_ascii=False)
        self.assertNotIn(KEY, sent, "密钥不能发给行政用的服务商")
        stored = "".join(p.read_text(encoding="utf-8") for p in (self.home / ".collab" / "行政对话").glob("*"))
        self.assertNotIn(KEY, stored)
        notes = [e.get("text", "") for e in events if e.get("type") in ("notice", "message", "user")]
        self.assertTrue(any("作废" in n for n in notes), "要提醒用户去作废这把密钥")


if __name__ == "__main__":
    import unittest
    unittest.main()
