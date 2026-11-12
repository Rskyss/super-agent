"""50 号单：积木库资料夹和接法卡。

整台电脑一份资料夹；接法卡能存、能读，格式不对要用中文说清；
预置 Codex、Cursor、agy 三张（只是模板，不自动进基础层）；检测和安装都留记录；
密钥永远不能写进接法卡和记录；拼命令不经过 shell。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab import blocks
from collab.blocks import BlockError


def cli_card(**over):
    card = {"id": "demo", "kind": "cli", "name": "演示工具", "command": "demo-cli",
            "run": ["-p", "{prompt}", "--model", "{model}"], "model": "m1"}
    card.update(over)
    return card


def api_card(**over):
    card = {"id": "qwen", "kind": "llm_api", "name": "通义", "provider": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
            "key_account": "bailian"}
    card.update(over)
    return card


class RootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "积木库"

    def tearDown(self):
        self._tmp.cleanup()


class HomeTest(RootCase):
    def test_default_home_is_app_support_folder(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPER_AGENT_BLOCKS_HOME", None)
            path = blocks.home()
        self.assertEqual(path.parts[-2:], ("超级助手", "积木库"))
        self.assertIn("Application Support", str(path))

    def test_env_overrides_and_nothing_is_created(self):
        target = self.root / "别处"
        with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(target)}):
            self.assertEqual(blocks.home(), target)
        self.assertFalse(target.exists(), "只是算出路径，不能顺手建文件夹")

    def test_explicit_root_wins(self):
        with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(self.root / "env")}):
            self.assertEqual(blocks.home(self.root), self.root)


class SaveLoadTest(RootCase):
    def test_round_trip_and_file_place(self):
        saved = blocks.save_card(cli_card(), root=self.root)
        path = self.root / "基础层" / "demo.json"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("演示工具", text, "中文直接写，不转义")
        self.assertEqual(blocks.load_card("demo", root=self.root), saved)
        self.assertEqual(list(path.parent.glob("*.tmp")), [], "写完不留临时文件")

    def test_defaults_filled(self):
        saved = blocks.save_card(cli_card(), root=self.root)
        self.assertEqual(saved["version_args"], ["--version"])
        self.assertEqual(saved["done_when"], {"file": "连通检测.txt"})
        self.assertEqual(saved["source"], "clerk")

    def test_api_card_round_trip(self):
        blocks.save_card(api_card(), root=self.root)
        self.assertEqual(blocks.load_card("qwen", root=self.root)["kind"], "llm_api")

    def test_save_overwrites_same_id(self):
        blocks.save_card(cli_card(), root=self.root)
        blocks.save_card(cli_card(name="改过名"), root=self.root)
        self.assertEqual(blocks.load_card("demo", root=self.root)["name"], "改过名")

    def test_missing_card(self):
        with self.assertRaises(BlockError) as ctx:
            blocks.load_card("nope", root=self.root)
        self.assertIn("没有", str(ctx.exception))

    def test_broken_file_reported_in_chinese(self):
        folder = self.root / "基础层"
        folder.mkdir(parents=True)
        (folder / "bad.json").write_text("{坏的", encoding="utf-8")
        with self.assertRaises(BlockError) as ctx:
            blocks.load_card("bad", root=self.root)
        self.assertRegex(str(ctx.exception), r"[一-鿿]")


class ValidateTest(RootCase):
    def bad(self, card, hint):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        message = str(ctx.exception)
        self.assertRegex(message, r"[一-鿿]", "报错要用中文")
        self.assertIn(hint, message)
        self.assertFalse((self.root / "基础层").exists())

    def test_not_a_dict(self):
        self.bad(["x"], "接法说明")

    def test_bad_id(self):
        for value in ("", "有中文", "UPPER", "a b", "../x", "a" * 41):
            self.bad(cli_card(id=value), "编号")

    def test_bad_kind(self):
        self.bad(cli_card(kind="mcp"), "类型")

    def test_empty_name(self):
        self.bad(cli_card(name=" "), "名字")

    def test_bad_command(self):
        for value in ("", "/usr/bin/codex", "co dex", "codex;rm", "../codex", "-codex"):
            self.bad(cli_card(command=value), "命令")

    def test_run_needs_prompt(self):
        self.bad(cli_card(run=["--model", "{model}"]), "{prompt}")
        self.bad(cli_card(run=[]), "{prompt}")
        self.bad(cli_card(run="-p {prompt}"), "{prompt}")

    def test_unknown_placeholder(self):
        self.bad(cli_card(run=["-p", "{prompt}", "{cwd}"]), "{cwd}")

    def test_model_placeholder_needs_model(self):
        self.bad(cli_card(model=""), "模型")
        card = cli_card()
        del card["model"]
        self.bad(card, "模型")

    def test_model_not_needed_without_placeholder(self):
        card = cli_card(run=["-p", "{prompt}"])
        del card["model"]
        blocks.validate_card(card)

    def test_login_check_shape(self):
        blocks.validate_card(cli_card(login_check={"args": ["status"], "ok_words": ["Logged in"]}))
        self.bad(cli_card(login_check={"args": [], "ok_words": ["x"]}), "登录")
        self.bad(cli_card(login_check={"args": ["status"], "ok_words": []}), "登录")

    def test_done_when_file_name_only(self):
        self.bad(cli_card(done_when={"file": "../x.txt"}), "文件")
        self.bad(cli_card(done_when={"file": "a/b.txt"}), "文件")

    def test_api_needs_https_and_fields(self):
        self.bad(api_card(base_url="http://example.com/v1"), "https")
        self.bad(api_card(model=""), "模型")
        self.bad(api_card(key_account=""), "钥匙串")
        self.bad(api_card(provider=""), "服务商")

    def test_unknown_field(self):
        self.bad(cli_card(extra=1), "extra")

    def test_bad_source(self):
        self.bad(cli_card(source="internet"), "来源")

    def test_secret_never_in_card(self):
        for field in ("key", "api_key", "API_KEY", "secret", "token", "password"):
            self.bad(dict(api_card(), **{field: "x"}), "密钥")
        self.bad(api_card(model="sk-abcdefghijklmnopqrstuvwx"), "密钥")
        self.bad(cli_card(login_check={"args": ["status", "sk-abcdefghijklmnopqrstuvwx"], "ok_words": ["ok"]}),
                 "密钥")

    def test_validate_returns_copy(self):
        card = cli_card()
        clean = blocks.validate_card(card)
        clean["run"].append("x")
        self.assertEqual(card["run"], ["-p", "{prompt}", "--model", "{model}"])


class ListRemoveTest(RootCase):
    def test_empty(self):
        self.assertEqual(blocks.list_cards(root=self.root), [])

    def test_broken_one_does_not_hide_others(self):
        blocks.save_card(cli_card(), root=self.root)
        blocks.save_card(api_card(), root=self.root)
        (self.root / "基础层" / "zz.json").write_text("[]", encoding="utf-8")
        (self.root / "基础层" / "说明.txt").write_text("不是卡", encoding="utf-8")
        items = blocks.list_cards(root=self.root)
        self.assertEqual([i["id"] for i in items], ["demo", "qwen", "zz"])
        broken = items[-1]
        self.assertIn("problem", broken)
        self.assertRegex(broken["problem"], r"[一-鿿]")
        self.assertNotIn("problem", items[0])

    def test_remove_keeps_records(self):
        blocks.save_card(cli_card(), root=self.root)
        blocks.add_record("demo", {"type": "check", "ok": True}, root=self.root)
        blocks.remove_card("demo", root=self.root)
        self.assertEqual(blocks.list_cards(root=self.root), [])
        self.assertEqual(len(blocks.records("demo", root=self.root)), 1, "移出基础层不删记录")

    def test_remove_missing(self):
        with self.assertRaises(BlockError):
            blocks.remove_card("nope", root=self.root)

    def test_remove_rejects_bad_id(self):
        with self.assertRaises(BlockError):
            blocks.remove_card("../基础层", root=self.root)


class BuiltinTest(RootCase):
    def test_three_builtins_valid(self):
        self.assertEqual(sorted(blocks.BUILTIN), ["agy", "codex", "cursor"])
        for name in blocks.BUILTIN:
            card = blocks.builtin_card(name)
            self.assertEqual(card["source"], "builtin")
            self.assertEqual(card["kind"], "cli")
            self.assertEqual(card["id"], name)
            blocks.validate_card(card)

    def test_commands_match_real_tools(self):
        self.assertEqual(blocks.builtin_card("codex")["command"], "codex")
        self.assertEqual(blocks.builtin_card("cursor")["command"], "agent")
        self.assertEqual(blocks.builtin_card("agy")["command"], "agy")

    def test_login_checks(self):
        self.assertEqual(blocks.builtin_card("codex")["login_check"]["args"], ["login", "status"])
        self.assertEqual(blocks.builtin_card("cursor")["login_check"]["args"], ["status"])
        self.assertNotIn("login_check", blocks.builtin_card("agy"), "agy 没有查登录的命令，不要编")

    def test_builtin_is_a_copy(self):
        card = blocks.builtin_card("codex")
        card["run"].append("x")
        self.assertNotIn("x", blocks.builtin_card("codex")["run"])

    def test_unknown_builtin(self):
        with self.assertRaises(BlockError):
            blocks.builtin_card("claude")

    def test_builtins_not_auto_saved(self):
        blocks.builtin_card("codex")
        self.assertEqual(blocks.list_cards(root=self.root), [], "预置卡只是模板，不自动进基础层")

    def test_codex_runs_exec_without_shell(self):
        args = blocks.build_args(blocks.builtin_card("codex"), "新建 连通检测.txt")
        self.assertEqual(args[0], "codex")
        self.assertIn("exec", args)
        self.assertIn("新建 连通检测.txt", args)


class BuildArgsTest(unittest.TestCase):
    def test_prompt_stays_one_argument(self):
        nasty = "写 \"OK\"; rm -rf ~ && echo $(whoami) `id` | cat"
        args = blocks.build_args(cli_card(), nasty)
        self.assertEqual(args, ["demo-cli", "-p", nasty, "--model", "m1"])

    def test_model_override(self):
        self.assertEqual(blocks.build_args(cli_card(), "hi", model="m2")[-1], "m2")

    def test_placeholder_inside_element(self):
        card = cli_card(run=["--prompt={prompt}", "--model={model}"])
        self.assertEqual(blocks.build_args(card, "a b"), ["demo-cli", "--prompt=a b", "--model=m1"])

    def test_braces_in_prompt_not_expanded(self):
        args = blocks.build_args(cli_card(), "{model} {prompt}")
        self.assertEqual(args[2], "{model} {prompt}")

    def test_api_card_has_no_args(self):
        with self.assertRaises(BlockError):
            blocks.build_args(api_card(), "hi")

    def test_validates_first(self):
        with self.assertRaises(BlockError):
            blocks.build_args(cli_card(command="a;b"), "hi")


class RecordTest(RootCase):
    def test_add_and_read(self):
        first = blocks.add_record("demo", {"type": "check", "ok": False, "reason": "没登录"}, root=self.root)
        self.assertIn("time", first)
        blocks.add_record("demo", {"type": "install", "ok": True}, root=self.root)
        blocks.add_record("demo", {"type": "check", "ok": True}, root=self.root)
        items = blocks.records("demo", root=self.root)
        self.assertEqual([i["type"] for i in items], ["check", "install", "check"])
        self.assertEqual(blocks.last_record("demo", root=self.root)["type"], "check")
        self.assertTrue(blocks.last_record("demo", root=self.root, type="check")["ok"])
        self.assertEqual(blocks.last_record("demo", root=self.root, type="install")["type"], "install")
        path = self.root / "记录" / "demo.jsonl"
        self.assertIn("没登录", path.read_text(encoding="utf-8"))

    def test_bad_lines_skipped(self):
        blocks.add_record("demo", {"type": "check", "ok": True}, root=self.root)
        with open(self.root / "记录" / "demo.jsonl", "a", encoding="utf-8") as fh:
            fh.write("半行坏的\n[]\n")
        blocks.add_record("demo", {"type": "check", "ok": False}, root=self.root)
        self.assertEqual(len(blocks.records("demo", root=self.root)), 2)

    def test_limit_keeps_latest(self):
        for n in range(5):
            blocks.add_record("demo", {"type": "check", "n": n}, root=self.root)
        self.assertEqual([i["n"] for i in blocks.records("demo", root=self.root, limit=2)], [3, 4])

    def test_no_records(self):
        self.assertEqual(blocks.records("demo", root=self.root), [])
        self.assertIsNone(blocks.last_record("demo", root=self.root))

    def test_record_rejects_secret(self):
        with self.assertRaises(BlockError):
            blocks.add_record("demo", {"type": "check", "output": "key sk-abcdefghijklmnopqrstuvwx"}, root=self.root)
        with self.assertRaises(BlockError):
            blocks.add_record("demo", {"type": "check", "api_key": "x"}, root=self.root)

    def test_record_needs_type_and_good_id(self):
        with self.assertRaises(BlockError):
            blocks.add_record("demo", {"ok": True}, root=self.root)
        with self.assertRaises(BlockError):
            blocks.add_record("../x", {"type": "check"}, root=self.root)

    def test_record_is_json_lines(self):
        blocks.add_record("demo", {"type": "env", "items": ["node"]}, root=self.root)
        line = (self.root / "记录" / "demo.jsonl").read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(line)["items"], ["node"])


if __name__ == "__main__":
    unittest.main()
