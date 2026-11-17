"""开发助手精简（2026-11-17 用户：“积木库就不用了，设置按钮叫团队，围绕开发做得轻量化”）。
- 看不到积木库：左栏底部没有“积木库”按钮，菜单“窗口”里没有“积木库”；积木库管家的积木/能力卡提议只在积木库窗口里出现，入口没了也就不会冒出来。
- 左栏底部齿轮改成文字按钮“团队”，点开还是团队页。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "desktop" / "static"
MAIN = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


def foot():
    html = (STATIC / "nav.html").read_text(encoding="utf-8")
    return html[html.index('<footer class="foot">'):html.index("</footer>")]


class NoBlocksEntry(unittest.TestCase):
    def test_nav_footer(self):
        self.assertNotIn("blocksBtn", foot())
        self.assertNotIn("积木库", foot())
        self.assertNotIn("blocksBtn", (STATIC / "nav.js").read_text(encoding="utf-8"))

    def test_window_menu(self):
        main = MAIN.read_text(encoding="utf-8")
        line = next(l for l in main.splitlines() if 'submenu("窗口"' in l)
        self.assertNotIn('"积木库"', line)
        self.assertIn('"项目窗口"', line)


class TeamButton(unittest.TestCase):
    def test_text_button(self):
        f = foot()
        m = re.search(r'<button[^>]*id="gear"[^>]*>(.*?)</button>', f, re.S)
        self.assertIsNotNone(m, "团队按钮沿用 id=gear（点开团队页的逻辑不变）")
        self.assertEqual(m.group(1).strip(), "团队")
        self.assertNotIn("<svg", m.group(0))

    def test_still_opens_team(self):
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertRegex(nav, r'\$\("gear"\)\.addEventListener\("click", function \(\) \{ tell\("superagent://settings"\); \}\)')


if __name__ == "__main__":
    unittest.main()
