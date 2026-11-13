"""52 号单：按接法卡检测。

CLI：在新建的临时文件夹里让工具干一件小事（建一个文件），以文件真的建出来为准，不听工具自己说。
没核实过的卡，用户确认前一律不跑（用户 2026-11-08 拍板）。
大模型 API：发一句话；当行政用的还要测会不会用工具。
结果写进记录，不存工具原始输出、不存密钥；同一块同时只跑一个检测。
"""
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from collab import blocks, blocks_check
from collab.blocks import BlockError


def script(folder: Path, name: str, body: str):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


MAKES_FILE = 'pwd > "{marker}"; for a in "$@"; do last="$a"; done\n' \
             'f=$(printf "%s" "$last" | sed -n "s/.*写进新文件 \\([^ ]*\\) 的第一行.*/\\1/p"); n=$(cat 题目.txt); printf "%s\\n%s\\n" $((n+1)) $(ls | wc -l) > "$f"; echo done'


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "积木库"
        self.bin = self.base / "bin"
        self.marker = self.base / "ran_in.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def fake(self, name, body=None):
        return script(self.bin, name, (body or MAKES_FILE).replace("{marker}", str(self.marker)))

    def check(self, card, **over):
        args = dict(root=self.root, path=str(self.bin), timeout=5)
        args.update(over)
        return blocks_check.check_cli(card, **args)

    def preview(self, card):
        return blocks_check.preview(card, path=str(self.bin))

    def unverified(self, **over):
        card = {"id": "demo", "kind": "cli", "name": "演示", "command": "demo-cli", "run": ["-p", "{prompt}"]}
        card.update(over)
        return card


class VerifiedTest(CliCase):
    def test_builtin_shape_runs_without_extra_confirm(self):
        self.fake("codex")
        result = self.check(blocks.builtin_card("codex"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["verified"])
        self.assertIsInstance(result["seconds"], (int, float))
        self.assertRegex(result["reason"], r"[一-鿿]")

    def test_runs_in_fresh_temp_folder_then_cleans_up(self):
        self.fake("codex")
        self.check(blocks.builtin_card("codex"))
        folder = Path(self.marker.read_text().strip())
        self.assertNotEqual(folder.resolve(), Path.cwd().resolve())
        self.assertFalse(folder.exists(), "检测用的临时文件夹要删掉")

    def test_record_written(self):
        self.fake("codex")
        self.check(blocks.builtin_card("codex"))
        last = blocks.last_record("codex", root=self.root, type="check")
        self.assertTrue(last["ok"])
        self.assertTrue(last["verified"])


class UnverifiedTest(CliCase):
    def test_needs_user_approval_before_running(self):
        self.fake("demo-cli")
        with self.assertRaises(BlockError) as ctx:
            self.check(self.unverified())
        self.assertIn("确认", str(ctx.exception))
        self.assertFalse(self.marker.exists(), "用户确认前一律不跑")
        self.assertEqual(blocks.records("demo", root=self.root), [])

    def test_runs_after_approval(self):
        self.fake("demo-cli")
        card = self.unverified()
        result = self.check(card, approved=self.preview(card))
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["verified"])

    def test_custom_done_file(self):
        self.fake("demo-cli")
        card = self.unverified(done_when={"file": "我的检测.txt"})
        result = self.check(card, approved=self.preview(card))
        self.assertTrue(result["ok"], result)


class FailureTest(CliCase):
    def test_missing_command_not_run(self):
        result = self.check(blocks.builtin_card("codex"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing")
        self.assertIn("没装", result["reason"])
        self.assertTrue(result["how"])

    def test_exit_ok_but_no_file_is_failure(self):
        self.fake("codex", 'echo "全部完成，文件已建好"')
        result = self.check(blocks.builtin_card("codex"))
        self.assertFalse(result["ok"], "不听工具自己说，以文件为准")
        self.assertEqual(result["status"], "fail")

    def test_not_logged_in_explained(self):
        self.fake("codex", 'echo "Error: Not logged in. Please run codex login" >&2; exit 1')
        result = self.check(blocks.builtin_card("codex"))
        self.assertFalse(result["ok"])
        self.assertIn("登录", result["reason"])
        self.assertIn("codex", result["how"])

    def test_timeout_stops_tool(self):
        self.fake("codex", 'pwd > "{marker}"; exec /bin/sleep 30')
        started = time.monotonic()
        result = self.check(blocks.builtin_card("codex"), timeout=1)
        self.assertLess(time.monotonic() - started, 8)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["reason"])

    def test_no_raw_output_or_email_kept(self):
        self.fake("codex", 'echo "signed in as someone@example.com, key sk-abcdefghijklmnopqrstuvwxyz"; exit 3')
        result = self.check(blocks.builtin_card("codex"))
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("someone@example.com", text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)
        last = blocks.last_record("codex", root=self.root, type="check")
        self.assertIsNotNone(last, "输出里有像密钥的字，也不能让记录丢掉")
        self.assertFalse(last["ok"])
        self.assertNotIn("output", last)

    def test_api_card_rejected(self):
        api = {"id": "q", "kind": "llm_api", "name": "通义", "provider": "bailian",
               "base_url": "https://x.example/v1", "model": "m", "key_account": "bailian"}
        with self.assertRaises(BlockError):
            self.check(api)

    def test_relative_path_entries_ignored(self):
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            script(self.base, "codex", MAKES_FILE.replace("{marker}", str(self.marker)))
            result = self.check(blocks.builtin_card("codex"), path=".::bin")
        finally:
            os.chdir(cwd)
        self.assertEqual(result["status"], "missing", "当前文件夹和相对路径里的冒名程序不能用")
        self.assertFalse(self.marker.exists())


class OneAtATime(CliCase):
    def test_same_card_cannot_check_twice_at_once(self):
        self.fake("codex", 'pwd > "{marker}"; /bin/sleep 2; ' + MAKES_FILE.split(";", 1)[1])
        results = []
        worker = threading.Thread(target=lambda: results.append(self.check(blocks.builtin_card("codex"))))
        worker.start()
        for _ in range(50):
            if blocks_check.checking("codex"):
                break
            time.sleep(0.05)
        self.assertTrue(blocks_check.checking("codex"))
        with self.assertRaises(BlockError) as ctx:
            self.check(blocks.builtin_card("codex"))
        self.assertIn("正在检测", str(ctx.exception))
        worker.join()
        self.assertFalse(blocks_check.checking("codex"))
        self.assertTrue(results[0]["ok"], results)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, on_text=None, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ApiTest(unittest.TestCase):
    KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"
    CARD = {"id": "qwen", "kind": "llm_api", "name": "通义", "provider": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
            "key_account": "bailian"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.made = []

    def tearDown(self):
        self._tmp.cleanup()

    def factory(self, client):
        def make(base_url, model, api_key):
            self.made.append((base_url, model, api_key))
            return client
        return make

    def check(self, client, **over):
        args = dict(root=self.root, client_factory=self.factory(client))
        args.update(over)
        return blocks_check.check_api(self.CARD, self.KEY, **args)

    def test_one_sentence_ok(self):
        client = FakeClient([{"text": "收到", "tool_calls": []}])
        result = self.check(client)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["tools"], "不要求时不测工具")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.made, [(self.CARD["base_url"], "qwen-plus", self.KEY)])

    def test_tools_required_and_supported(self):
        client = FakeClient([{"text": "收到", "tool_calls": []},
                             {"text": "", "tool_calls": [{"id": "1", "name": "report_ready", "arguments": "{\"ok\": true}"}]}])
        result = self.check(client, need_tools=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["tools"])
        self.assertTrue(client.calls[1]["tools"])

    def test_tools_required_but_missing(self):
        client = FakeClient([{"text": "收到", "tool_calls": []}, {"text": "好的，已报告", "tool_calls": []}])
        result = self.check(client, need_tools=True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["tools"])
        self.assertIn("工具", result["reason"])

    def test_empty_reply_is_failure(self):
        result = self.check(FakeClient([{"text": "  ", "tool_calls": []}]))
        self.assertFalse(result["ok"])

    def test_model_error_explained(self):
        from desktop.clerk.client import ModelError
        result = self.check(FakeClient([ModelError("模型密钥不对，请检查后重新填写。")]))
        self.assertFalse(result["ok"])
        self.assertIn("密钥", result["reason"])

    def test_key_never_kept(self):
        self.check(FakeClient([{"text": "收到 " + self.KEY, "tool_calls": []}]))
        record = (self.root / "记录" / "qwen.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(self.KEY, record)
        last = blocks.last_record("qwen", root=self.root, type="check")
        self.assertTrue(last["ok"])

    def test_record_optional(self):
        self.check(FakeClient([{"text": "收到", "tool_calls": []}]), record=False)
        self.assertFalse((self.root / "记录").exists())

    def test_empty_key_rejected_without_calling(self):
        client = FakeClient([])
        with self.assertRaises(BlockError):
            blocks_check.check_api(self.CARD, "  ", root=self.root, client_factory=self.factory(client))
        self.assertEqual(self.made, [])

    def test_cli_card_rejected(self):
        with self.assertRaises(BlockError):
            blocks_check.check_api(blocks.builtin_card("codex"), self.KEY, root=self.root,
                                   client_factory=self.factory(FakeClient([])))


if __name__ == "__main__":
    unittest.main()
