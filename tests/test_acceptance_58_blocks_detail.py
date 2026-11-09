"""58 号单：积木库页面（下）——聊天上方固定的体检卡、基础层详情（检测、移出）。

规则层（blocks.js 里 BlocksCore 新加的几个函数）用 node 检查；页面结构和密钥的处理用文字检查；
本机服务这张单不改，最后一组只守住页面要用的体检字段。聊天页（项目窗口也在用）一行不改，由原有测试把关。
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
        proc = subprocess.run(["node", str(HERE / "blocks_detail_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_page_56_rules_still_pass(self):
        proc = subprocess.run(["node", str(HERE / "blocks_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class PageTest(unittest.TestCase):
    def test_health_card_above_chat(self):
        html = read("blocks.html")
        for element in ('id="healthCard"', 'id="healthLine"', 'id="healthToggle"', 'id="healthRecheck"',
                        'id="healthRows"'):
            self.assertIn(element, html)
        stage = html[html.index('class="stage"'):]
        self.assertLess(stage.index('class="topbar"'), stage.index('id="healthCard"'))
        self.assertLess(stage.index('id="healthCard"'), stage.index('id="chatFrame"'),
                        "体检卡固定在聊天上方，不进聊天记录（用户 2026-11-09 定）")
        self.assertIn("重新检查", html)

    def test_detail_area(self):
        html = read("blocks.html")
        for element in ('id="blockDetail"', 'id="detailBack"', 'id="detailTitle"', 'id="detailStatus"',
                        'id="detailWarn"', 'id="detailRows"', 'id="detailPreview"', 'id="detailKeyBox"',
                        'id="detailCheck"', 'id="detailRemove"', 'id="detailResult"', 'id="detailCard"'):
            self.assertIn(element, html)
        section = re.search(r"<section[^>]*id=\"blockDetail\"[^>]*>", html).group(0)
        self.assertIn("hidden", section, "一开始显示聊天，详情藏着")
        key = re.search(r"<input[^>]*id=\"detailKey\"[^>]*>", html).group(0)
        self.assertIn('type="password"', key)
        self.assertIn('autocomplete="off"', key)
        self.assertIn("密钥只填在这里，别贴在聊天里", html)
        self.assertRegex(html, r"<details[^>]*id=\"detailCard\"[^>]*>\s*<summary>接法卡全文</summary>")
        for words in ("返回聊天", "移出基础层"):
            self.assertIn(words, html)

    def test_uses_existing_endpoints(self):
        script = read("blocks.js")
        for part in ("/api/blocks/scan", "/api/blocks/job", "/api/blocks/detail", "/api/blocks/check",
                     "/api/blocks/remove", "approved", "api_key"):
            self.assertIn(part, script)

    def test_confirm_before_remove(self):
        script = read("blocks.js")
        self.assertRegex(script, r"window\.confirm\(\s*BlocksCore\.removeConfirm\(",
                         "移出前先确认（用户 2026-11-09 定）")

    def test_key_rules(self):
        script = read("blocks.js")
        self.assertNotRegex(script, r"localStorage|sessionStorage|indexedDB|document\.cookie", "密钥不能存在网页里")
        self.assertNotRegex(script, r"api_key=|[?&]key=", "密钥不能放进地址")
        self.assertNotIn("console.log", script, "不打印")
        self.assertIn("请先在密钥框里填好密钥", script, "没填密钥就点检测，页面先拦住，不发请求")
        self.assertRegex(script, r"detailKey\"?\)?\.value\s*=\s*\"\"", "检测请求成功、离开详情时清掉密钥框")

    def test_text_not_html(self):
        script = read("blocks.js")
        for name in ("healthView", "detailView", "checkView"):
            self.assertIn(name, script)
        self.assertNotRegex(script, r"innerHTML\s*=\s*[^;]*(cardText|\.preview|\.fix|\.why|problem)",
                            "接法卡、命令、体检说明、坏卡原因都可能带网页代码，一律 textContent")

    def test_styles_use_theme(self):
        css = read("blocks.css")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b", "颜色只用 theme.css 里的变量")
        for part in ("health", "detail"):
            self.assertIn(part, css, "体检卡、详情要有自己的样式")


class ScanContractTest(BlocksCase):
    def test_scan_items_have_what_page_needs(self):
        status, data = self.bpost("/api/blocks/scan")
        self.assertEqual(status, 200, data)
        result = self.job(data["job"])["result"]
        self.assertTrue(result["time"])
        self.assertTrue(result["items"])
        for item in result["items"]:
            for key in ("name", "label", "kind", "status", "version", "login", "why", "note", "fix"):
                self.assertIn(key, item, f"体检卡要用 {key}")
            self.assertIn(item["status"], ("有", "没有", "找不到"))


if __name__ == "__main__":
    unittest.main()
