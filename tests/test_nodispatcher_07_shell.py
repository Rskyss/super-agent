"""去掉调度员 D7：窗口外壳里用户在行政路径上会看到的几处文字不再提调度员（外壳是冻结的，改了要重装才生效）。"""
import re
import unittest
from pathlib import Path

SWIFT = (Path(__file__).resolve().parent.parent / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"
         ).read_text(encoding="utf-8")


def body(name: str) -> str:
    match = re.search(r"func " + re.escape(name) + r"\([^)]*\)[^{]*\{(.*?)\n    \}\n", SWIFT, re.S)
    assert match, name
    return match.group(1)


class ShellWords(unittest.TestCase):
    def test_window_title_is_just_the_project(self):
        text = body("updateTitle")
        self.assertNotIn("调度员", text)
        self.assertIn('"开发助手 · " + project.lastPathComponent', text)

    def test_connecting_placeholder_does_not_name_a_dispatcher(self):
        self.assertNotIn("正在连接调度员", SWIFT)
        self.assertEqual(SWIFT.count('"正在连接…"'), 2)

    def test_a_dispatcher_left_in_the_old_config_is_not_read(self):
        # 行政必配：老配置、老设置里残留的调度员不再让窗口走旧聊天；只剩走查脚本用的 --dispatcher
        text = body("forProject")
        self.assertIn('argValue("--dispatcher")', text)
        for banned in ("配置.json", "UserDefaults", "team"):
            self.assertNotIn(banned, text)

    def test_turning_off_the_clerk_does_not_promise_a_dispatcher_chat_for_a_new_project(self):
        text = body("confirmClearClerk")
        self.assertIn("没有可以对话的对象", text)
        self.assertIn("Dispatcher.forProject(project) == nil", text)

    def test_saving_on_the_team_page_stays_there_when_the_clerk_is_not_ready(self):
        # 行政必配由外壳一处把关（它才知道行政是不是真的接通了）：没配好就回到团队页，页面带 need_clerk 提示
        start = SWIFT.index('case "saved":')
        text = SWIFT[start:SWIFT.index('case "card":', start)]
        self.assertIn("!clerkReady", text)
        self.assertIn("loadConfig(needClerk: true)", text)
        self.assertLess(text.index("loadConfig(needClerk: true)"), text.index("whenChatIdle"))
        self.assertNotIn("UserDefaults.standard.set(who", text, "页面不再送调度员过来")
        self.assertIn("need_clerk", body("loadConfig"))

    def test_new_and_continue_chat_cannot_go_around_the_clerk_requirement(self):
        for name in ("newChat", "continueChat"):
            text = body(name)
            first = text.strip().splitlines()[0]
            self.assertIn("clerkReady", first, name)
            self.assertIn(".config", text.strip().splitlines()[0] + text.strip().splitlines()[1], name)

    def test_chat_menu_items_follow_the_clerk_but_the_terminal_stays_off(self):
        text = body("validateMenuItem")
        chat = text[text.index("#selector(continueChat(_:))"):text.index("case #selector(openRecent")]
        self.assertIn("clerkReady", chat)
        terminal = text[text.index("#selector(showTerminalView(_:))"):]
        self.assertIn("return who != nil", terminal.split("default:")[0])

    def test_the_window_tells_the_page_once_the_clerk_is_really_connected(self):
        text = body("afterClerkKeyChange")
        self.assertIn("clerkConnected", text)
        self.assertIn("clerkReady", text)

    def test_key_dialog_does_not_promise_a_codex_chat_fallback(self):
        self.assertNotIn("不填也能继续用 Codex 聊天", SWIFT)
        self.assertIn("行政是对话的入口，不配就没法在这里聊天", SWIFT)

    def test_clicking_the_team_button_again_closes_the_team_page(self):
        # 用户 11-17：点“团队”打开设置页后，再点一次“团队”就是关掉（以前只能靠页面里的“保存并返回对话”出来）
        start = SWIFT.index('case "settings":')
        text = SWIFT[start:SWIFT.index('case "clerk-apply":', start)]
        self.assertIn("leftView == .config", text)
        self.assertIn("showLeft(preferredLeft(), opening: false)", text, "在设置页再点一次：回到对话")
        self.assertIn("showLeft(.config, opening: false)", text, "不在设置页：照旧打开")
        # 行政必配：行政没配好时没有可回的对话，留在团队页并说明，不能借“关掉”绕过去
        self.assertIn("clerkReady", text)
        # 这条路没保存任何东西，页面提示里不能说“团队已保存”（need_clerk=2，保存后回对话才是 1）
        self.assertIn("loadConfig(needClerk: true, afterSave: false)", text)

    def test_a_save_result_that_arrives_after_the_page_was_closed_is_ignored(self):
        # Codex 复查：行政“测试并保存”期间再点“团队”关掉设置页，保存结果随后才到，不能再把聊天连接断掉却没人接回去
        start = SWIFT.index('case "saved":')
        text = SWIFT[start:SWIFT.index('case "card":', start)]
        self.assertIn("leftView != .config", text)
        self.assertLess(text.index("leftView != .config"), text.index("whenChatIdle"))
        self.assertLess(text.index("leftView != .config"), text.index("closeChatOnServer"))

    def test_need_clerk_two_means_nothing_was_saved(self):
        text = body("loadConfig")
        self.assertIn("afterSave", text)
        self.assertIn("need_clerk=", text)


if __name__ == "__main__":
    unittest.main()
