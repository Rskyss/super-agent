"""51 号单：环境体检。

只看不跑任务：查 Node、npm、Homebrew、git 和各 CLI 的版本、登录；
超级助手自己的找命令路径和终端的路径各查一遍，标出“装了但超级助手找不到”；
每项限时，并行跑；不保存工具的原始输出（里面可能有邮箱）。
"""
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from collab import blocks, blocks_env


def script(folder: Path, name: str, body: str):
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "积木库"
        self.app = base / "app_bin"
        self.term = base / "term_bin"
        self.app.mkdir()
        self.term.mkdir()
        script(self.app, "node", 'echo "v22.1.0"')
        script(self.app, "brew", 'echo "Homebrew 4.6.0"; echo "其他行"')
        script(self.app, "git", 'exec /bin/sleep 10')
        script(self.app, "codex", '[ "$1" = "login" ] && { echo "Logged in using ChatGPT" >&2; exit 0; }\n'
                                 'echo "codex-cli 0.167.0"')
        script(self.app, "agent", '[ "$1" = "status" ] && { echo "Not logged in (someone@example.com)"; exit 1; }\n'
                                  'echo "2026.11.05"')
        script(self.term, "agy", 'echo "1.5.3"')
        script(self.app, "demo-cli", 'echo "demo 1.0"')

    def tearDown(self):
        self._tmp.cleanup()

    def run_scan(self, **over):
        args = dict(root=self.root, app_path=str(self.app), terminal_path=str(self.term), timeout=2.0)  # 全量测试时电脑忙，0.5 秒会偶发查不到版本
        args.update(over)
        return blocks_env.scan(**args)

    @staticmethod
    def by_name(result):
        return {item["name"]: item for item in result["items"]}


class ScanTest(EnvCase):
    def test_prereqs(self):
        items = self.by_name(self.run_scan())
        self.assertEqual(items["node"]["status"], "有")
        self.assertEqual(items["node"]["version"], "v22.1.0")
        self.assertEqual(items["node"]["kind"], "prereq")
        self.assertIsNone(items["node"]["login"])
        self.assertTrue(items["node"]["path"].endswith("/node"))
        self.assertEqual(items["brew"]["version"], "Homebrew 4.6.0", "只取第一行")
        self.assertEqual(items["npm"]["status"], "没有")
        self.assertIn("行政", items["npm"]["fix"])
        for name in ("node", "npm", "brew", "git"):
            self.assertRegex(items[name]["why"], r"[一-鿿]", "每个前提工具都要写清做什么用")

    def test_builtin_tools_and_login(self):
        items = self.by_name(self.run_scan())
        self.assertEqual(items["codex"]["kind"], "tool")
        self.assertEqual(items["codex"]["version"], "codex-cli 0.167.0")
        self.assertEqual(items["codex"]["login"], "已登录", "登录结果可能写在错误输出里")
        self.assertEqual(items["cursor"]["status"], "有")
        self.assertEqual(items["cursor"]["login"], "没登录")
        self.assertIn("登录", items["cursor"]["fix"])

    def test_terminal_only_is_flagged(self):
        agy = self.by_name(self.run_scan())["agy"]
        self.assertEqual(agy["status"], "找不到")
        self.assertEqual(agy["version"], "1.5.3", "终端里找到的也要查版本")
        self.assertEqual(agy["login"], "查不了", "agy 没有查登录的命令")
        self.assertIn("重新打开", agy["fix"])

    def test_without_terminal_path(self):
        result = self.run_scan(terminal_path=None)
        self.assertFalse(result["terminal_path_read"])
        self.assertEqual(self.by_name(result)["agy"]["status"], "没有")

    def test_missing_tool(self):
        os.remove(self.app / "agent")
        cursor = self.by_name(self.run_scan())["cursor"]
        self.assertEqual(cursor["status"], "没有")
        self.assertIsNone(cursor["login"])
        self.assertIn("行政", cursor["fix"])

    def test_slow_item_times_out_and_scan_stays_fast(self):
        started = time.monotonic()
        git = self.by_name(self.run_scan())["git"]
        self.assertLess(time.monotonic() - started, 8, "每项限时，并行跑，不能一项一项等（一项一项等要 10 秒以上）")
        self.assertEqual(git["status"], "有")
        self.assertEqual(git["version"], "")
        self.assertIn("超时", git["note"])

    def test_saved_cli_cards_included_api_and_broken_skipped(self):
        card = {"id": "demo", "kind": "cli", "name": "演示", "command": "demo-cli", "run": ["-p", "{prompt}"]}
        blocks.save_card(card, root=self.root)
        blocks.save_card({"id": "qwen", "kind": "llm_api", "name": "通义", "provider": "bailian",
                          "base_url": "https://x.example/v1", "model": "m", "key_account": "bailian"},
                         root=self.root)
        (self.root / "基础层" / "bad.json").write_text("[]", encoding="utf-8")
        items = self.by_name(self.run_scan())
        self.assertEqual(items["demo"]["version"], "demo 1.0")
        self.assertEqual(items["demo"]["kind"], "tool")
        self.assertNotIn("qwen", items)
        self.assertNotIn("bad", items)

    def test_saved_card_overrides_builtin(self):
        blocks.save_card(dict(blocks.builtin_card("codex"), name="我的 Codex", source="clerk"), root=self.root)
        items = [i for i in self.run_scan()["items"] if i["name"] == "codex"]
        self.assertEqual(len(items), 1, "同一个编号只查一次")
        self.assertEqual(items[0]["label"], "我的 Codex")

    def test_order(self):
        names = [i["name"] for i in self.run_scan()["items"]]
        self.assertEqual(names[:7], ["node", "npm", "brew", "git", "codex", "cursor", "agy"])

    def test_no_raw_output_kept(self):
        result = self.run_scan()
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("someone@example.com", text)
        self.assertNotIn("Logged in using", text)
        record = (self.root / "记录" / "env.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("someone@example.com", record)

    def test_record_written_or_not(self):
        self.run_scan(record=False)
        self.assertFalse((self.root / "记录" / "env.jsonl").exists())
        self.run_scan()
        last = blocks.last_record("env", root=self.root, type="env")
        self.assertIn("node", [i["name"] for i in last["items"]])

    def test_result_has_time_and_is_json(self):
        result = self.run_scan()
        self.assertIn("time", result)
        json.dumps(result)

    def test_does_not_create_folder_when_not_recording(self):
        self.run_scan(record=False)
        self.assertFalse(self.root.exists())


class RunnerTest(EnvCase):
    def test_runs_absolute_path_without_shell(self):
        calls = []
        real = blocks_env.run_command

        def spy(args, path, timeout):
            calls.append((args, path))
            return real(args, path, timeout)

        self.run_scan(runner=spy)
        self.assertTrue(calls)
        for args, path in calls:
            self.assertIsInstance(args, list)
            self.assertTrue(os.path.isabs(args[0]), "按找到的完整路径运行")
        logins = [a for a, _ in calls if a[1:] == ["login", "status"]]
        self.assertEqual(len(logins), 1)

    def test_run_command_returns_code_and_combined_output(self):
        code, out = blocks_env.run_command([str(self.app / "codex"), "login", "status"], str(self.app), 2)
        self.assertEqual(code, 0)
        self.assertIn("Logged in", out)

    def test_run_command_timeout_raises(self):
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            blocks_env.run_command([str(self.app / "git"), "--version"], str(self.app), 0.3)

    def test_runner_error_is_reported_not_raised(self):
        def broken(args, path, timeout):
            raise OSError("坏了")

        node = self.by_name(self.run_scan(runner=broken))["node"]
        self.assertEqual(node["status"], "有")
        self.assertEqual(node["version"], "")
        self.assertTrue(node["note"])


if __name__ == "__main__":
    unittest.main()
