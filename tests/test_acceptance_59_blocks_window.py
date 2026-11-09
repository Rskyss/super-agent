"""59 号单：Mac 客户端里的积木库窗口。

菜单“窗口 → 积木库”和项目窗口左栏底部的入口打开一个单独的窗口，加载 blocks.html；
页面交来的密钥存进钥匙串（和项目窗口同一套账号名）；打开时从钥匙串自动重连行政；
两个窗口都开着时关项目窗口只是藏起来（用户 2026-11-09 定）。

Swift 编不编得过、窗口真不真能开，靠施工后 `desktop/mac/build.sh` 和 Claude 的真实走查；这里用文字检查守住约定。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "desktop" / "static"
SOURCES = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent"
MAIN = SOURCES / "main.swift"
BLOCKS = SOURCES / "BlocksWindow.swift"


def read(path):
    return path.read_text(encoding="utf-8")


def body_of(text, signature, after=""):
    """取一个 Swift 函数从签名到配对的右花括号为止的正文；给了 after 就从那段文字后面找。"""
    start = text.index(signature, text.index(after) if after else 0)
    depth, i = 0, text.index("{", start)
    while True:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


class EntryTest(unittest.TestCase):
    def test_window_menu_has_blocks_and_project_window(self):
        main = read(MAIN)
        menu = body_of(main, "func buildMenu()")
        line = next(l for l in menu.splitlines() if 'submenu("窗口"' in l or "submenu(\"窗口\"" in l)
        self.assertIn('"积木库"', line, "窗口菜单里要有“积木库”")
        self.assertIn('"项目窗口"', line, "窗口菜单里要有“项目窗口”，把藏起来的项目窗口叫回来")
        self.assertIn("openBlocks(_:)", line)
        self.assertIn("showProjectWindow(_:)", line)

    def test_nav_footer_has_blocks_entry(self):
        html = read(STATIC / "nav.html")
        foot = html[html.index('<footer class="foot">'):html.index("</footer>")]
        self.assertIn('id="blocksBtn"', foot, "左栏底部要有积木库入口")
        self.assertIn("积木库", foot)
        nav = read(STATIC / "nav.js")
        self.assertRegex(nav, r'\$\("blocksBtn"\)\.addEventListener\("click"[^;]*superagent://blocks')
        self.assertIn('case "blocks"', read(MAIN), "窗口要接住 superagent://blocks")

    def test_nav_rules_unchanged(self):
        if not shutil.which("node"):
            self.skipTest("这台电脑没有 node")
        proc = subprocess.run(["node", str(HERE / "nav_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BLOCKS.exists(), "积木库窗口单独写在 BlocksWindow.swift")
        self.src = read(BLOCKS)

    def test_own_window_loading_blocks_page_with_token(self):
        self.assertIn("final class BlocksWindow", self.src)
        self.assertIn("NSWindow(", self.src)
        self.assertIn("isReleasedWhenClosed = false", self.src)
        self.assertIn('"/static/blocks.html?token=', self.src)
        self.assertIn('"积木库"', self.src, "窗口标题")
        self.assertIn("SuperAgentBlocksWindow", self.src, "记住窗口大小位置")

    def test_one_window_brought_to_front(self):
        show = body_of(self.src, "func show(")
        self.assertIn("makeKeyAndOrderFront", show)

    def test_closing_stops_the_page(self):
        close = body_of(self.src, "func windowWillClose(")
        self.assertIn("about:blank", close, "关掉后页面别在后台每几秒问一次")

    def test_dialogs_on_its_own_window(self):
        for sig in ("runJavaScriptConfirmPanelWithMessage", "runJavaScriptAlertPanelWithMessage"):
            fn = body_of(self.src, sig)
            self.assertIn("webView.window", fn, "确认框要弹在积木库窗口上，不能弹在（可能藏着的）项目窗口上")
            self.assertIn("completionHandler(", fn)

    def test_key_handler(self):
        self.assertIn("WKScriptMessageHandler", self.src)
        self.assertRegex(self.src, r'\.add\(.*name:\s*"blocksKey"\)')
        handler = body_of(self.src, "func userContentController(", after="final class BlocksWindow")
        self.assertIn("ClerkKey.save(", handler)
        self.assertIn("message.webView", handler, "只收积木库窗口自己页面发来的")
        self.assertIn("securityOrigin", handler, "只收本机服务页面（127.0.0.1 和当前端口）发来的")
        self.assertIn("isValidAccount", handler)
        for bad in ("print(", "NSLog(", "UserDefaults"):
            self.assertNotIn(bad, handler)

    def test_account_rule(self):
        fn = body_of(self.src, "static func isValidAccount(")
        for word in ('"tencent"', '"bailian"', '"other:'):
            self.assertIn(word, fn)

    def test_reconnect_from_keychain_before_loading(self):
        fn = body_of(self.src, "func load(")
        self.assertIn("/api/blocks/state", fn)
        self.assertIn("ClerkKey.read(", fn)
        self.assertIn("/api/blocks/connect", fn)
        self.assertLess(fn.index("/api/blocks/state"), fn.index("/api/blocks/connect"))
        # “先连上再开页面”靠真实走查看：打开窗口不出现“重新连上行政”那张卡
        connect = fn[fn.index("/api/blocks/connect"):]
        self.assertNotRegex(connect[:300], r'"check"\s*:\s*true', "从钥匙串自动重连不测，不多花额度")

    def test_no_secret_in_address_or_log(self):
        self.assertNotRegex(self.src, r'blocks\.html\?[^"]*api_key')
        self.assertNotIn("print(", self.src)
        self.assertNotIn("NSLog(", self.src)


class ProjectWindowTest(unittest.TestCase):
    def setUp(self):
        self.main = read(MAIN)

    def test_closing_project_window_hides_when_blocks_open(self):
        fn = body_of(self.main, "func windowShouldClose(")
        self.assertIn("orderOut", fn)
        self.assertIn("blocks", fn)

    def test_last_window_rule(self):
        fn = body_of(self.main, "@objc func showProjectWindow(")
        self.assertIn("makeKeyAndOrderFront", fn)
        closed = body_of(self.main, "func blocksWindowClosed(")
        self.assertIn("isVisible", closed)
        self.assertIn("NSApp.terminate", closed, "项目窗口藏着、积木库也关了：两个都关了，退出")

    def test_project_delegate_ignores_blocks_window(self):
        fn = body_of(self.main, "func windowDidResize(")
        self.assertRegex(fn, r"notification\.object\s*(as\? NSWindow)?\s*===?\s*window|guard .*=== window",
                         "积木库窗口改大小时，不能去重排项目窗口的三栏")

    def test_server_port_reaches_blocks_window(self):
        said = body_of(self.main, "func serverSaid(")
        self.assertIn("blocks", said, "本机服务起来（或重启换了端口）后，积木库窗口要跟着重新加载")
        stopped = body_of(self.main, "func serverStopped(")
        self.assertIn("blocks", stopped, "本机服务停了，积木库窗口也要说一声")

    def test_project_window_stuff_unchanged(self):
        for words in ('case "card"', 'case "main"', 'case "settings"', 'case "thread"', 'case "new"', 'case "saved"',
                      "applicationShouldTerminateAfterLastWindowClosed", '"/api/clerk/test"'):
            self.assertIn(words, self.main)


if __name__ == "__main__":
    unittest.main()
