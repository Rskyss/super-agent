"""项目切换第四轮复查退回：一条退回对应一个测试。"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"
STATIC = ROOT / "desktop" / "static"


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


class ProjectSwitchReview4Regressions(unittest.TestCase):
    def setUp(self):
        self.swift = SWIFT.read_text(encoding="utf-8")
        self.chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.nav = (STATIC / "nav.js").read_text(encoding="utf-8")

    # Claude 接手（11-20）：“停用行政时也在团队设置页恢复卡片”这个边角两轮退回、第二轮改出了设置页右栏不收起的新问题，
    # 按“同一角第二轮就砍”的规矩砍掉。停用行政的项目切回来停在团队设置页、不恢复卡；卡号留着，等聊天页加载完再恢复。
    # 原来的两条测试改成守住这个取舍。
    def test_restored_card_is_scoped_to_project_and_waits_for_chat(self):
        finish = block_after(self.swift, "func webView(_ webView: WKWebView, didFinish")
        self.assertIn("pendingPlaceProject == project.path", finish, "只打开这个项目自己记下的卡")
        self.assertIn("selectCard(card)", finish)
        self.assertNotIn("func restorePendingCardWithoutChat(", self.swift, "不再在团队设置页里恢复卡")

    def test_settings_page_keeps_its_original_look(self):
        sync_right = block_after(self.swift, "func syncRight(")
        show_line = re.search(r"let\s+show\s*=\s*([^\n]+)", sync_right)
        self.assertTrue(show_line)
        self.assertIn("leftView != .config", show_line.group(1), "进团队设置页时右栏照旧收起（复查第 3 轮第 2 条）")
        nav_mode = block_after(self.swift, "func navMode(")
        self.assertRegex(nav_mode, r'leftView\s*==\s*\.config\s*\?\s*"settings"', "在团队设置页时“团队”按钮照旧点亮")
        remember = block_after(self.swift, "func cardToRemember(")
        self.assertIn("pendingPlaceCard", remember, "停在团队设置页时，待恢复的卡号不能被切走时记成空")

    def test_delayed_thread_restore_keeps_text_typed_before_list_loads(self):
        list_start = self.chat.find('fetch("/api/clerk/list?"')
        list_block = self.chat[list_start:list_start + 1800] if list_start >= 0 else ""
        clerk_open = block_after(self.chat, "function clerkOpen(")

        opens_saved_thread = "clerkOpen(r.open)" in list_block
        fill_at = list_block.find('if (!placeTouched)')
        open_at = list_block.find("clerkOpen(r.open)")
        fill_skips_typed_text = fill_at >= 0 and open_at >= 0 and open_at < fill_at
        switch_clears_input = re.search(
            r"if\s*\(\s*\(id\s*\|\|\s*null\)\s*!==\s*currentThread\s*\)\s*"
            r'\$\("input"\)\.value\s*=\s*""',
            clerk_open,
        )
        typed_text_is_protected = (
            re.search(
                r'(?:var|let|const)\s+\w+\s*=\s*\$\("input"\)\.value'
                r"[\s\S]{0,500}clerkOpen\(r\.open\)",
                list_block,
            )
            or re.search(
                r"if\s*\([^)]*(?:!restoringPlace|restoringPlace\s*===\s*false"
                r"|!keepDraft|!preserveDraft)[\s\S]{0,200}\$\(\"input\"\)\.value",
                clerk_open,
            )
        )
        self.assertFalse(
            opens_saved_thread
            and fill_skips_typed_text
            and switch_clears_input
            and not typed_text_is_protected,
            "聊天列表回来前用户打的话，不能在切回旧对话时被清掉",
        )

    def test_quit_flush_does_not_clear_saved_main_draft_while_viewing_card(self):
        place_api = block_after(self.chat, "place: function")
        flush_chat_place = block_after(self.swift, "func flushChatPlace(")

        card_reports_blank_draft = re.search(
            r'draft\s*:\s*viewingCard\s*\?\s*""',
            place_api,
        )
        blank_draft_overwrites_record = re.search(
            r'record\["draft"\]\s*=\s*(?:String\()?\(place\["draft"\]',
            flush_chat_place,
        )
        saved_draft_is_protected = (
            re.search(
                r"(?:if|guard)\s+[^\n]*(?:selectedCard|viewingCard)[^\n]*"
                r"(?:==\s*nil|!=\s*nil|==\s*false|!=\s*true)[\s\S]{0,600}"
                r'record\["draft"\]',
                flush_chat_place,
            )
            or re.search(r'record\["draft"\]\s*=\s*record\["draft"\]', flush_chat_place)
        )
        self.assertFalse(
            card_reports_blank_draft
            and blank_draft_overwrites_record
            and not saved_draft_is_protected,
            "看任务卡时退出，不能用空草稿盖掉已经保存的主对话草稿",
        )


if __name__ == "__main__":
    unittest.main()
