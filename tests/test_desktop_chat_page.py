"""聊天页面：记账层用 node 检查（没装 node 的电脑跳过）；页面文件能从本机服务打开。"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class ChatPageTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面记账层检查")
    def test_chat_bookkeeping_and_safety(self):
        proc = subprocess.run(["node", str(HERE / "chat_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    def test_chat_page_files_exist_and_load_the_script(self):
        static = HERE.parent / "desktop" / "static"
        html = (static / "chat.html").read_text(encoding="utf-8")
        self.assertIn("/static/chat.js", html)
        self.assertTrue((static / "chat.css").exists())


if __name__ == "__main__":
    unittest.main()
