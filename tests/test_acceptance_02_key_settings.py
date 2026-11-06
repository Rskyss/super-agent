"""02 号单：打开软件不弹密钥框，密钥入口在设置里。Swift 和页面用文字检查，真实窗口由 Claude 走查。"""
import unittest

from acceptance_helpers import STATIC, SWIFT, ServerCase


def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.swift = SWIFT.read_text(encoding="utf-8")

    def test_opening_the_app_does_not_ask_for_a_key(self):
        setup = section(self.swift, "func setupClerk()", "func clerkSelfTest")
        self.assertNotIn("askClerk()", setup)
        self.assertIn("--clerk-try", setup)
        self.assertIn("--clerk-ui-test", setup)

    def test_form_says_it_is_optional(self):
        ask = section(self.swift, "func askClerk()", "@objc func clerkSaveClicked")
        self.assertIn("配置行政人员", ask)
        self.assertNotIn("请先配置", ask)
        self.assertIn("不填也能继续用 Codex 聊天", ask)
        self.assertIn('addButton(withTitle: "取消")', ask)
        self.assertNotIn("runModal", ask)

    def test_settings_addresses_are_handled(self):
        self.assertIn('case "clerk-setup"', self.swift)
        self.assertIn('case "clerk-clear"', self.swift)
        self.assertIn("/api/clerk/clear", self.swift)
        self.assertIn("ClerkKey.delete()", self.swift)

    def test_settings_page_is_told_whether_the_clerk_is_ready(self):
        line = next(l for l in self.swift.splitlines() if "team.html?" in l)
        block = self.swift[self.swift.index(line) - 800:self.swift.index(line) + len(line)]
        self.assertIn("clerk=", block)


class PageTest(unittest.TestCase):
    def test_settings_page_has_the_clerk_section(self):
        html = (STATIC / "team.html").read_text(encoding="utf-8")
        for words in ("行政人员（可选）", 'id="clerkState"', 'id="clerkSetup"', 'id="clerkClear"'):
            self.assertIn(words, html)

    def test_settings_page_buttons_tell_the_window(self):
        js = (STATIC / "team.js").read_text(encoding="utf-8")
        self.assertIn("superagent://clerk-setup", js)
        self.assertIn("superagent://clerk-clear", js)


class ServerTest(ServerCase):
    def test_clear_forgets_the_key(self):
        self.server.clerk = object()
        status, _ = self.post("/api/clerk/clear", {})
        self.assertEqual(status, 200)
        self.assertIsNone(self.server.clerk)

    def test_clear_needs_the_window_token(self):
        self.server.clerk = marker = object()
        status, _ = self.post("/api/clerk/clear", {}, token="错的")
        self.assertEqual(status, 403)
        self.assertIs(self.server.clerk, marker)


if __name__ == "__main__":
    unittest.main()
