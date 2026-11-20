"""左下角一键切换项目（验收测试，施工方不许改）。

依据 docs/切换项目/施工单-切换项目.md。规则层用 node 跑 projswitch_page_check.js；
窗口外壳（Swift）和页面接线用文字检查——真正点了切不切得过去，由 Claude 重装后在真实窗口里走查。
"""
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
    """从 start 所在处起，取第一对花括号里的内容（含嵌套）。找不到返回空串。"""
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return ""


def case_body(text, name):
    """取 superagent:// 分支 case "name": 到下一个 case/default 之间的内容。"""
    m = re.search(r'case "%s":(.*?)(?=\n\s*case "|\n\s*default:)' % re.escape(name), text, re.S)
    return m.group(1) if m else ""


class RuleLayerTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过规则层检查")
    def test_project_list_search_and_restore_rules(self):
        proc = subprocess.run(["node", str(HERE / "projswitch_page_check.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


class NavPageTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "nav.html").read_text(encoding="utf-8")
        self.js = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.css = (STATIC / "nav.css").read_text(encoding="utf-8")

    def test_bottom_left_is_a_button_with_the_project_name(self):
        foot = self.html[self.html.find('<footer'):self.html.find('</footer>')]
        self.assertIn('id="projBtn"', foot, "左下角项目名要做成按钮")
        self.assertRegex(foot, r'<button[^>]*id="projBtn"')
        self.assertIn('id="proj"', foot, "项目名那块的 id 不变（后台每次刷新都往里写名字）")
        self.assertIn('id="gear"', foot, "“团队”按钮还在")

    def test_popup_has_search_list_and_add(self):
        for part in ('id="projMenu"', 'id="projQ"', 'id="projList"', 'id="projAdd"'):
            self.assertIn(part, self.html)
        self.assertIn("搜索项目", self.html)
        self.assertIn("添加项目", self.html)
        self.assertNotIn("新建项目", self.html + self.js, "用户 11-19 定：叫“添加项目”")
        self.assertIn("没有匹配的项目", self.html + self.js)
        self.assertRegex(self.html, r'id="projMenu"[^>]*hidden', "浮层平时藏着")

    def test_popup_talks_to_the_window(self):
        self.assertIn("superagent://project?path=", self.js)
        self.assertIn("superagent://project-add", self.js)
        self.assertIn("encodeURIComponent", self.js)
        self.assertIn('params.get("projects")', self.js, "项目列表从页面地址里拿")
        self.assertIn("NavCore.projectItems", self.js, "画列表要走规则层")
        self.assertIn("Escape", self.js, "按 Esc 关浮层")

    def test_popup_styles_exist(self):
        for sel in (".proj-menu", ".proj-item"):
            self.assertIn(sel, self.css)


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.s = swift_text()

    def test_recent_list_keeps_twenty(self):
        self.assertRegex(self.s, r"let recentLimit\s*=\s*20\b")
        remember = block_after(self.s, "func remember(")
        self.assertIn("recentLimit", remember)
        self.assertNotIn("prefix(10)", remember, "最多记 20 个（用户 11-19）")
        rebuild = block_after(self.s, "func rebuildRecentMenu(")
        self.assertIn("recentProjectList()", rebuild, "顶部菜单和左下角用同一份列表")

    def test_recent_list_helper_filters_missing_folders(self):
        body = block_after(self.s, "func recentProjectList(")
        self.assertTrue(body, "要有 func recentProjectList() -> [String]")
        self.assertIn("recentProjectsKey", body)
        self.assertIn("isFolder", body)

    def test_switch_only_to_known_existing_folders(self):
        guard = block_after(self.s, "func canSwitchTo(")
        self.assertTrue(guard, "要有 func canSwitchTo(_ path: String) -> Bool")
        self.assertIn("recentProjectList()", guard, "只认最近列表里的")
        self.assertIn("isFolder", guard, "文件夹得还在")
        body = case_body(self.s, "project")
        self.assertTrue(body, '外壳要接 superagent://project')
        self.assertIn("canSwitchTo(", body)
        self.assertIn("switchTo(", body, "切换要走现有的 switchTo，不另造")
        self.assertIn("project.path", body, "点当前项目不重新切")

    def test_add_reuses_existing_folder_picker(self):
        body = case_body(self.s, "project-add")
        self.assertTrue(body, '外壳要接 superagent://project-add')
        self.assertIn("askForProject()", body)
        self.assertIn("switchTo(", body)

    def test_nav_gets_the_list(self):
        load = block_after(self.s, "func loadPanel(")
        self.assertIn("&projects=", load)
        self.assertIn("recentProjectList()", load)

    def test_place_is_saved_per_project(self):
        self.assertIn("func placeKey(", self.s)
        key = block_after(self.s, "func placeKey(")
        self.assertIn("path", key, "每个项目一份：键里带项目路径")
        body = case_body(self.s, "place")
        self.assertTrue(body, '外壳要接 superagent://place（聊天页报当前对话和草稿）')
        self.assertIn("prefix(2000)", body, "草稿有长度上限")
        self.assertIn("placeKey(", body)

    def test_switch_saves_place_before_leaving(self):
        body = block_after(self.s, "func switchTo(")
        self.assertIn("SuperAgentChat.place", body, "切走前问聊天页要当前对话和草稿")
        self.assertIn("selectedCard", body)
        i_ask = body.find("SuperAgentChat.place")
        i_close = body.find("closeChatOnServer")
        # 问聊天页是等回话的：后半段可以挪进回话里（另起一个函数），但不许先关对话再问
        self.assertTrue(i_ask >= 0 and (i_close < 0 or i_ask < i_close), "先存下停的地方，再关旧项目的对话")

    def test_restore_passes_place_to_chat_and_card(self):
        load = block_after(self.s, "func loadChat(")
        self.assertIn("&thread=", load)
        self.assertIn("&draft=", load)
        self.assertIn("pendingPlaceCard", self.s, "记录里的卡等聊天页加载完再打开")


class ChatPageTest(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "chat.js").read_text(encoding="utf-8")

    def test_chat_reports_and_restores_place(self):
        api = self.js[self.js.find("window.SuperAgentChat = {"):]
        self.assertIn("place:", api[:4000], "SuperAgentChat 要有 place()")
        self.assertIn("superagent://place", self.js, "打字停下后告诉外壳，退出应用也记得")
        self.assertIn('params.get("thread")', self.js)
        self.assertIn('params.get("draft")', self.js)
        self.assertIn("ChatCore.restorePlace", self.js, "恢复走规则层")


if __name__ == "__main__":
    unittest.main()
