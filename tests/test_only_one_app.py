"""重装后本机只剩一个开发助手（用户 2026-11-25：搜索里出现三个，打开的是旧的）。"""
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

MAC = Path(__file__).resolve().parents[1] / "desktop" / "mac"
BUILD = MAC / "build.sh"


class OnlyOneApp(unittest.TestCase):
    """build.sh --install 装完，编译出来的那份要删掉，不然搜索里又多一个。实跑安装那一段。"""

    def make_app(self, path, mark):
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "mark.txt").write_text(mark)
        with open(path / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": "local.superagent.desktop"}, f)

    def run_tail(self, install):
        text = BUILD.read_text(encoding="utf-8")
        tail = text[text.index('if [ "${1:-}" = "--install" ]'):]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            here = d / "mac"
            here.mkdir()
            shutil.copy(MAC / "remove_app.sh", here / "remove_app.sh")
            self.make_app(here / "build" / "开发助手.app", "new")
            self.make_app(d / "Applications" / "开发助手.app", "old")
            script = d / "t.sh"
            script.write_text('set -euo pipefail\nHERE="%s"\nAPP="$HERE/build/开发助手.app"\n' % here + tail, encoding="utf-8")
            out = subprocess.run(["bash", str(script)] + (["--install"] if install else []), capture_output=True, text=True,
                                 timeout=30, env={**os.environ, "HOME": str(d)})
            self.assertEqual(out.returncode, 0, out.stderr)
            return ((here / "build" / "开发助手.app").exists(),
                    (d / "Applications" / "开发助手.app" / "Contents" / "mark.txt").read_text(), out.stdout)

    def test_install_leaves_only_the_installed_one(self):
        left, mark, out = self.run_tail(True)
        self.assertFalse(left, "装完编译出来的那份还在")
        self.assertEqual(mark, "new")
        self.assertIn("Applications/开发助手.app", out)

    def test_plain_build_keeps_its_output(self):
        left, mark, _ = self.run_tail(False)
        self.assertTrue(left)
        self.assertEqual(mark, "old")


if __name__ == "__main__":
    unittest.main()
