"""左下角切换项目的补充测试：只测验收口径，不规定实现细节。"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "desktop" / "static"
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


def swift_text():
    return SWIFT.read_text(encoding="utf-8")


def block_after(text, start):
    start_at = text.find(start)
    if start_at < 0:
        return ""
    brace_at = text.find("{", start_at)
    if brace_at < 0:
        return ""
    depth = 0
    for index in range(brace_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_at:index + 1]
    return ""


def case_body(text, name):
    match = re.search(r'case "%s":(.*?)(?=\n\s*case "|\n\s*default:)' % re.escape(name), text, re.S)
    return match.group(1) if match else ""


def css_rule(css, selector):
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return ""


class RuleLayerExtraTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过规则层检查")
    def test_project_rules_and_popup_behavior(self):
        proc = subprocess.run(["node", str(HERE / "projswitch_extra_page_check.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


class NavMarkupExtraTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "nav.html").read_text(encoding="utf-8")
        self.css = (STATIC / "nav.css").read_text(encoding="utf-8")

    def test_button_wraps_name_and_menu_sits_above_footer(self):
        foot = self.html[self.html.find("<footer"):self.html.find("</footer>")]
        button = re.search(r'<button\b[^>]*\bid="projBtn"[^>]*>(.*?)</button>', foot, re.S)
        self.assertTrue(button, "左下角要是按钮")
        name = re.search(r'<span\b[^>]*\bid="proj"[^>]*>.*?</span>', button.group(1), re.S)
        arrow = re.search(r"<svg\b.*?</svg>", button.group(1), re.S)
        self.assertTrue(name, "项目名还在按钮里")
        self.assertTrue(arrow, "项目名后面有箭头")
        self.assertLess(name.start(), arrow.start(), "箭头放在项目名后面")

        aside = self.html[self.html.find("<aside"):self.html.find("</aside>")]
        menu_at = aside.find('id="projMenu"')
        foot_at = aside.find("<footer")
        self.assertGreater(menu_at, 0)
        self.assertLess(menu_at, foot_at, "浮层放在左栏底部按钮上方")
        self.assertRegex(aside, r'id="projMenu"[^>]*hidden', "浮层默认隐藏")
        self.assertIn('id="projQ"', aside)
        self.assertIn('placeholder="搜索项目"', aside)
        self.assertIn('aria-label="搜索项目"', aside)
        self.assertIn('id="projList"', aside)
        self.assertIn('id="projAdd"', aside)

    def test_popup_layout_and_list_styles(self):
        nav_rule = css_rule(self.css, ".nav")
        self.assertRegex(nav_rule, r"(?:^|\s)position\s*:\s*relative\b", "浮层要挂在左栏内")
        button = css_rule(self.css, ".proj-btn")
        self.assertRegex(button, r"\bborder\s*:\s*(?:0|none)\b", "项目按钮无边框")
        self.assertRegex(button, r"\bbackground\s*:\s*(?:none|transparent)\b", "项目按钮默认不铺底色")
        hover = css_rule(self.css, ".proj-btn:hover")
        self.assertRegex(hover, r"\bbackground\s*:", "项目按钮 hover 要变亮")

        menu = css_rule(self.css, ".proj-menu")
        self.assertRegex(menu, r"\bposition\s*:\s*absolute\b", "浮层要绝对定位")
        self.assertRegex(menu, r"\bbottom\s*:", "浮层要从按钮上方弹出")
        self.assertTrue(
            re.search(r"\bleft\s*:\s*8px\b.*\bright\s*:\s*8px\b", menu, re.S)
            or re.search(r"\bwidth\s*:\s*calc\(100%\s*-\s*16px\)", menu),
            "浮层左右留 8px"
        )
        self.assertRegex(menu, r"\bborder-radius\s*:\s*12px\b")
        self.assertRegex(menu, r"\b(?:box-shadow|filter)\s*:")
        self.assertRegex(menu, r"\bbackground\s*:")
        self.assertRegex(menu, r"\bz-index\s*:")

        list_rule = next((css_rule(self.css, selector) for selector in
                          ("#projList", ".proj-list", ".proj-menu #projList", ".proj-menu .proj-list") if css_rule(self.css, selector)), "")
        self.assertTrue(list_rule, "项目列表要有独立滚动区域")
        self.assertRegex(list_rule, r"\bmax-height\s*:\s*(?:252px|calc\(36px\s*\*\s*7\))\b", "列表约七行")
        self.assertRegex(list_rule, r"\boverflow-y\s*:\s*(?:auto|scroll)\b", "超出要滚动")

        item = css_rule(self.css, ".proj-item")
        self.assertRegex(item, r"\b(?:height|min-height)\s*:\s*3[2-8]px\b", "项目行约 36px")
        self.assertRegex(item, r"\bwidth\s*:\s*100%")
        self.assertRegex(item, r"\bborder\s*:\s*(?:0|none)\b")
        item_hover = css_rule(self.css, ".proj-item:hover")
        item_on = css_rule(self.css, ".proj-item.on")
        self.assertRegex(item_hover, r"\bbackground\s*:")
        self.assertRegex(item_on, r"\bbackground\s*:")


class ShellExtraTest(unittest.TestCase):
    def setUp(self):
        self.swift = swift_text()

    def test_project_switch_ignores_same_and_unknown_paths(self):
        body = case_body(self.swift, "project")
        self.assertTrue(body, "外壳要接项目切换地址")
        self.assertIn("canSwitchTo(", body)
        self.assertIn("switchTo(", body)
        same = re.search(r"(?:if|guard)[^\n]*path\s*==\s*project(?:\.path|\?\.path)", body)
        self.assertTrue(same, "点当前项目不能重新切换")

    def test_nav_project_list_is_encoded_json_from_recent_list(self):
        load = block_after(self.swift, "func loadPanel(")
        self.assertIn("recentProjectList()", load)
        self.assertIn("JSONSerialization", load)
        self.assertIn("urlEncode(", load)
        self.assertIn("&projects=", load)
        self.assertIn("&token=", load, "右栏和左栏原有 token 参数不能丢")

    def test_place_is_saved_for_old_project_and_card_selection_keeps_other_fields(self):
        select = block_after(self.swift, "func selectCard(")
        self.assertIn("selectedCard = id", select)
        self.assertIn("placeKey(for: project)", select, "选卡片只更新当前项目记录里的卡片")
        self.assertIn('"card"', select)

        switch = block_after(self.swift, "func switchTo(")
        self.assertIn("SuperAgentChat.place", switch)
        self.assertIn("placeKey(for: project)", switch, "切走前要存旧项目的位置")
        self.assertIn("selectedCard", switch)
        old_place = switch.find("placeKey(for: project)")
        new_project = switch.find("project = url")
        old_card = switch.find("selectedCard")
        no_card = switch.find("selectedCard = nil")
        self.assertTrue(old_place >= 0 and new_project >= 0 and old_place < new_project, "位置存给旧项目")
        self.assertTrue(old_card >= 0 and no_card >= 0 and old_card < no_card, "卡片也要在清空前存")

    def test_switch_waits_once_and_keeps_original_flow_order(self):
        switch = block_after(self.swift, "func switchTo(")
        self.assertIn("evaluateJavaScript", switch)
        self.assertTrue(
            re.search(r"deadline\s*:\s*\.now\(\)\s*\+\s*(?:1(?:\.0)?|1\.0)", switch),
            "聊天页最多等一秒"
        )
        self.assertTrue(re.search(r"var\s+\w+\s*=\s*false", switch), "后续动作要有只执行一次的标记")

        between = self.swift[self.swift.find("func switchTo("):self.swift.find("func remember(")]
        self.assertIn("/api/clerk/preview", between, "切项目前仍要清理旧预览")
        self.assertIn("project = url", between)
        self.assertIn("applyClerkSetting", between)
        self.assertIn("showLeft(firstLeft()", between)
        self.assertLess(between.find("/api/clerk/preview"), between.find("project = url"))
        self.assertLess(between.find("project = url"), between.find("applyClerkSetting"))
        self.assertLess(between.find("applyClerkSetting"), between.find("showLeft(firstLeft()"))

    def test_pending_card_waits_for_chat_load(self):
        self.assertIn("var pendingPlaceCard: String?", self.swift)
        finish = block_after(self.swift, "func webView(_ webView: WKWebView, didFinish")
        self.assertTrue(finish, "聊天页加载完成要有回调")
        self.assertIn("webView === chatWeb", finish, "只等聊天页，不等别的页面")
        self.assertIn("pendingPlaceCard", finish)
        clear = re.search(r"pendingPlaceCard\s*=\s*nil", finish)
        select = re.search(r"selectCard\(", finish)
        self.assertTrue(clear and select and clear.start() < select.start(), "先清标记再选卡，避免重复打开")

    def test_startup_restores_saved_card_only_without_card_argument(self):
        launch = block_after(self.swift, "func applicationDidFinishLaunching(")
        self.assertIn('selectedCard = argValue("--card")', launch, "自测 --card 行为不变")
        self.assertIn("argValue(\"--card\") == nil", launch, "没带 --card 才按记录恢复卡片")
        self.assertIn("pendingPlaceCard", launch)


class ChatPageExtraTest(unittest.TestCase):
    def setUp(self):
        self.chat = (STATIC / "chat.js").read_text(encoding="utf-8")

    def test_place_api_reports_clerk_place_and_blank_draft_for_card(self):
        start = self.chat.find("window.SuperAgentChat = {")
        api = self.chat[start:self.chat.find("\n  };", start)] if start >= 0 else ""
        self.assertIn("place: function", api, "聊天页要能报当前位置")
        # Claude 接手（复查第 3 轮第 1 条）：看卡时报主对话那段和放在一边的主对话草稿，不报卡片对话里的字、也不报空
        self.assertIn("placeNow()", api)
        place_now = block_after(self.chat, "function placeNow(")
        self.assertIn('viewingCard ? { thread: mainThread || "", draft: mainDraft }', place_now)

    def test_restore_uses_rule_layer_and_adjusts_input_height(self):
        start = self.chat.find('fetch("/api/clerk/list?"')
        block = self.chat[start:start + 1600] if start >= 0 else ""
        self.assertIn("ChatCore.restorePlace", block)
        self.assertIn('params.get("thread")', block)
        self.assertIn('params.get("draft")', block)
        self.assertIn("clerkOpen(r.open)", block)
        self.assertIn('$("input").value = r.draft', block)
        self.assertIn("resize()", block, "恢复草稿后输入框高度要跟着变")
        # 已经开卡时不能被列表拉回主对话：只记下回去时接哪段、放回哪些字（Claude 接手改）
        self.assertRegex(block, r"if \(viewingCard\) \{[^}]*mainThread = r\.open;[^}]*mainDraft = r\.draft;")

    def test_place_reporting_is_clerk_only_and_capped(self):
        self.assertIn("clerkMode && !butlerMode", self.chat, "积木库管家不报位置")
        self.assertIn("!viewingCard", self.chat, "看卡时不报行政输入框草稿")
        self.assertIn("800", self.chat, "输入停下 800 毫秒再报")
        self.assertRegex(self.chat, r"(?:slice|substring)\(0,\s*2000\)")
        clerk_open = block_after(self.chat, "function clerkOpen(")
        self.assertIn("superagent://place", self.chat)
        self.assertTrue(
            "superagent://place" in clerk_open or re.search(r"\b\w*place\w*\(\)", clerk_open, re.I),
            "切换对话后也要报一次位置"
        )


if __name__ == "__main__":
    unittest.main()
