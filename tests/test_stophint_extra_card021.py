"""CARD-021 补充验收：卡停下时右栏顶部写“卡住了（原因在中间卡片标题下）”。"""
import shutil
import subprocess
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


class StopHintExtra(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_page(self):
        process = subprocess.run(
            ["node", str(HERE / "stophint_extra_card021.js")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("OK", process.stdout)


if __name__ == "__main__":
    unittest.main()
