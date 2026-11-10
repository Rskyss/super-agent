"""第一阶段收尾（2026-11-09 用户看页面后改）：环境体检并进“设置”。

按钮叫“设置”（不再叫“行政设置”）；点开是一页两块：上面行政、下面环境体检（默认展开）；
体检条不再固定在聊天上方；体检发现问题时“设置”按钮带小红点。绑定页（第一次绑定、重新连上）里没有体检。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


class SettingsPage(unittest.TestCase):
    def test_button_named_settings(self):
        html = read("blocks.html")
        self.assertRegex(html, r'id="settingsBtn"[^>]*>设置</button>')
        self.assertNotIn("行政设置", html)
        self.assertNotIn("行政设置", read("blocks.js"))

    def test_health_card_lives_in_settings_not_above_chat(self):
        html = read("blocks.html")
        stage = html[html.index('class="stage"'):]
        self.assertNotIn('id="healthCard"', stage[:stage.index('id="chatFrame"')], "体检条不再固定在聊天上方")
        bind = html[html.index('id="bindCard"'):html.index('id="mainLayout"')]
        self.assertIn('id="healthCard"', bind, "体检在设置页里")
        self.assertIn('id="settingsHealth"', bind)
        self.assertLess(bind.index('id="bindKey"'), bind.index('id="settingsHealth"'), "上面行政、下面环境体检")
        self.assertIn("环境体检", bind)
        self.assertIn('id="bindSub"', bind)

    def test_wiring(self):
        js = read("blocks.js")
        for words in ("settingsOpen", '$("settingsHealth").hidden = !settingsOpen', "manualExpand = true",
                      'BlocksCore.settingsAlert', 'classList.toggle("alert"'):
            self.assertIn(words, js)
        self.assertNotIn('$("healthCard").hidden', js, "体检卡的显隐只由设置页决定")
        self.assertIn('$("bindTitle").textContent = settingsOpen ? "设置"', js)

    def test_css(self):
        css = read("blocks.css")
        self.assertIn("#settingsBtn.alert", css)
        self.assertIn(".bind-card.wide", css)


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class AlertRule(unittest.TestCase):
    def test_rule(self):
        script = r"""
var assert = require("assert");
var B = require(process.argv[1] + "/desktop/static/blocks.js");
assert.strictEqual(B.settingsAlert({ state: "problem" }, ""), true);
assert.strictEqual(B.settingsAlert({ state: "ok" }, ""), false);
assert.strictEqual(B.settingsAlert({ state: "loading" }, ""), false, "还在查的时候不亮");
assert.strictEqual(B.settingsAlert({ state: "ok" }, "体检没查成"), true, "体检本身出错也亮");
assert.strictEqual(B.settingsAlert(null, ""), false);
"""
        proc = subprocess.run(["node", "-e", script, str(HERE.parent)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
