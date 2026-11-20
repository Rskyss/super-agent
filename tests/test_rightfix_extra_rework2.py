"""右栏显示小修复查退回：起草进度不能新旧同时显示。"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ChatDraftProgressTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_latest_draft_progress_replaces_the_old_one(self):
        environment = dict(os.environ, TZ="Asia/Shanghai")
        process = subprocess.run(
            ["node", str(ROOT / "tests" / "rightfix_extra_rework2_page.js")],
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("OK", process.stdout)


if __name__ == "__main__":
    unittest.main()
