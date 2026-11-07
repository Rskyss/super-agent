"""聊天页面：记账层用 node 检查（没装 node 的电脑跳过）；页面文件能从本机服务打开。"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class ChatPageTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面记账层检查")
    def test_chat_bookkeeping_and_safety(self):
        proc = subprocess.run(["node", str(HERE / "chat_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    def test_chat_page_files_exist_and_load_the_script(self):
        static = HERE.parent / "desktop" / "static"
        html = (static / "chat.html").read_text(encoding="utf-8")
        self.assertIn("/static/chat.js", html)
        self.assertTrue((static / "chat.css").exists())

    def test_unified_dark_theme_and_cleaned_copy(self):
        static = HERE.parent / "desktop" / "static"
        theme = (static / "theme.css").read_text(encoding="utf-8")
        for token in ("--bg:", "#17181b", "--bg-side:", "#131417", "--surface:", "#212327",
                      "--surface-2:", "#2b2d33", "--surface-3:", "#3a3d44", "--me:", "#2f3238",
                      "--line:", "#2a2c31", "--line-2:", "#373a41", "--text:", "#ecedef",
                      "--dim:", "#a0a4ad", "--accent:", "#f0a23b", "--on-accent:", "#1d1403",
                      "--wait:", "#7fb2f0", "--ok:", "#62cc93", "--ok-bg:", "#1f3a2c", "--bad:", "#f0776c"):
            self.assertIn(token, theme)

        for page in ("chat.html", "panel.html", "team.html"):
            content = (static / page).read_text(encoding="utf-8")
            self.assertIn("/static/theme.css", content, f"{page} 应引入 theme.css")

        for style in ("chat.css", "panel.css", "team.css"):
            css = (static / style).read_text(encoding="utf-8").lower()
            for old_blue in ("#0b1220", "#0d1524", "#101a2b", "#15233a", "#1f2d44", "#26364f"):
                self.assertNotIn(old_blue, css, f"{style} 不应残留旧蓝色 {old_blue}")

        chat_html = (static / "chat.html").read_text(encoding="utf-8")
        self.assertNotIn("Shift + Enter", chat_html)
        self.assertIn('placeholder="跟调度员说…"', chat_html)

        panel_html = (static / "panel.html").read_text(encoding="utf-8")
        panel_js = (static / "panel.js").read_text(encoding="utf-8")
        self.assertNotIn("点头像，看这个人的全部交接", panel_html)
        self.assertNotIn("点头像，看这个人的全部交接", panel_js)
        self.assertNotIn("在左边对调度员说", panel_js)

        team_html = (static / "team.html").read_text(encoding="utf-8")
        self.assertIn("<h1>团队</h1>", team_html)
        self.assertNotIn("团队与模型设置", team_html)
        self.assertNotIn("<small>", team_html)
        self.assertNotIn("<h2>", team_html)

    def test_coming_back_from_terminal_stays_on_the_same_conversation(self):
        """从终端或设置回来不能连到最新那段，否则左栏选中的和实际聊的不是同一段。"""
        chat_js = (Path(__file__).resolve().parent.parent / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        resume = chat_js[chat_js.index("function resume()"):chat_js.index("function switchSession")]
        self.assertIn('switchSession("continue", false, currentThread)', resume)
        self.assertNotIn("/api/chat/open", resume, "回来和切换必须走同一条路，分开写过三次都出了问题")

    def test_blank_new_chat_starts_over_when_coming_back(self):
        """没说过话的新对话没有存档，回来时开一段新的，不去接它。"""
        chat_js = (Path(__file__).resolve().parent.parent / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        resume = chat_js[chat_js.index("function resume()"):chat_js.index("function switchSession")]
        self.assertIn("blankChat", resume)
        self.assertIn('switchSession("fresh", false, null)', resume)

    def test_working_means_the_server_says_busy(self):
        """没人回复的旧确认卡不算干活，否则切终端会误报。"""
        chat_js = (Path(__file__).resolve().parent.parent / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        line = chat_js[chat_js.index("isWorking:"):].split("\n")[0]
        self.assertIn("model.busy", line)
        self.assertNotIn("ask", line)

    def test_background_sync_keeps_its_place_in_the_event_stream(self):
        """后台连接结束后要接着它的编号取事件，否则报错会显示两遍。"""
        chat_js = (Path(__file__).resolve().parent.parent / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertEqual(chat_js.count("model.seq = pendingModel.seq"), 2)


if __name__ == "__main__":
    unittest.main()
