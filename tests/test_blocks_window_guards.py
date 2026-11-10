"""59 号单第一次交付（2026-11-09，Gemini）验收时查出、原验收测试没拦住的问题。

那次交付 16 项验收全过，但：齿轮按钮失灵、左栏切换逻辑被整段重写、退出不再停后台服务、
施工回执被整份覆盖、积木库窗口打开是空白、两个窗口都关了不退出、存密钥的来源检查没按单子做。
这里把这些都守住。Swift 编不编得过靠 build.sh 和真实走查。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_acceptance_59_blocks_window import BLOCKS, MAIN, STATIC, body_of, read

ROOT = Path(__file__).resolve().parent.parent


class ProjectWindowKept(unittest.TestCase):
    def setUp(self):
        self.main = read(MAIN)

    def test_gear_still_opens_settings(self):
        self.assertIn('$("gear").addEventListener("click", function () { tell("superagent://settings"); });',
                      read(STATIC / "nav.js"))

    def test_quit_still_stops_terminal_and_server(self):
        fn = body_of(self.main, "func applicationWillTerminate(")
        for words in ("quitting = true", "stopTerminal()", "server?.terminate()"):
            self.assertIn(words, fn)

    def test_left_switching_not_rewritten(self):
        fn = body_of(self.main, "func showLeft(")
        for words in ("switchToTerminal(opening: opening, chatMode: chatMode)", "leaveConfirmed = false",
                      "chatKept = true", "closeChatOnServer(clearView: false)", "stopTerminal()", "loadConfig()"):
            self.assertIn(words, fn, "左栏切换（离开前确认、看设置时聊天不断开）不能动")

    def test_project_menus_bring_project_window_front(self):
        """积木库在前面时按 ⌘N、⌘2 这些项目窗口的快捷键：先把项目窗口叫到前面（用户 2026-11-09 定）"""
        self.assertIn("NSMenu.willSendActionNotification", self.main)
        fn = body_of(self.main, "@objc func projectMenuWillAct(")
        for words in ('"文件"', '"项目"', '"对话"', '"面板"', "makeKeyAndOrderFront"):
            self.assertIn(words, fn)
        self.assertNotIn('"窗口"', fn, "窗口菜单（最小化、积木库）不能把项目窗口拉出来")

    def test_no_native_button_on_project_window(self):
        self.assertNotIn('NSButton(title: "积木库"', self.main, "入口在左栏页面里，不在窗口上另加原生按钮")

    def test_server_ready_reloads_blocks_only_when_open(self):
        said = body_of(self.main, "func serverSaid(")
        self.assertRegex(said, r"blocks\.is(Visible|Open)")
        # 只在拿到新端口那一次重连：放在 guard port == nil 之后，不然服务每说一段话就重载一次
        self.assertLess(said.index("guard port == nil"), said.index("blocks.load("))
        self.assertIn("--open-blocks", self.main, "走查开关")

    def test_receipts_append_only(self):
        text = read(ROOT / "docs" / "积木库" / "施工单" / "回执.md")
        for n in ("50", "54", "55", "56", "57", "59"):
            self.assertIn(f"## 单号 {n}", text, "回执只往后加，不能覆盖前面的")
        self.assertIn("施工单 58：体检卡和基础层详情 已完成", text)


class NavButtonStyled(unittest.TestCase):
    def test_css(self):
        css = read(STATIC / "nav.css")
        self.assertIn(".blocks-btn", css)
        rules = "\n".join(l for l in css.splitlines() if ".blocks-btn" in l)
        self.assertIn("var(--dim)", rules)
        self.assertIn("flex: none", rules, "项目名过长时按钮不被挤掉")
        self.assertNotRegex(rules, r"#[0-9a-fA-F]{3,6}\b", "不写死色值")


class BlocksWindowBehaviour(unittest.TestCase):
    def setUp(self):
        self.src = read(BLOCKS)

    def test_opening_loads_the_page(self):
        show = body_of(self.src, "func show(")
        self.assertIn("load(", show, "从关着变开着要加载页面，不然是空白")

    def test_closing_tells_app(self):
        close = body_of(self.src, "func windowWillClose(")
        self.assertIn("blocksWindowClosed()", close, "两个窗口都关了要退出")
        main = read(MAIN)
        closed = body_of(main, "func blocksWindowClosed(")
        self.assertRegex(closed, r"!window\.isVisible\s*&&\s*!window\.isMiniaturized", "项目窗口最小化时关积木库不能退出")
        should = body_of(main, "func windowShouldClose(")
        self.assertRegex(should, r"blocks\.isOpen", "积木库最小化也算开着")
        self.assertIn("return false", should)

    def test_app_held_weakly(self):
        self.assertRegex(self.src, r"weak (var|let) app")

    def test_dark_min_size_and_selftest_name(self):
        show = body_of(self.src, "func show(")
        for words in ("darkAqua", "panelBackground", "NSSize(width: 760, height: 480)", "SuperAgentBlocksSelfTest"):
            self.assertIn(words, show)

    def test_key_only_from_own_page_and_local_server(self):
        handler = body_of(self.src, "func userContentController(", after="final class BlocksWindow")
        self.assertRegex(handler, r"message\.webView\s*===")
        self.assertIn("message.frameInfo.securityOrigin", handler)
        self.assertIn('"127.0.0.1"', handler)
        self.assertRegex(handler, r"origin\.port\s*==\s*port", "来源端口要和当前端口比")

    def test_stopped_wording(self):
        self.assertIn('stoppedWords = "本机服务停了，可以用菜单「面板 → 刷新右栏」再试一次。"', self.src)
        self.assertIn("stoppedWords", body_of(self.src, "func serverStopped("))

    def test_superagent_links_cancelled(self):
        fn = body_of(self.src, "decidePolicyFor")
        self.assertIn('"superagent"', fn)
        self.assertIn(".cancel", fn)

    def test_stale_load_ignored(self):
        fn = body_of(self.src, "func load(")
        self.assertIn("generation", fn, "慢的旧请求回来不能盖掉新的（端口换了、关了又开）")


@unittest.skipUnless(shutil.which("swift"), "这台电脑没有 swift")
class AccountRuleRuns(unittest.TestCase):
    """真跑一遍单子定的账号规则（比 account_for 窄：下划线、IPv6、中文域名不收，已登记为已知取舍）。"""

    def test_cases(self):
        fn = body_of(read(BLOCKS), "static func isValidAccount(")
        fn = fn.replace("static func", "func", 1)
        cases = {"tencent": True, "bailian": True, "other:api.example.com": True, "other:a-b.c9": True,
                 "other:": False, "other:API.example.com": False, "other:a b": False, "other:a/b": False,
                 "other:a\nb": False, "other:" + "a" * 254: False, "other:" + "a" * 253: True,
                 "Tencent": False, "": False, "openai": False, "other:a_b": False,
                 # 沙盒接入 65 号单：联网搜索和普通工具的密钥账号，和 server.py 的 _SAVED_ACCOUNT 一致
                 "tavily": True, "tavily2": False, "cli:demotool:DEMO_KEY": True, "cli:a-1:AB_2": True,
                 "cli:Demo:DEMO_KEY": False, "cli:demotool:demo_key": False, "cli:demotool:D": False,
                 "cli::DEMO_KEY": False, "cli:demotool:DEMO_KEY:x": False, "cli:-a:KEY": False}
        lines = ["import Foundation", fn]
        for i, (value, want) in enumerate(cases.items()):  # JSON 的字符串写法 Swift 也认
            lines.append(f'print(isValidAccount({json.dumps(value)}) == {str(want).lower()} ? "ok" : "bad {i}")')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "check.swift"
            script.write_text("\n".join(lines), encoding="utf-8")
            proc = subprocess.run(["swift", str(script)], capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([l for l in proc.stdout.splitlines() if l != "ok"], [])


if __name__ == "__main__":
    unittest.main()
