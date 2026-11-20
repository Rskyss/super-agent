"""右栏停下提示（docs/右栏停下提示/施工单-右栏停下提示.md）：卡停下时右栏顶部写“卡住了（原因在中间卡片标题下）”。
来由：右栏显示小修的施工单写的就是这句，CARD-020 只做成了“卡住了”，用户看不出原因去哪了。真的执行右栏脚本检查。"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class StopHint(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_page(self):
        proc = subprocess.run(["node", str(HERE / "stophint_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
