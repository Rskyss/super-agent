"""57 号单：聊天里积木库的三种提议卡——安装（实时输出）、接入 CLI、接入大模型 API（密钥框），检测通过后“放进基础层”。

规则层（chat.js 里 ChatCore 新加的几个函数）用 node 检查；页面层的密钥规矩用文字检查；
本机服务加两样：按提议找后台任务（页面重新打开后还能接上），任务回话里带“放进去了没有”。
项目窗口的提议卡不变，由原有的聊天页测试把关。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from collab import blocks
from test_acceptance_55_blocks_server import KEY, BlocksCase, write_script

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


class RulesTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则层检查")
    def test_rules(self):
        proc = subprocess.run(["node", str(HERE / "chat_blocks_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_old_chat_checks_still_pass(self):
        for name in ("chat_page_check.js", "chat_card_check.js"):
            proc = subprocess.run(["node", str(HERE / name)], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{name}：{proc.stderr or proc.stdout}")


class PageTest(unittest.TestCase):
    def test_key_box(self):
        script = read("chat.js")
        self.assertIn("password", script, "密钥框要用密码框，填的字不显示出来")
        self.assertIn("autocomplete", script, "密钥框要关掉浏览器自动填写")
        self.assertIn("api_key", script, "确认接入大模型 API 时把密钥框里的密钥带上")
        self.assertIn("请先在密钥框里填好密钥", script, "没填密钥就点检测，页面先拦住，不发请求")

    def test_key_not_kept(self):
        script = read("chat.js")
        self.assertNotRegex(script, r"localStorage|sessionStorage|indexedDB|document\.cookie", "密钥不能存在网页里")
        self.assertNotIn("console.log", script, "不打印")

    def test_uses_job_and_save(self):
        script = read("chat.js")
        for part in ("/api/blocks/job", "/api/blocks/save", "proposal", "blocksKey", "放进基础层"):
            self.assertIn(part, script)

    def test_output_styles_use_theme(self):
        css = read("chat.css")
        self.assertIn(".prop-output", css, "安装输出区要有自己的样式，像终端一样等宽、能滚动")
        block = css[css.index(".prop-output"):]
        block = block[:block.index("}")]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b", "颜色只用 theme.css 里的变量")


class JobByProposalTest(BlocksCase):
    def setUp(self):
        super().setUp()
        (self.bin / "codex").unlink()
        import os
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        write_script(self.bin / "npm", 'echo "added 1 package"\n')

    def test_find_job_by_proposal(self):
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        status, data = self.confirm(cid, pid)
        self.assertEqual(status, 200, data)
        self.job(data["job"])
        status, found = self.bget("/api/blocks/job", proposal=pid)
        self.assertEqual(status, 200, found)
        self.assertEqual(found["kind"], "install")
        self.assertTrue(found["done"])
        self.assertTrue(found["result"]["ok"], found)
        self.assertIn("added 1 package", found["tail"], "重新打开页面后还能看到安装的最后几行")
        self.assertEqual(found["job"], data["job"], "回话里带上任务编号，页面接着用它")

    def test_unknown_proposal(self):
        status, data = self.bget("/api/blocks/job", proposal="nothing")
        self.assertEqual(status, 404, data)
        self.assertTrue(data["error"])

    def test_open_proposal_has_no_job(self):
        _, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        status, _ = self.bget("/api/blocks/job", proposal=pid)
        self.assertEqual(status, 404, "还没确认的提议没有后台任务")


class SavedFlagTest(BlocksCase):
    def test_saved_flag(self):
        self.tool()
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        done = self.job(data["job"])
        self.assertTrue(done["result"]["ok"], done)
        self.assertIs(done["saved"], False, "检测通过、还没放进去")
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 200)
        _, after = self.bget("/api/blocks/job", id=data["job"])
        self.assertIs(after["saved"], True, "放进去以后，重新打开页面不再出“放进基础层”按钮")
        _, by_pid = self.bget("/api/blocks/job", proposal=pid)
        self.assertIs(by_pid["saved"], True)
        self.assertEqual(blocks.load_card("mytool")["name"], "我的工具")

    def test_api_flow_key_not_returned(self):
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus", "name": "通义"})
        status, data = self.confirm(cid, pid, api_key=KEY)
        self.assertEqual(status, 200, data)
        self.job(data["job"])
        _, found = self.bget("/api/blocks/job", proposal=pid)
        self.assertTrue(found["result"]["ok"], found)
        self.assertIs(found["saved"], False)
        # 密钥不出现在任何返回里：BlocksCase 收尾时统一查


if __name__ == "__main__":
    unittest.main()
