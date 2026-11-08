"""56 号单：积木库页面（上）——绑定页、左栏（聊天列表、基础层列表）、中间的行政聊天。

规则层（blocks.js 里的 BlocksCore）用 node 检查；页面结构和密钥的处理用文字检查；
页面要的两样数据（积木库资料夹在哪、有哪些服务商）由 /api/blocks/state 给。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_acceptance_55_blocks_server import BlocksCase

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


class RulesTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则层检查")
    def test_rules(self):
        proc = subprocess.run(["node", str(HERE / "blocks_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


class PageTest(unittest.TestCase):
    def test_files_and_shared_theme(self):
        html = read("blocks.html")
        for part in ("/static/theme.css", "/static/blocks.css", "/static/blocks.js", 'lang="zh-CN"'):
            self.assertIn(part, html)
        self.assertIn("<title>超级助手 · 积木库</title>", html)

    def test_bind_card(self):
        html = read("blocks.html")
        for element in ('id="bindCard"', 'id="bindProvider"', 'id="bindModel"', 'id="bindAddress"',
                        'id="bindKey"', 'id="bindCheck"', 'id="bindResult"'):
            self.assertIn(element, html)
        key = re.search(r"<input[^>]*id=\"bindKey\"[^>]*>", html).group(0)
        self.assertIn('type="password"', key)
        self.assertIn('autocomplete="off"', key)
        self.assertIn("API 密钥", html, "绑定页要说清楚：要的是按用量付费的 API 密钥，不是会员订阅")
        self.assertIn("订阅", html)

    def test_main_layout(self):
        html = read("blocks.html")
        for element in ('id="side"', 'id="newChat"', 'id="threads"', 'id="blocksList"', 'id="settingsBtn"',
                        'id="chatFrame"'):
            self.assertIn(element, html)
        self.assertIn("基础层", html)
        self.assertIn("聊天", html)
        frame = re.search(r"<iframe[^>]*id=\"chatFrame\"[^>]*>", html).group(0)
        self.assertNotIn("src=", frame, "聊天地址要等拿到资料夹和口令后由脚本填")

    def test_key_handling(self):
        script = read("blocks.js")
        self.assertNotRegex(script, r"localStorage|sessionStorage|indexedDB|document\.cookie", "密钥不能存在网页里")
        self.assertNotRegex(script, r"api_key=|[?&]key=", "密钥不能放进地址")
        self.assertNotIn("console.log", script, "不往控制台打东西，免得把密钥打出来")
        self.assertRegex(script, r"bindKey\"?\)?\.value\s*=\s*\"\"", "绑定后要清掉密钥框")
        self.assertIn("blocksKey", script, "在 Mac 窗口里，绑定成功后把密钥交给窗口存进钥匙串（第 8 项接）")
        self.assertIn("/api/blocks/bind", script)
        self.assertIn("/api/blocks/connect", script)

    def test_module_for_node(self):
        script = read("blocks.js")
        self.assertIn("module.exports = BlocksCore", script)
        self.assertIn('typeof document !== "undefined"', script, "页面层只在窗口里跑，node 里只测规则层")

    def test_chat_page_unchanged_for_projects(self):
        self.assertIn('params.get("clerk") === "1"', read("chat.js"), "复用现有聊天页的行政模式")


class StateTest(BlocksCase):
    def test_state_tells_page_folder_and_providers(self):
        from collab import blocks
        status, data = self.bget("/api/blocks/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["home"], str(blocks.home()))
        ids = [p["id"] for p in data["providers"]]
        self.assertEqual(ids, ["tencent", "bailian", "other"])
        for item in data["providers"]:
            self.assertEqual(set(item), {"id", "name", "base_url", "models"})

    def test_page_served(self):
        import urllib.request
        with urllib.request.urlopen(self.base + "/static/blocks.html", timeout=10) as answer:
            self.assertEqual(answer.status, 200)
            self.assertIn("积木库", answer.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
