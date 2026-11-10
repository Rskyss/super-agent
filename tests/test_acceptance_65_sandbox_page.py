"""65 号单：沙盒接入的页面和 Mac 窗口。

规则层用 node 检查（tests/sandbox_page_check.js）；页面层的密钥规矩、Mac 窗口的钥匙串账号规则用文字检查。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "desktop" / "static"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class Rules(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_rules(self):
        proc = subprocess.run(["node", str(HERE / "sandbox_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_old_page_checks_still_pass(self):
        for name in ("chat_page_check.js", "chat_card_check.js", "chat_blocks_check.js", "blocks_page_check.js",
                     "blocks_detail_check.js", "blocks_saved_keys_check.js"):
            proc = subprocess.run(["node", str(HERE / name)], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{name}：{proc.stderr or proc.stdout}")


class Page(unittest.TestCase):
    def test_secret_boxes_are_password_and_not_stored(self):
        for rel in ("desktop/static/chat.js", "desktop/static/blocks.js"):
            text = read(rel)
            self.assertIn("data-account", text, f"{rel}：每把密钥一个框，带上钥匙串账号名")
            self.assertNotRegex(text, r"localStorage|sessionStorage|indexedDB|document\.cookie")
            self.assertNotIn("console.log", text)
        chat = read("desktop/static/chat.js")
        self.assertIn("secret_keys", chat, "确认时把几把密钥一起带上")
        self.assertIn("请先把每个密钥框都填好", chat)
        self.assertIn("passKeyToWindow(acc", chat, "放进基础层后逐把交给 Mac 窗口存进钥匙串")

    def test_pending_uses_connect_builtin(self):
        text = read("desktop/static/blocks.js")
        self.assertIn("/api/blocks/connect_builtin", text)
        self.assertIn("data-pending", text)

    def test_styles_use_theme_vars(self):
        for rel, sel in (("desktop/static/chat.css", ".prop-warn.caution"), ("desktop/static/blocks.css", ".block-item.pending")):
            css = read(rel)
            self.assertIn(sel, css)
            block = css[css.index(sel):]
            block = block[:block.index("}")]
            self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b", "颜色只用 theme.css 里的变量")


class MacWindow(unittest.TestCase):
    def test_account_rule_matches_server(self):
        swift = read("desktop/mac/Sources/SuperAgent/BlocksWindow.swift")
        self.assertIn('"tavily"', swift)
        self.assertIn("cli:", swift)
        server = read("desktop/server.py")
        self.assertIn("tavily|cli:", server)


if __name__ == "__main__":
    unittest.main()
