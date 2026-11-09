"""56 号单验收和复查后的修改：积木库页面。

“重新连上”卡上手填的密钥要真的测一下（打错一位不能当成连上了，更不能被存进钥匙串）；
窗口从钥匙串自动重连不测，不多花额度。删除对话的提示照项目窗口：标题去掉括号尾巴，有没确认的提议要说会作废。
检测中不能点“返回”；回到聊天要清掉密钥框；刷新失败要说一句；刷新循环只起一套。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_acceptance_55_blocks_server import KEY, BlocksCase

HERE = Path(__file__).resolve().parent
SCRIPT = (HERE.parent / "desktop" / "static" / "blocks.js")


class ConnectCheckTest(BlocksCase):
    def reopen(self):
        self.bind()
        self.server.blocks_clerk = None  # 相当于重新打开窗口
        return len(self.fake.chats)

    def test_typed_key_is_checked(self):
        calls = self.reopen()
        self.fake.mode = "bad_key"
        status, data = self.bpost("/api/blocks/connect", {"api_key": "sk-typo", "check": True})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["ok"], "打错的密钥不能算连上")
        self.assertTrue(data["reason"], data)
        self.assertFalse(self.bget("/api/blocks/state")[1]["connected"])
        self.assertGreater(len(self.fake.chats), calls, "手填的要真的测一下")

    def test_typed_key_ok(self):
        calls = self.reopen()
        status, data = self.bpost("/api/blocks/connect", {"api_key": KEY, "check": True})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        self.assertTrue(self.bget("/api/blocks/state")[1]["connected"])
        self.assertEqual(len(self.fake.chats), calls + 1, "只发一句试试，不再测工具（绑定时测过了）")

    def test_check_failure_keeps_setting(self):
        self.reopen()
        self.fake.mode = "bad_key"
        self.bpost("/api/blocks/connect", {"api_key": "sk-typo", "check": True})
        self.assertEqual(self.bget("/api/blocks/state")[1]["setting"]["provider"], "tencent", "测不过不能把设置弄丢")


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class RulesTest(unittest.TestCase):
    def node(self, code):
        prog = "var B = require(%r);\n%s" % (str(SCRIPT), code)
        proc = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_delete_words_like_project_window(self):
        words = self.node('process.stdout.write(B.deleteConfirm({id: "a", title: "装 Codex（进行中）", pending: true}))')
        self.assertIn("装 Codex", words)
        self.assertNotIn("进行中", words, "标题和左栏一样去掉括号尾巴")
        self.assertIn("作废", words, "里面有没确认的提议，要说会一起作废")
        plain = self.node('process.stdout.write(B.deleteConfirm({id: "a", title: "装 Codex"}))')
        self.assertNotIn("作废", plain)

    def test_thread_title(self):
        self.assertEqual(self.node('process.stdout.write(B.threadTitle("装 Codex（3）"))'), "装 Codex")
        self.assertEqual(self.node('process.stdout.write(B.threadTitle(""))'), "对话")


class PageTextTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_reconnect_asks_for_check(self):
        self.assertRegex(self.script, r"api_key:\s*rawKey,\s*check:\s*true", "手填的密钥要让本机服务测一下")

    def test_back_disabled_while_checking(self):
        self.assertRegex(self.script, r"bindBack\"\)\.disabled\s*=\s*(true|on)\b", "检测中不能点返回，免得结果被吞掉")

    def test_main_view_clears_key(self):
        body = re.search(r"function showMainView\(\)\s*\{(.*?)\n  \}", self.script, re.S).group(1)
        self.assertIn('bindKey").value = ""', body, "回到聊天时清掉密钥框")

    def test_refresh_failure_says_so(self):
        self.assertEqual(self.script.count('<div class="empty">没连上本机服务，请稍后再试</div>'), 2,
                         "对话列表和基础层一次都没取到时，要说一句没连上")


if __name__ == "__main__":
    unittest.main()
