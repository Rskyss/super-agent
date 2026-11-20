"""切换项目第四轮复查退回的回归测试：一条退回对应一个测试。"""
import re
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "desktop" / "static"
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


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


def css_rule(css, selector):
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return ""


class ProjectSwitchReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.swift = SWIFT.read_text(encoding="utf-8")
        self.chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.nav_html = (STATIC / "nav.html").read_text(encoding="utf-8")
        self.nav_css = (STATIC / "nav.css").read_text(encoding="utf-8")

    def test_pending_card_restore_is_scoped_to_its_project_and_shows_right_column(self):
        pending_state = re.search(
            r"pendingPlace\w*[\s\S]{0,500}(?:project|url)[\s\S]{0,500}card",
            self.swift,
            re.I,
        )
        self.assertTrue(pending_state, "待恢复的卡片要和项目放在一起，不能只是一张卡编号")

        finish_block = block_after(self.swift, "func webView(_ webView: WKWebView, didFinish")
        self.assertTrue(finish_block, "聊天页加载完成回调要存在")
        # Claude 接手：停用行政时不在设置页恢复卡（见 review4 的说明），只守住“待恢复的卡号不丢”
        for name in ("func switchTo(", "func flushChatPlace("):
            self.assertIn("cardToRemember()", block_after(self.swift, name),
                          "卡还没恢复好就切走或退出，记下的仍是那张卡（复查第 3 轮第 3 条）")

    def test_switching_before_chat_restore_keeps_saved_draft(self):
        api_at = self.chat.find("window.SuperAgentChat = {")
        place_api = self.chat[api_at:self.chat.find("\n  };", api_at)] if api_at >= 0 else ""
        not_ready = re.search(
            r"if\s*\(\s*!\s*(?:placeReady|placeLoaded|placeRestored|restoreReady|restored)\s*\)",
            place_api,
        )
        reports_ready = re.search(
            r"\b(?:placeReady|placeLoaded|placeRestored|restoreReady|ready)\s*:",
            place_api,
        )
        # Claude 接手：改成“没恢复好时只有用户打过字才报”，实际效果由 projswitch_extra_claude_dom.js 第 6 段实跑守住
        ready_first = re.search(r"if\s*\(\s*placeReady\s*\)\s*return placeNow\(\);\s*if\s*\(\s*!placeTouched\s*\)\s*return null;", place_api)
        self.assertTrue(
            not_ready or reports_ready or ready_first,
            "聊天列表没回来前，place() 不能把还没恢复的空输入框当成可用位置",
        )

    def test_sent_message_does_not_stay_as_saved_draft(self):
        submit = block_after(self.chat, '$("composer").addEventListener("submit"')
        clear_at = submit.find('box.value = "";')
        self.assertGreaterEqual(clear_at, 0, "发送时要清空输入框")
        after_clear = submit[clear_at:clear_at + 400]
        self.assertRegex(
            after_clear,
            r"(?:reportPlace\(|savePlaceNow|flushPlace)\s*\(",
            "发送清空输入框后要立刻把空草稿存下去，不能等旧的 800 毫秒定时器",
        )

    def test_project_popup_closes_when_another_column_is_clicked(self):
        nav_closes_on_blur = re.search(
            r'(?:window|document)\.addEventListener\(\s*["\'](?:blur|focusout|visibilitychange)["\']'
            r"[\s\S]{0,250}setProjectMenu\(false\)",
            self.nav,
        )
        shell_closes_across_columns = (
            "closeProjectMenu" in self.nav
            and re.search(r"(?:NSEvent|leftMouseDown|closeProjectMenu)", self.swift)
        )
        self.assertTrue(
            nav_closes_on_blur or shell_closes_across_columns,
            "点聊天等其他栏时，左栏项目浮层也要关掉",
        )

    def test_project_button_arrow_points_down(self):
        foot = self.nav_html[self.nav_html.find("<footer"):self.nav_html.find("</footer>")]
        path = re.search(r'<path\b[^>]*\bd="([^"]+)"', foot)
        self.assertTrue(path, "左下角项目按钮要有箭头")
        numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path.group(1))]
        self.assertGreaterEqual(len(numbers), 6, "箭头路径要有三个点")
        _, first_y = numbers[0], numbers[1]
        _, middle_y = numbers[2], numbers[3]
        _, last_y = numbers[4], numbers[5]
        self.assertLess(first_y, middle_y, "箭头左边要往下走")
        self.assertLess(last_y, middle_y, "箭头右边要往上收，尖端朝下")

    def test_project_popup_highlight_uses_theme_colors(self):
        for selector in (".proj-btn:hover", ".proj-item:hover", ".proj-item.on", ".proj-add:hover"):
            background = css_rule(self.nav_css, selector)
            self.assertRegex(
                background,
                r"background\s*:\s*var\(--[^)]+\)",
                f"{selector} 的亮底色要来自主题变量",
            )
            self.assertNotRegex(background, r"rgba\(", f"{selector} 不能写死 rgba 颜色")

    def test_draft_limit_does_not_split_a_character(self):
        report = block_after(self.chat, "function reportPlace(")
        self.assertFalse(
            re.search(r"\.slice\(0,\s*2000\)", report),
            "不能按 UTF-16 下标截断，表情可能被切成半个",
        )
        safe_limit = (
            re.search(r"Array\.from\([^)]*\)\.slice\(0,\s*2000\)", report)
            or re.search(r"\b(?:truncateDraft|safePrefix|sliceCodePoints)\s*\(", report)
        )
        self.assertTrue(safe_limit, "截到 2000 字时要按完整字符截断")


if __name__ == "__main__":
    unittest.main()
