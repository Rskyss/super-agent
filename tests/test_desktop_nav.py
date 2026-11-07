"""左栏和三段窗口：规则层用 node 检查（没装 node 的电脑跳过）；页面结构和窗口地址用文字检查。"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"
SWIFT = HERE.parent / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


class NavPageTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则层检查")
    def test_dots_search_selection_and_track(self):
        proc = subprocess.run(["node", str(HERE / "nav_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    def test_nav_page_uses_the_shared_theme(self):
        html = (STATIC / "nav.html").read_text(encoding="utf-8")
        self.assertIn("/static/theme.css", html)
        self.assertIn("/static/nav.js", html)
        self.assertIn("主对话", html)

    def test_panel_has_no_cards_tab_any_more(self):
        html = (STATIC / "panel.html").read_text(encoding="utf-8")
        self.assertNotIn("tab-cards", html)
        self.assertNotIn("pane-cards", html)
        for removed in ('id="track"', 'id="elapsed"', 'id="title"', 'id="now"', 'id="round"'):
            self.assertNotIn(removed, html)
        self.assertIn('id="steps"', html)

    def test_nav_talks_to_the_window_through_addresses_it_handles(self):
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        swift = SWIFT.read_text(encoding="utf-8")
        for host in ("card", "main", "settings", "thread", "new"):
            self.assertIn(f"superagent://{host}", nav)
            self.assertIn(f'case "{host}"', swift)
        self.assertIn('case "saved"', swift)
        self.assertIn("window.SuperAgentPanel", swift)
        self.assertIn("window.SuperAgentNav", swift)

    def test_chat_page_is_only_the_conversation(self):
        html = (STATIC / "chat.html").read_text(encoding="utf-8")
        js = (STATIC / "chat.js").read_text(encoding="utf-8")
        for removed in ("historyBtn", "newChatBtn", "historyMenu"):
            self.assertNotIn(removed, html)
            self.assertNotIn(removed, js)
        self.assertIn("openThread", js)

    def test_left_column_names(self):
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        html = (STATIC / "nav.html").read_text(encoding="utf-8")
        self.assertIn('role="tablist"', html)
        self.assertIn("主对话", html)
        self.assertIn("等待处理", html)
        self.assertIn("等你处理", nav)
        self.assertNotIn("任务卡片", html + nav)

    def test_leaving_settings_works_with_only_the_clerk(self):
        """只配了行政没选调度员时，点“回到对话”也要离开设置页。"""
        swift = SWIFT.read_text(encoding="utf-8")
        body = swift[swift.index("func selectCard"):swift.index("func openClerkCard")]
        self.assertIn("if leftView == .config, Dispatcher.forProject(project) != nil || clerkReady {", body)

    def test_tab_click_only_changes_the_list(self):
        """用户定的：点标签中间不动，点列表里的一项才切换。"""
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        body = nav[nav.index("function setTab"):]
        body = body[:body.index("\n  }\n")]
        self.assertNotIn("tell(", body)

    def test_old_codex_conversations_are_gone(self):
        """2026-11-06 用户决定删掉“Codex 旧对话”。入口和背后的功能都不留。"""
        self.assertFalse((HERE.parent / "desktop" / "clerk" / "codex_history.py").exists())
        for path in (STATIC / "nav.html", STATIC / "nav.js", STATIC / "nav.css", STATIC / "chat.js",
                     HERE.parent / "desktop" / "server.py", SWIFT):
            source = path.read_text(encoding="utf-8")
            for word in ("codexOld", "Codex 旧对话", "openCodexHistory", "closeCodexHistory", "viewingCodex",
                         "codex_history", "/api/clerk/codex", "superagent://codex"):
                self.assertNotIn(word, source, f"{path.name} 里还有 {word}")

    def test_right_column_only_exists_for_a_chosen_card(self):
        """主对话里还不确定是不是任务，右栏不能显示任何卡片的流程。"""
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("let show = selectedCard != nil && rightWanted", swift)
        self.assertIn("rightToggle.isHidden = selectedCard == nil", swift)

    def test_window_listens_only_after_everything_is_built(self):
        """恢复上次窗口大小时会立刻发“窗口变了”，这时界面还没建好，不能接。"""
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertLess(swift.index("rightBox.isHidden = true\n        syncRight()"), swift.index("window.delegate = self"))

    def test_clicking_the_current_conversation_goes_through_the_window(self):
        """当前这段连失败时，再点要能重连；所以这一行不能绕开切换入口。"""
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertNotIn('data-current") === "1"', nav)

    def test_opening_settings_keeps_the_dispatcher_connected(self):
        """看设置不用让出对话，调度员继续干活；回复中或等确认时断开会丢内容。"""
        swift = SWIFT.read_text(encoding="utf-8")
        branch = swift[swift.index("if view == .config {"):swift.index("let target: LeftView")]
        self.assertNotIn("closeChatOnServer", branch)

    def test_terminal_asks_before_interrupting_the_dispatcher(self):
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("isWorking", swift)
        self.assertIn("切到终端会中断这一轮", swift)
        change = swift[swift.index("func segmentChanged()"):swift.index("// MARK: 右边：本机数据服务")]
        self.assertIn("switchToTerminal()", change)
        menu = swift[swift.index("func openTerminalAfterWarning"):]
        self.assertIn("switchToTerminal()", menu.split("\n")[0])
        self.assertIn("openTerminalAfterWarning()", swift[swift.index("@objc func showTerminalView"):swift.index("func openTerminalAfterWarning")])

    def test_leaving_settings_for_terminal_still_frees_the_conversation(self):
        swift = SWIFT.read_text(encoding="utf-8")
        branch = swift[swift.index("let target: LeftView"):swift.index("func stopTerminal()")]
        self.assertIn("let chatOpen = leftView != .terminal", branch)
        self.assertIn("if chatOpen {", branch)

    def test_saving_settings_does_not_interrupt_running_work(self):
        swift = SWIFT.read_text(encoding="utf-8")
        saved = swift[swift.index('case "saved":'):swift.index('case "card":')]
        self.assertIn("whenChatIdle", saved)
        self.assertIn("switchToTerminal(", saved, "换成只能用终端的调度员时，也要先提示")
        self.assertIn("self.leftView == .config", saved, "等关连接的几秒里用户可能已经走开了")
        self.assertIn("self.project == place", saved)

    def test_prompt_shows_the_current_view_until_confirmed(self):
        """弹窗时左上角不能已经高亮终端，看起来像切过去了。"""
        swift = SWIFT.read_text(encoding="utf-8")
        body = swift[swift.index("func switchToTerminal"):swift.index("func confirmInterrupt")]
        self.assertLess(body.index("self.segment.selectedSegment ="), body.index("self.confirmInterrupt()"))

    def test_every_way_to_the_terminal_asks_first(self):
        swift = SWIFT.read_text(encoding="utf-8")
        for name in ("@objc func continueChat", "@objc func newChat"):
            body = swift[swift.index(name):]
            body = body[:body.index("\n    }\n")]
            self.assertIn("switchToTerminal(", body)
            self.assertNotIn("showLeft(.terminal", body)

    def test_terminal_prompt_lives_in_the_one_place_every_path_goes_through(self):
        """入口一个个补总会漏，提示放在 showLeft 里。"""
        swift = SWIFT.read_text(encoding="utf-8")
        body = swift[swift.index("func showLeft("):swift.index("func stopTerminal()")]
        gate = body[body.index("let target: LeftView"):body.index("if target == .chat {")]
        self.assertIn("switchToTerminal(opening: opening, chatMode: chatMode)", gate)
        self.assertIn("!leaveConfirmed", gate)

    def test_settings_only_keeps_a_chat_page_that_really_loaded(self):
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("if leftView == .chat, chatLoadedKey != nil { chatKept = true }", swift)
        stopped = swift[swift.index("func serverStopped"):]
        self.assertIn("chatKept = false", stopped[:stopped.index("\n    }\n")])
        self.assertIn("port = nil", stopped[:stopped.index("\n    }\n")], "旧端口留着，重启后聊天页会卡死")

    def test_codex_terminal_gets_its_role_without_an_automatic_message(self):
        """自动发话要花额度；Codex 的角色走启动设置，只有第一次接入才发话。"""
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("developer_instructions", swift)
        start = swift[swift.index("func startTerminal"):]
        start = start[:start.index("let after =")]
        self.assertIn('(firstTime || who.saved != "codex")', start)
        self.assertIn("role: role", start)

    def test_saved_list_only_fills_an_empty_list(self):
        """存好的选中只用于刚打开；点新对话后不能又选回上一段。"""
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("if (d.cached && threads.length) return;", nav)

    def test_terminal_only_dispatcher_gets_no_conversation_list(self):
        """换成只能用终端的调度员后，那些行打不开，不该显示。"""
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn('params.get("chat") !== "1"', nav)
        self.assertIn("hasChat", swift[swift.index("func loadPanel"):swift.index("func loadPanel") + 900])

    def test_list_refreshes_soon_after_connecting(self):
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("d.cached", nav)
        self.assertIn("WARM_MS", nav)

    def test_coming_back_from_settings_does_not_reconnect(self):
        """连接一直在，页面也一直在收消息，重连只会把消息再放一遍。"""
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("chatKept", swift)
        branch = swift[swift.index("if target == .chat {"):swift.index("let chatOpen = leftView")]
        self.assertIn("if chatKept", branch)

    def test_self_test_also_restores_the_saved_window_size(self):
        """自测不能绕开用户每次打开都会走的路。"""
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertNotIn("if !selfTest { window.setFrameAutosaveName", swift)


if __name__ == "__main__":
    unittest.main()
