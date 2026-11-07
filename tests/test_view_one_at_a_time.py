"""中间只显示一种界面：不再并排放“聊天 | 终端”，终端挪进菜单（2026-11-07 用户选 A）。"""
import unittest

from acceptance_helpers import SWIFT


class ViewRules(unittest.TestCase):
    def setUp(self):
        self.swift = SWIFT.read_text(encoding="utf-8")

    def section(self, start, end):
        return self.swift[self.swift.index(start):self.swift.index(end)]

    def test_switch_is_gone_from_the_top_bar(self):
        self.assertIn("segment.isHidden = true", self.section("func buildTopBar", "func updateViewSwitch"))

    def test_terminal_lives_in_the_menu(self):
        menu = self.swift[self.swift.index('submenu("对话"'):self.swift.index('submenu("面板"')]
        self.assertIn('"打开终端（高级）"', menu)
        self.assertNotIn('"聊天视图"', menu)
        self.assertNotIn('"终端视图"', menu)

    def test_way_back_only_when_there_is_a_chat_to_go_back_to(self):
        self.assertIn("backToChat.isHidden = !(leftView == .terminal && canChat)", self.swift)
        self.assertIn('NSButton(title: "返回聊天"', self.swift)
        self.assertIn("syncBackToChat()", self.swift[self.swift.index("func showLeft"):self.swift.index("func showLeft") + 200])

    def test_first_open_says_what_the_terminal_is(self):
        body = self.section("@objc func showTerminalView", "func openTerminalAfterWarning")
        self.assertIn("terminalWarned", body)
        self.assertIn("beginSheetModal", body)
        self.assertIn("也不留在卡片记录里", body)
        self.assertIn("不经过行政", body)

    def test_terminal_only_dispatcher_is_not_warned(self):
        body = self.section("@objc func showTerminalView", "func openTerminalAfterWarning")
        self.assertIn("canChat", body)

    def test_second_trigger_or_project_change_cannot_restart_a_terminal(self):
        body = self.section("@objc func showTerminalView", "func openTerminalAfterWarning")
        self.assertIn("window.attachedSheet == nil", body)
        self.assertIn("self.project == place", body)
        self.assertIn("self.leftView != .terminal", body)
        self.assertIn("Dispatcher.forProject(self.project) != nil", body)

    def test_new_and_continue_prefer_the_chat(self):
        for name, end in (("@objc func continueChat", "@objc func newChat"), ("@objc func newChat", "@objc func showChatView")):
            body = self.section(name, end)
            self.assertIn("preferredLeft() == .chat", body, name)
            self.assertLess(body.index("preferredLeft() == .chat"), body.index("switchToTerminal("), name)

    def test_way_back_follows_the_clerk_becoming_ready(self):
        self.assertRegex(self.swift, r"var clerkReady = false \{[^\n]*\n\s+didSet \{ syncBackToChat\(\) \}")
        self.assertIn("if backToChat == nil { return }", self.section("func syncBackToChat", "func showLeft"))

    def test_chat_messages_do_not_mention_the_old_switch(self):
        root = SWIFT.parents[3]
        for path in (root / "dispatchers" / "codex.py", root / "chat.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("切到终端视图", source, path.name)
            self.assertNotIn("请用终端视图", source, path.name)

    def test_interrupt_question_stays(self):
        self.assertIn("confirmInterrupt", self.section("func switchToTerminal", "func confirmInterrupt"))

    def test_lost_connection_points_to_the_menu(self):
        self.assertIn("聊天连接停止了。可以从菜单“对话 → 打开终端（高级）”继续。", self.swift)
        self.assertNotIn("切到终端视图继续", self.swift)


if __name__ == "__main__":
    unittest.main()
