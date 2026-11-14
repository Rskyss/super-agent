"""精读报告第一批修复（docs/精读报告第一批修复/）：每条一个先红后绿的测试。"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import SWIFT


def swift_func(src, name):
    """从源码里取出一个顶层函数的全文（到第一个回到行首的右括号为止）。"""
    m = re.search(r"^func " + re.escape(name) + r"\b.*?^}", src, re.S | re.M)
    return m.group(0) if m else None


def section(src, start, end):
    return src[src.index(start):src.index(end, src.index(start))]


class S11SelfTestNeverTouchesRealKeychain(unittest.TestCase):
    """S11：所有走查、自测都用单独的测试钥匙串和单独的设置，碰不到用户真实的行政密钥。"""

    FLAGS_SELF_TEST = ["--walk", "--walk-clerk", "--walk-clerk-forget", "--walk-clerk-edge", "--walk-list",
                       "--walk-accept", "--walk-view", "--snapshot", "--clerk-try", "--clerk-ui-test",
                       "--clerk-key-file", "--clerk-key-dir"]
    FLAGS_NORMAL = ["--project", "--card", "--dispatcher", "--left", "--command", "--open-blocks",
                    "--bundled-root", "--old-package"]

    def setUp(self):
        self.src = SWIFT.read_text(encoding="utf-8")

    def test_one_shared_rule(self):
        self.assertIsNotNone(swift_func(self.src, "isSelfTestRun"), "要有一个共用的“是不是自测”判断 isSelfTestRun")

    def test_rule_behaviour(self):
        body = swift_func(self.src, "isSelfTestRun")
        self.assertIsNotNone(body)
        if not shutil.which("swift"):
            self.skipTest("本机没有 swift")
        lines = [body]
        for flag in self.FLAGS_SELF_TEST + self.FLAGS_NORMAL:
            lines.append(f'print("{flag}", isSelfTestRun(["/app", "{flag}", "x"]))')
        lines.append('print("none", isSelfTestRun(["/app"]))')
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "rule.swift"
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out = subprocess.run(["swift", str(f)], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = dict(line.split(" ") for line in out.stdout.split("\n") if line)
        for flag in self.FLAGS_SELF_TEST:
            self.assertEqual(got[flag], "true", f"{flag} 要算自测")
        for flag in self.FLAGS_NORMAL + ["none"]:
            self.assertEqual(got[flag], "false", f"{flag} 是正常使用，不算自测")

    def test_keychain_uses_shared_rule(self):
        """自测时钥匙串名字是 local.superagent.clerk.selftest，正常用是 local.superagent.clerk（方向不能反）。"""
        service = section(self.src, "static var service: String", "static func query")
        self.assertRegex(service, r'"local\.superagent\.clerk"\s*\+\s*\(isSelfTestRun\(\)\s*\?\s*"\.selftest"\s*:\s*""\)')
        self.assertNotIn("CommandLine.arguments", service, "不要再各写一份名单")
        # 读、存、删、列出都必须用这个 service，不能别处写死真实的名字（Codex 复查第 2 轮）
        key = section(self.src, "enum ClerkKey", "enum ChatMode")
        uses = re.findall(r"kSecAttrService as String:\s*([^,\]\n]+)", key)
        self.assertGreaterEqual(len(uses), 2, "query 和 accounts 都要带 service")
        for use in uses:
            self.assertEqual(use.strip(), "service", f"钥匙串请求要用 service，不能写成 {use.strip()}")
        self.assertEqual(self.src.count('"local.superagent.clerk"'), 1, "真实钥匙串名字只能在 service 里出现一次")

    def test_saved_settings_use_shared_rule(self):
        """自测时走查设置用单独一份，正常用才用真实的（方向不能反）。"""
        prefs = section(self.src, "var clerkPrefs: UserDefaults", "enum ClerkKey")
        self.assertRegex(prefs, r'isSelfTestRun\(\)\s*\?\s*\(UserDefaults\(suiteName:\s*"local\.superagent\.desktop\.walk"\)\s*\?\?\s*\.standard\)\s*:\s*\.standard')
        self.assertNotIn("CommandLine.arguments", prefs)

    def test_window_memory_and_opening_use_shared_rule(self):
        """同一类问题：自测不改用户的窗口记忆、不真开浏览器，也按同一条规则认（方向不能反）。"""
        self.assertIn("var selfTest: Bool { isSelfTestRun() }", self.src)
        opening = section(self.src, "func openFromPanel(", "@objc func openTeamConfig")
        self.assertRegex(opening, r"if isSelfTestRun\(\) \{\s*walkOpened\.append\(target\.absoluteString\)\s*\} else \{\s*NSWorkspace\.shared\.open\(target\)")


BLOCKS_SWIFT = SWIFT.parent / "BlocksWindow.swift"


def run_swift(lines):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "check.swift"
        f.write_text("import Foundation\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return subprocess.run(["swift", str(f)], capture_output=True, text=True, timeout=120)


class LinksOpenInBrowser(unittest.TestCase):
    """链接（11-14 用户实测打不开）：结果里的链接是“新窗口打开”，两个窗口都要接住，
    只把外部网页交给默认浏览器；本机服务地址（带口令）和其他开头的一律不打开。"""

    def setUp(self):
        self.main = SWIFT.read_text(encoding="utf-8")
        self.blocks = BLOCKS_SWIFT.read_text(encoding="utf-8")

    def test_rule_behaviour(self):
        body = swift_func(self.main, "externalWebLink")
        self.assertIsNotNone(body, "要有一个共用的判断 externalWebLink")
        if not shutil.which("swift"):
            self.skipTest("本机没有 swift")
        cases = {
            "https://example.com/a?b=1": True, "http://news.example.cn": True, "HTTPS://Example.com": True,
            "javascript:alert(1)": False, "file:///etc/passwd": False, "superagent://blocks": False,
            "http://127.0.0.1:18768/api/abilities/file?token=x": False, "http://localhost:8000/": False,
            "data:text/html,hi": False, "about:blank": False,
            "http://localhost.:18768/a": False, "http://0.0.0.0:18768/a": False, "http://127.1:18768/": False,
            "http://2130706433:18768/": False, "http://[::1]:18768/": False, "http://[0:0:0:0:0:0:0:1]:18768/": False,
            "http://[::ffff:127.0.0.1]:18768/": False, "http://[::ffff:0.1.2.3]/": False, "http://[::]:18768/": False, "http://a.localhost/": False,
            "http://user@127.0.0.1/": False, "https://example.com:8443/x": True, "https://192.168.1.10/": True,
            "https://127example.com/": True, "https://[2001:db8::1]/": True,
        }
        lines = [body]
        for i, raw in enumerate(cases):
            lines.append(f'print("{i}", externalWebLink(URL(string: "{raw}")) != nil)')
        lines.append('print("nil", externalWebLink(nil) != nil)')
        lines.append('print("same", externalWebLink(URL(string: "https://news.example.cn/a?b=1"))?.absoluteString == "https://news.example.cn/a?b=1")')
        out = run_swift(lines)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = dict(line.split(" ") for line in out.stdout.split("\n") if line)
        for i, (raw, want) in enumerate(cases.items()):
            self.assertEqual(got[str(i)], "true" if want else "false", raw)
        self.assertEqual(got["nil"], "false")
        self.assertEqual(got["same"], "true", "放行时交出去的就是原地址")

    def check_window(self, src, opener):
        m = re.search(r"func webView\(_ webView: WKWebView, createWebViewWith.*?\n    \}", src, re.S)
        self.assertIsNotNone(m, "要接住“新窗口打开”的请求")
        body = m.group(0)
        # 回调第一句就是打开判断，前面不能先 return（Codex 复查第 2 轮）
        self.assertRegex(body, r"windowFeatures: WKWindowFeatures\) -> WKWebView\? \{\n\s*if let target = externalWebLink")
        self.assertEqual(body.count("return"), 1, "只有最后一句 return nil")
        self.assertRegex(body, r"if let target = externalWebLink\(navigationAction\.request\.url\) \{ " + re.escape(opener) + r"\(target\) \}",
                         "只有外部网页才打开，且经过 openFromPanel（走查时只记下不真开）")
        self.assertRegex(body, r"\n\s*return nil\n", "不在应用里另开窗口")
        self.assertNotIn("NSWorkspace", body)

    def test_project_window(self):
        self.check_window(self.main, "openFromPanel")
        make_web = section(self.main, "func makeWeb(", "return view")
        self.assertIn("view.uiDelegate = self", make_web, "聊天、设置、左栏的页面都要接上")
        self.assertIn("web.uiDelegate = self", self.main, "右栏页面也要接上")

    def test_blocks_window(self):
        self.check_window(self.blocks, "app?.openFromPanel")
        self.assertIn("view.uiDelegate = self", section(self.blocks, "func show(", "makeKeyAndOrderFront"))


if __name__ == "__main__":
    unittest.main()
