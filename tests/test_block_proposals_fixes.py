"""57 号单验收和复查后的修改：积木库提议卡。

同一个任务只跟进一套：确认请求还没回话时，不按提议去查（服务器这时还没登记，会误报“超级助手重开过”）；
网络断一下不放弃，过一会儿再问；“未核实，有风险”要醒目；装好了但没认到时要说怎么办；
接 API 时页面刷新过、密钥没带上，放进基础层后要说一声；放进基础层失败的原因不能被下一次刷新冲掉；
密钥框旁的旧提示，点了检测就收起；安装输出往上翻时，不被拉回最底下。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"
SCRIPT = STATIC / "chat.js"


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class RulesTest(unittest.TestCase):
    def node(self, code):
        prog = "var C = require(%r);\n%s" % (str(SCRIPT), code)
        proc = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def view(self, job):
        import json
        return json.loads(self.node("process.stdout.write(JSON.stringify(C.jobView(%s)))" % json.dumps(job)))

    def test_installed_but_not_found_says_how(self):
        v = self.view({"kind": "install", "done": True, "error": "", "tail": [],
                       "result": {"ok": True, "reason": "codex 装好了", "how": "装好了，但超级助手还没认到：关掉超级助手再重新打开，然后检测一次"}})
        self.assertEqual(v["state"], "ok")
        self.assertIn("关掉超级助手再重新打开", v["text"], "没认到时要告诉用户先重开")
        self.assertEqual(v["text"].count("装好了"), 1, "不要说两遍“装好了”")

    def test_installed_normal_words_unchanged(self):
        v = self.view({"kind": "install", "done": True, "error": "", "tail": [],
                       "result": {"ok": True, "reason": "codex 装好了", "how": ""}})
        self.assertEqual(v["text"], "codex 装好了。跟行政说一声，它会接着帮你接进基础层")

    def test_fail_without_reason_no_leading_mark(self):
        v = self.view({"kind": "check", "done": True, "error": "", "result": {"ok": False, "reason": "", "how": "检查密钥"}})
        self.assertEqual(v["text"], "检查密钥")

    def test_connect_title_says_what_it_does(self):
        out = self.node('var b = {proposalKind: "connect", info: {name: "Codex", verified: true}};'
                        'var a = {proposalKind: "connect_api", info: {name: "通义", provider: "bailian", model: "q", block: {base_url: "https://x"}}};'
                        'process.stdout.write(C.blockCard(b).title + "|" + C.blockCard(a).title)')
        cli, api = out.split("|")
        self.assertIn("Codex", cli)
        self.assertIn("接进基础层", cli, "只写一个名字，看不出这张卡要干什么")
        self.assertIn("通义", api)
        self.assertIn("接进基础层", api)


class PageTextTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")
        self.css = (STATIC / "chat.css").read_text(encoding="utf-8")

    def func(self, name):
        m = re.search(r"function %s\(.*?\n  \}\n" % name, self.script, re.S)
        self.assertIsNotNone(m, name)
        return m.group(0)

    def test_warn_is_visible(self):
        self.assertRegex(self.css, r"\.ask \.prop-warn\s*\{[^}]*color:\s*var\(--bad\)",
                         "风险提示要比 .ask p 更具体，用出错的颜色，不然被盖成灰字")

    def test_one_follow_per_job(self):
        poll = self.func("startJobPoll")
        self.assertRegex(poll, r"if \(rec\.polling\) return;", "同一个任务只跑一套：开始请求前就占住")
        ensure = self.func("ensureProposalJob")
        self.assertIn("confirming[pid]", ensure, "确认请求还没回话时不按提议去查")
        self.assertIn("rec.polling", ensure, "已经在跟进了就不再起一套")
        self.assertEqual(self.script.count('if (blockKind && decision === "confirm") touchProposal(pid);'), 2,
                         "确认回话没带任务、或请求出错时都要按提议查一次（服务器可能已经确认了）；点“先不做”不重画")

    def test_network_error_retries(self):
        poll = self.func("startJobPoll")
        catch = poll[poll.index(".catch"):]
        self.assertIn("setTimeout(tick", catch, "网络断一下要过一会儿再问，不能一直停在“正在安装…”")
        ensure = self.func("ensureProposalJob")
        catch = ensure[ensure.index(".catch"):]
        self.assertNotIn("jobView(null)", catch, "连不上不能说成“超级助手重开过”")

    def test_missing_key_is_told(self):
        self.assertIn("密钥没带上", self.script, "刷新过、密钥丢了，放进基础层后要说一声")

    def test_save_error_survives_redraw(self):
        self.assertGreaterEqual(self.script.count("saveError"), 3, "放进基础层失败的原因要记下来，重画时还在")

    def test_key_hint_cleared(self):
        self.assertIn("hint.hidden = true", self.script,"点检测时收起上次“请先填好密钥”的提示")

    def test_output_keeps_scroll(self):
        self.assertIn("nearEnd", self.script, "用户往上翻安装输出时，不要拉回最底下")


if __name__ == "__main__":
    unittest.main()
