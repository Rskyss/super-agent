"""第三批 C4：右栏一块接一块往下排，只让流程/交接列表伸缩（11-19 截图复现：
原来按“三行”排，等你批准方案时有四块，“流程”那行被挤进伸缩行，窗口高时下面空一大块、矮时和“提需求”叠字）。"""
import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")


def rule(selector):
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.M)
    return m.group(1) if m else ""


class StackLayout(unittest.TestCase):
    def test_side_is_a_column_not_three_fixed_rows(self):
        side = rule(".side")
        self.assertIn("flex-direction: column", side)
        self.assertNotIn("grid-template-rows", side)

    def test_only_the_pane_stretches(self):
        self.assertIn("flex: none", rule(".side > *"))
        pane = rule(".side > .pane")
        self.assertIn("flex: 1 1 auto", pane)
        self.assertIn("min-height: 0", pane)


if __name__ == "__main__":
    unittest.main()
