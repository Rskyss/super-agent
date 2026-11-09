"""58 号单验收后的修补（Claude 在浏览器里走查发现的）。

1. 没核实时亮出的完整命令不折行，长了就跑出框外，用户看不全就点了“确认检测”。
2. “未核实，有风险”是白字，施工单要醒目颜色。
3. 详情的行：标签和内容各占一半，内容被挤到右边，命令那行靠右对齐。
4. 体检卡清单整行红或整行绿，打叉的说明没有另起一行；说明要用灰字。
5. 详情取不到时，断网也说成“基础层里没有这一块了”；只有 404 才这么说。
6. 体检查过、登录那项是空的，要写“查不了”。
7. （复查）详情还在读取时仍显示上一块，点检测测的是上一块，新详情卡在“正在检测…”。点开就要清掉旧内容。
8. （复查）离开又回到同一块时，路上那次查询回来会接着跟进旧任务；换块就作废旧的跟进。
9. （复查）“正在检测…”“结果没留下来”的字要灰。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def rule(css, selector):
    """取某个选择器（单独成条的）的样式内容。"""
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


class DetailLookTest(unittest.TestCase):
    def setUp(self):
        self.css = read("blocks.css")

    def test_preview_and_card_wrap(self):
        for sel in (".detail-preview", ".detail-card pre"):
            body = rule(self.css, sel)
            self.assertIn("pre-wrap", body, sel + " 要折行")
            self.assertRegex(body, r"(word-break|overflow-wrap)\s*:\s*(break-all|anywhere)", sel + " 长路径也要能断开")

    def test_warn_is_bad_color(self):
        self.assertIn("var(--bad)", rule(self.css, ".detail-warn"))

    def test_label_fixed_width_value_takes_rest(self):
        self.assertNotRegex(self.css, r"\.detail-row span\s*,", "标签也是 span，不能和内容一起 flex: 1")
        self.assertRegex(rule(self.css, ".detail-label"), r"flex\s*:\s*0 0")
        self.assertIn("flex: 1", rule(self.css, ".detail-value"))
        self.assertIn("margin: 0", rule(self.css, ".detail-value"))

    def test_idle_status_dim(self):
        self.assertIn("var(--dim)", rule(self.css, ".detail-status.idle"))


class HealthRowsTest(unittest.TestCase):
    def test_marks_colored_notes_dim(self):
        css = read("blocks.css")
        self.assertNotRegex(css, r"\.health-row\.bad\s*\{[^}]*--bad", "不要整行红")
        self.assertIn("var(--bad)", rule(css, ".health-mark.bad"))
        self.assertIn("var(--ok)", rule(css, ".health-mark.ok"))
        self.assertIn("var(--dim)", rule(css, ".health-note"))
        js = read("blocks.js")
        self.assertIn('"health-note"', js)
        self.assertNotIn('createElement("br")', js, "说明另起一行靠块元素，flex 里的 br 不换行")


class DetailErrorTest(unittest.TestCase):
    def test_only_404_means_gone(self):
        js = read("blocks.js")
        m = re.search(r'基础层里没有这一块了', js)
        self.assertTrue(m)
        around = js[max(0, m.start() - 300):m.end() + 100]
        self.assertIn("404", around, "只有 404 才说没有这一块，断网要写原因")


class DetailSwitchTest(unittest.TestCase):
    def setUp(self):
        js = read("blocks.js")
        start = js.index("function openDetail(")
        self.open_detail = js[start:js.index('getJson("/api/blocks/detail"', start)]
        start = js.index("function followCheck(")
        self.follow = js[start:js.index("function", start + 10)]
        self.js = js

    def test_open_clears_old_detail_first(self):
        self.assertIn("currentDetail = null", self.open_detail)
        self.assertIn("正在读取…", self.open_detail)

    def test_stale_follow_dropped(self):
        self.assertIn("checkGen", self.follow)
        start = self.js.index("function clearCheckState(")
        self.assertIn("checkGen++", self.js[start:self.js.index("}", start)])

    def test_running_and_lost_dim(self):
        css = read("blocks.css")
        self.assertIn("var(--dim)", rule(css, ".detail-result.running"))
        self.assertIn("var(--dim)", rule(css, ".detail-result.lost"))


class LoginTextTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_empty_login_is_cannot_check(self):
        code = (
            'var B = require(%r);'
            'var d = {card: {id: "t", kind: "cli", name: "T", command: "t", run: ["{prompt}"]}, verified: true,'
            ' preview: "", key_account: "", last_check: null};'
            'var v = B.detailView(d, {name: "t", version: "1", login: ""});'
            'if (v.rows[3].text !== "查不了") { console.error(v.rows[3].text); process.exit(1); }'
        ) % str(STATIC / "blocks.js")
        proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
