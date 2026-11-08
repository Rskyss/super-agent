"""51 号单第二轮复查查出的体检问题：相对路径里的冒名程序、警告里的邮箱、后台小程序占着输出。"""
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from collab import blocks_env


def script(folder: Path, name: str, body: str):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class EnvFixes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "积木库"
        self.app = self.base / "app_bin"
        self.app.mkdir()
        self._cwd = os.getcwd()
        os.chdir(self.base)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def scan(self, **over):
        args = dict(root=self.root, app_path=str(self.app), terminal_path=None, timeout=1.0, record=True)
        args.update(over)
        return {i["name"]: i for i in blocks_env.scan(**args)["items"]}

    def test_relative_and_empty_path_entries_ignored(self):
        script(self.base, "npm", 'echo "fake 9.9.9"')
        script(self.base / "cwdbin", "brew", 'echo "fake brew 1.0"')
        items = self.scan(app_path="::cwdbin:" + str(self.app))
        self.assertEqual(items["npm"]["status"], "没有", "当前文件夹里的冒名程序不能算")
        self.assertEqual(items["brew"]["status"], "没有", "相对路径里的不能算")
        items = self.scan(app_path="::cwdbin", terminal_path=".:cwdbin")
        self.assertEqual(items["brew"]["status"], "没有")

    def test_found_paths_are_absolute(self):
        script(self.app, "node", 'echo v22.1.0')
        node = self.scan()["node"]
        self.assertTrue(os.path.isabs(node["path"]))

    def test_warning_with_email_not_kept(self):
        script(self.app, "git", 'echo "warning: signed in as someone@example.com" >&2; echo "git version 2.54.0"')
        git = self.scan()["git"]
        self.assertEqual(git["version"], "git version 2.54.0")
        record = (self.root / "记录" / "env.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("someone@example.com", record)

    def test_email_in_version_line_masked(self):
        script(self.app, "node", 'echo "v22.1.0 (someone@example.com)"')
        node = self.scan()["node"]
        self.assertNotIn("someone@example.com", json.dumps(node, ensure_ascii=False))
        self.assertIn("v22.1.0", node["version"])

    def test_background_child_does_not_cause_timeout_or_linger(self):
        marker = self.base / "child.pid"
        script(self.app, "node", f'/bin/sleep 30 & echo $! > "{marker}"; echo v1.2.3')
        started = time.monotonic()
        node = self.scan()["node"]
        self.assertLess(time.monotonic() - started, 1.0 + 0.8, "子程序退出了就该结束，不能等满限时")
        self.assertEqual(node["version"], "v1.2.3")
        self.assertEqual(node["note"], "")
        pid = int(marker.read_text().strip())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError, msg="后台留下的小程序要一起关掉"):
            os.kill(pid, 0)

    def test_timeout_kills_whole_group(self):
        marker = self.base / "child.pid"
        script(self.app, "slow", f'/bin/sleep 30 & echo $! > "{marker}"; exec /bin/sleep 30')
        with self.assertRaises(subprocess.TimeoutExpired):
            blocks_env.run_command([str(self.app / "slow")], str(self.app), 0.5)
        pid = int(marker.read_text().strip())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
