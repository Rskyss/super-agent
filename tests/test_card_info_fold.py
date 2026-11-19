"""中间区卡片原文默认收起（用户 11-18：“上面这个框我没法缩，下面的内容我又看不到”，选“默认收起，点开看全文”）。

默认只显示 2 行，后面有“展开”；展开后最多占中间区约一半高，超出的在框里滚动；再点“收起”回到 2 行；切卡时回到收起。
下面的对话和行政的确认框始终有地方显示。界面效果靠真实窗口走查，这里守住写法。
"""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "desktop" / "static"


class Fold(unittest.TestCase):
    def setUp(self):
        self.css = (STATIC / "chat.css").read_text(encoding="utf-8")
        self.js = (STATIC / "chat.js").read_text(encoding="utf-8")

    def rule(self, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, f"缺少样式 {selector}")
        return m.group(1)

    def test_collapsed_by_default_two_lines(self):
        body = self.rule("#cardInfo.fold")
        self.assertIn("overflow: hidden", body)
        self.assertIn("max-height: calc(2 * 1.6em)", body, "收起时只显示 2 行")
        self.assertIn("line-height: 1.6", self.rule("#cardInfo p"), "2 行的高度按这个行高算")

    def test_open_is_capped_and_scrolls(self):
        body = self.rule("#cardInfo.open")
        self.assertIn("max-height: 45vh", body, "展开后最多占约一半高")
        self.assertIn("overflow-y: auto", body)

    def test_toggle_words_and_reset_on_open_card(self):
        self.assertIn("展开", self.js)
        self.assertIn("收起", self.js)
        open_card = self.js[self.js.index("openCard: function"):self.js.index("openCard: function") + 2500]
        self.assertIn("cardFold(box, true)", open_card, "切卡时回到收起")
        fold = self.js[self.js.index("function cardFold("):self.js.index("var nowCard")]
        self.assertIn('addEventListener("click"', fold, "点按钮能切换")
        self.assertIn("ResizeObserver", fold, "拖宽拖窄后重新判断要不要“展开”")
        self.assertIn("box.scrollTop = 0", fold, "收起时回到开头")
        self.assertIn("box.scrollHeight <= box.clientHeight", fold, "放得下就不出按钮")
        hide = self.js[self.js.index("function hideCard("):self.js.index("function hideCard(") + 800]
        self.assertIn("foldBtn.hidden = true", hide, "关掉卡片时按钮也藏起来")

    def test_keyboard_focus_visible(self):
        self.assertIn("outline", self.rule("#cardFold:focus-visible"))

    def test_messages_keep_room(self):
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto auto", self.css, "对话区始终占剩下的高度")


if __name__ == "__main__":
    unittest.main()
