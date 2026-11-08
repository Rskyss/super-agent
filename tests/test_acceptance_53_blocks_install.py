"""53 号单：核实名单和安装执行。

只有名单里的安装方式能由程序执行（每条有官方来源），行政只能按编号选，不能自己写命令；
先查前提工具；按参数执行，不经过 shell；关掉输入，要密码的会直接失败而不是卡住；
限时，超时整组关掉；要管理员权限就停下说明，不提权；装完再找一次；
结果写记录，不存原始输出；同一时间只装一个。
"""
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from collab import blocks, blocks_install
from collab.blocks import BlockError


def script(folder: Path, name: str, body: str):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class ListTest(unittest.TestCase):
    def test_verified_list(self):
        self.assertEqual(sorted(blocks_install.INSTALLS), ["codex-brew", "codex-npm", "node-brew"])
        for key, item in blocks_install.INSTALLS.items():
            with self.subTest(key=key):
                self.assertTrue(item["source"].startswith("https://"))
                self.assertIsInstance(item["args"], list)
                self.assertIn(item["needs"], ("npm", "brew"))
                self.assertEqual(item["args"][0], item["needs"])
                self.assertRegex(item["label"], r"[一-鿿]")

    def test_commands_are_official(self):
        self.assertEqual(blocks_install.INSTALLS["codex-npm"]["args"], ["npm", "install", "-g", "@openai/codex"])
        self.assertEqual(blocks_install.INSTALLS["codex-brew"]["args"], ["brew", "install", "--cask", "codex"])
        self.assertEqual(blocks_install.INSTALLS["node-brew"]["args"], ["brew", "install", "node"])

    def test_options(self):
        self.assertEqual(sorted(i["id"] for i in blocks_install.options("codex")), ["codex-brew", "codex-npm"])
        self.assertEqual([i["id"] for i in blocks_install.options("node")], ["node-brew"])
        self.assertEqual(blocks_install.options("cursor"), [], "Cursor 官方只给下载脚本直接执行，不进名单")
        self.assertEqual(blocks_install.options("claude"), [])

    def test_describe_for_proposal_card(self):
        info = blocks_install.describe("codex-npm")
        self.assertEqual(info["command"], "npm install -g @openai/codex")
        for field in ("id", "label", "tool", "source", "needs", "minutes"):
            self.assertIn(field, info)

    def test_unknown_id(self):
        for bad in ("nope", "", None, "codex-npm; rm -rf ~"):
            with self.subTest(bad=bad), self.assertRaises(BlockError):
                blocks_install.describe(bad)


class RunCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "积木库"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.ran = self.base / "ran.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def install(self, key="codex-npm", **over):
        args = dict(root=self.root, path=str(self.bin), timeout=5)
        args.update(over)
        return blocks_install.run_install(key, **args)

    def fake_npm(self, body):
        return script(self.bin, "npm", f'echo "$@" > "{self.ran}"\n' + body)


class RunTest(RunCase):
    def test_success_and_found(self):
        self.fake_npm(f'printf "#!/bin/sh\\necho codex-cli 9.9.9\\n" > "{self.bin}/codex"; /bin/chmod +x "{self.bin}/codex"; '
                      'echo "added 1 package"')
        result = self.install()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["found"])
        # 第四轮复查后：页面隔一会儿取最后几行，不再把每一行推给页面
        self.assertIn("added 1 package", blocks_install.output_tail())
        self.assertEqual(self.ran.read_text().split(), ["install", "-g", "@openai/codex"])

    def test_installed_but_not_found(self):
        self.fake_npm('echo "added 1 package"')
        result = self.install()
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertIn("重新打开", result["how"])

    def test_missing_prerequisite_not_run(self):
        result = self.install("codex-brew")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_prereq")
        self.assertIn("Homebrew", result["reason"])
        self.assertIn("终端", result["how"], "Homebrew 要输电脑密码，只能带着用户在终端里装")
        result = self.install("codex-npm")
        self.assertEqual(result["status"], "missing_prereq")
        self.assertIn("Node", result["how"])

    def test_already_installed_skipped(self):
        script(self.bin, "codex", "echo codex-cli 1.0")
        self.fake_npm("exit 0")
        result = self.install()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already")
        self.assertFalse(self.ran.exists())

    def test_failure_explained(self):
        self.fake_npm('echo "npm ERR! network request failed" >&2; exit 1')
        result = self.install()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "fail")
        self.assertRegex(result["reason"], r"[一-鿿]")

    def test_needs_admin_stops(self):
        self.fake_npm('echo "npm ERR! Error: EACCES: permission denied, mkdir \'/usr/local/lib/node_modules\'" >&2; exit 243')
        result = self.install()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_admin")
        self.assertIn("管理员", result["reason"])
        self.assertIn("不会自动", result["reason"])

    def test_password_prompt_does_not_hang(self):
        self.fake_npm('echo "Password:"; read pw; echo "got [$pw]"; exit 1')
        started = time.monotonic()
        result = self.install(timeout=10)
        self.assertLess(time.monotonic() - started, 5, "输入是关着的，要密码的会立刻失败，不会卡住")
        self.assertFalse(result["ok"])

    def test_timeout_stops_whole_group(self):
        marker = self.base / "child.pid"
        self.fake_npm(f'/bin/sleep 30 & echo $! > "{marker}"; exec /bin/sleep 30')
        started = time.monotonic()
        result = self.install(timeout=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result["status"], "timeout")
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(marker.read_text().strip()), 0)

    def test_record_without_raw_output(self):
        self.fake_npm('echo "token sk-abcdefghijklmnopqrstuvwxyz for someone@example.com"; exit 1')
        result = self.install()
        last = blocks.last_record("codex", root=self.root, type="install")
        self.assertIsNotNone(last)
        self.assertEqual(last["install"], "codex-npm")
        self.assertFalse(last["ok"])
        text = json.dumps([result, last], ensure_ascii=False)
        self.assertNotIn("someone@example.com", text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)
        self.assertNotIn("output", last)

    def test_output_tail_masks_secrets(self):
        self.fake_npm('echo "token sk-abcdefghijklmnopqrstuvwxyz"; exit 0')
        self.install()
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", "\n".join(blocks_install.output_tail()))

    def test_runs_absolute_path_and_prefix(self):
        # 53 号单复查后：额外环境设置能跑任意代码，去掉，只留装进指定文件夹的 prefix（见 test_blocks_install_fixes）
        self.fake_npm('echo "prefix=$npm_config_prefix" >> "' + str(self.base / "env.txt") + '"; exit 0')
        self.install(prefix=str(self.base / "prefix"))
        self.assertIn(str(self.base / "prefix"), (self.base / "env.txt").read_text())

    def test_one_install_at_a_time(self):
        self.fake_npm("/bin/sleep 2; exit 0")
        results = []
        worker = threading.Thread(target=lambda: results.append(self.install()))
        worker.start()
        for _ in range(50):
            if blocks_install.installing():
                break
            time.sleep(0.05)
        self.assertTrue(blocks_install.installing())
        with self.assertRaises(BlockError) as ctx:
            self.install("node-brew")
        self.assertIn("正在安装", str(ctx.exception))
        worker.join()
        self.assertFalse(blocks_install.installing())

    def test_relative_path_ignored(self):
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            script(self.base, "npm", f'touch "{self.ran}"')
            result = self.install(path=".::bin")
        finally:
            os.chdir(cwd)
        self.assertEqual(result["status"], "missing_prereq")
        self.assertFalse(self.ran.exists())


if __name__ == "__main__":
    unittest.main()
