"""50 号单验收时查出、验收测试没覆盖的问题。行政起草的卡什么都可能写错，必须给中文提示，不能崩。"""
import json
import tempfile
import unittest
from pathlib import Path

from collab import blocks
from collab.blocks import BlockError

BASE = {"id": "demo", "kind": "cli", "name": "演示", "command": "demo", "run": ["-p", "{prompt}"]}


class WrongTypesDoNotCrash(unittest.TestCase):
    def bad(self, card):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        self.assertRegex(str(ctx.exception), r"[一-鿿]")

    def test_wrong_types_give_chinese_errors(self):
        for field, value in (("id", 5), ("name", 5), ("command", ["a"]), ("kind", None),
                             ("run", ["-p", "{prompt}", 3]), ("model", 7),
                             ("version_args", "--version"), ("version_args", [1]),
                             ("login_check", "status"), ("login_check", {"args": [1], "ok_words": ["ok"]}),
                             ("login_check", {"args": ["status"], "ok_words": [None]}),
                             ("done_when", "ok.txt"), ("done_when", {"file": 3}),
                             ("source", 1), ("note", 3)):
            with self.subTest(field=field, value=value):
                self.bad(dict(BASE, **{field: value}))

    def test_api_wrong_types(self):
        api = {"id": "q", "kind": "llm_api", "name": "通义", "provider": "bailian",
               "base_url": "https://x.example/v1", "model": "m", "key_account": "bailian"}
        for field, value in (("base_url", 5), ("provider", ["a"]), ("model", 1), ("key_account", {})):
            with self.subTest(field=field):
                self.bad(dict(api, **{field: value}))

    def test_empty_done_when_file_rejected(self):
        self.bad(dict(BASE, done_when={"file": ""}))
        self.bad(dict(BASE, done_when={}))

    def test_any_unknown_placeholder_rejected(self):
        for value in ("{CWD}", "{cwd1}", "{ prompt }", "{}"):
            with self.subTest(value=value):
                self.bad(dict(BASE, run=["-p", "{prompt}", value]))

    def test_non_string_ids_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fn in (lambda: blocks.load_card(5, root=tmp), lambda: blocks.remove_card(None, root=tmp),
                       lambda: blocks.records("../基础层/demo", root=tmp),
                       lambda: blocks.last_record(5, root=tmp)):
                with self.assertRaises(BlockError):
                    fn()


class LoadValidates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "基础层").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        (self.root / "基础层" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_list_content_rejected(self):
        self.write("bad", [])
        with self.assertRaises(BlockError):
            blocks.load_card("bad", root=self.root)

    def test_missing_fields_rejected(self):
        self.write("half", {"id": "half", "kind": "cli"})
        with self.assertRaises(BlockError):
            blocks.load_card("half", root=self.root)

    def test_loaded_card_has_defaults(self):
        self.write("demo", BASE)
        self.assertEqual(blocks.load_card("demo", root=self.root)["done_when"], {"file": "连通检测.txt"})

    def test_file_name_must_match_id(self):
        self.write("other", BASE)
        with self.assertRaises(BlockError):
            blocks.load_card("other", root=self.root)

    def test_error_names_the_card(self):
        self.write("bad", [])
        with self.assertRaises(BlockError) as ctx:
            blocks.load_card("bad", root=self.root)
        self.assertIn("bad", str(ctx.exception))


class Records(unittest.TestCase):
    def test_caller_record_untouched_and_type_is_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = {"type": "check"}
            saved = blocks.add_record("demo", record, root=tmp)
            self.assertEqual(record, {"type": "check"})
            self.assertIn("time", saved)
            with self.assertRaises(BlockError):
                blocks.add_record("demo", {"type": 5}, root=tmp)
            with self.assertRaises(BlockError):
                blocks.add_record("demo", {"type": " "}, root=tmp)


class BuiltinShape(unittest.TestCase):
    def test_builtin_is_mapping_by_id(self):
        self.assertEqual(sorted(blocks.BUILTIN), ["agy", "codex", "cursor"])
        self.assertEqual(blocks.BUILTIN["codex"]["command"], "codex")


if __name__ == "__main__":
    unittest.main()


class ReviewRoundOne(unittest.TestCase):
    """独立复查第一轮：接法卡不能变成运行任意程序的口子；密钥格式要认全；坏数据不能让全部读不出。"""

    def bad(self, card):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        self.assertRegex(str(ctx.exception), r"[一-鿿]")
        return str(ctx.exception)

    def test_dangerous_commands_rejected(self):
        for name in ("bash", "sh", "zsh", "env", "sudo", "rm", "curl", "wget", "python3", "python3.12", "node",
                     "osascript", "open", "xargs", "npm", "npx", "brew", "pip3", "git", "ssh", "BASH"):
            with self.subTest(command=name):
                self.bad(dict(BASE, command=name))

    def test_version_args_fixed_forms(self):
        for args in (["--version"], ["-V"], ["-v"], ["version"]):
            blocks.validate_card(dict(BASE, version_args=args))
        for args in (["install", "evil"], ["--version", "x"], ["-c", "id"]):
            with self.subTest(args=args):
                self.bad(dict(BASE, version_args=args))

    def test_login_args_fixed_forms(self):
        for args in (["status"], ["login", "status"], ["auth", "status"], ["whoami"]):
            blocks.validate_card(dict(BASE, login_check={"args": args, "ok_words": ["Logged in"]}))
        self.bad(dict(BASE, login_check={"args": ["logout"], "ok_words": ["Logged in"]}))
        self.bad(dict(BASE, login_check={"args": ["status"], "ok_words": [""]}))
        self.bad(dict(BASE, login_check={"args": ["status"], "ok_words": ["Logged in"], "shell": "x"}))

    def test_done_when_extra_fields_and_odd_names(self):
        self.bad(dict(BASE, done_when={"file": "a.txt", "x": 1}))
        for name in ("a\x00b", "x" * 101, "~", " .hidden", "a.txt "):
            with self.subTest(name=name):
                self.bad(dict(BASE, done_when={"file": name}))

    def test_modern_secret_formats(self):
        for text in ("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "sk-proj-AbCdEf_GhIjKlMnOpQrStUv",
                     "AIzaSyA1234567890abcdefghijklmnopqrstu", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                     "Bearer abcdefghijklmno1234567890"):
            with self.subTest(text=text):
                self.assertIn("密钥", self.bad(dict(BASE, note=text)))
        for field in ("api-key", "access_token", "Authorization", "privateKey", "client_secret"):
            with self.subTest(field=field):
                self.assertIn("密钥", self.bad(dict(BASE, done_when={"file": "a.txt", field: "x"})))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockError):
                blocks.add_record("demo", {"type": "c", "out": "Authorization: Bearer sk-ant-api03-AbCdEfGhIjKlMn"},
                                  root=tmp)

    def test_key_account_is_not_a_secret(self):
        blocks.validate_card({"id": "q", "kind": "llm_api", "name": "通义", "provider": "bailian",
                              "base_url": "https://x.example/v1", "model": "m", "key_account": "bailian"})

    def test_kind_and_name_odd_types(self):
        for value in ([], {}, 1):
            with self.subTest(value=value):
                self.bad(dict(BASE, kind=value))
        with self.assertRaises(BlockError):
            blocks.builtin_card([])

    def test_not_json_safe(self):
        self.bad(dict(BASE, note="a\udc80"))
        with tempfile.TemporaryDirectory() as tmp:
            for value in ("\udc80", Path("x"), b"x"):
                with self.subTest(value=value), self.assertRaises(BlockError):
                    blocks.add_record("demo", {"type": "c", "out": value}, root=tmp)
            with self.assertRaises(BlockError):
                blocks.save_card(dict(BASE, name="a\udc80"), root=tmp)
            self.assertEqual(list(Path(tmp).rglob("*.tmp")), [])

    def test_deep_nesting_does_not_break_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocks.save_card(BASE, root=tmp)
            (Path(tmp) / "基础层" / "deep.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
            items = blocks.list_cards(root=tmp)
            self.assertEqual([i["id"] for i in items], ["deep", "demo"])
            self.assertIn("problem", items[0])

    def test_bad_bytes_in_records_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocks.add_record("demo", {"type": "check", "ok": True}, root=tmp)
            with open(Path(tmp) / "记录" / "demo.jsonl", "ab") as fh:
                fh.write(b"\xff\xfe broken\n")
            blocks.add_record("demo", {"type": "check", "ok": False}, root=tmp)
            self.assertEqual(len(blocks.records("demo", root=tmp)), 2)

    def test_concurrent_save_same_card(self):
        import threading
        errors = []
        with tempfile.TemporaryDirectory() as tmp:
            def work(name):
                for _ in range(100):
                    try:
                        blocks.save_card(dict(BASE, name=name), root=tmp)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)
            threads = [threading.Thread(target=work, args=(n,)) for n in ("短", "很长很长很长很长的名字")]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(errors, [])
            blocks.load_card("demo", root=tmp)
            self.assertEqual(list(Path(tmp).rglob("*.tmp")), [])

    def test_records_limit_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("2", -1, 0):
                with self.subTest(limit=bad), self.assertRaises(BlockError):
                    blocks.records("demo", root=tmp, limit=bad)

    def test_home_expands_tilde(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": "~/x"}):
            self.assertEqual(blocks.home(), Path.home() / "x")

    def test_build_args_odd_inputs(self):
        with self.assertRaises(BlockError):
            blocks.build_args(dict(BASE, run=["-m", "{model}", "{prompt}"], model="m"), "hi", model=5)
        with self.assertRaises(BlockError):
            blocks.build_args(BASE, None)


class ReviewRoundTwoCards(unittest.TestCase):
    def bad(self, card):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        return str(ctx.exception)

    def test_more_secret_formats(self):
        for text in ("github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "AKIAIOSFODNN7EXAMPLE"):
            with self.subTest(text=text):
                self.assertIn("密钥", self.bad(dict(BASE, note=text)))
        for flag in ("--api-key", "--apikey=abc.def", "--token", "--password", "--secret", "--auth-token"):
            with self.subTest(flag=flag):
                self.assertIn("密钥", self.bad(dict(BASE, run=["-p", "{prompt}", flag, "x"])))

    def test_safety_off_switches_rejected(self):
        for flag in ("--dangerously-skip-permissions", "--yolo", "danger-full-access",
                     "--dangerously-bypass-approvals-and-sandbox", "--bypass-sandbox"):
            with self.subTest(flag=flag):
                self.assertIn("安全", self.bad(dict(BASE, run=["-p", "{prompt}", flag])))

    def test_builtins_still_pass(self):
        for name in blocks.BUILTIN:
            blocks.builtin_card(name)

    def test_ok_words_too_short(self):
        self.bad(dict(BASE, login_check={"args": ["status"], "ok_words": ["e"]}))
        self.bad(dict(BASE, login_check={"args": ["status"], "ok_words": ["ok"]}))
        blocks.validate_card(dict(BASE, login_check={"args": ["status"], "ok_words": ["Logged in"]}))

    def test_usage_fields_are_not_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocks.add_record("demo", {"type": "check", "tokens_used": 120, "max_tokens": 4000}, root=tmp)
            for field in ("access_token", "refresh_token", "token"):
                with self.subTest(field=field), self.assertRaises(BlockError):
                    blocks.add_record("demo", {"type": "check", field: "x"}, root=tmp)


class VerifiedCards(unittest.TestCase):
    """用户 2026-11-08 拍板：核实过的工具放行，名单外的亮出完整命令、你再确认。"""

    def test_builtins_are_verified(self):
        for name in blocks.BUILTIN:
            self.assertTrue(blocks.is_verified(blocks.builtin_card(name)))

    def test_clerk_copy_of_builtin_is_verified(self):
        card = dict(blocks.builtin_card("codex"), source="clerk", name="我的 Codex")
        self.assertTrue(blocks.is_verified(card), "命令和参数跟预置一模一样，就算核实过")

    def test_changed_args_not_verified(self):
        card = blocks.builtin_card("codex")
        card["run"] = card["run"] + ["--search"]
        self.assertFalse(blocks.is_verified(card))
        card = blocks.builtin_card("cursor")
        card["login_check"] = {"args": ["whoami"], "ok_words": ["Logged in"]}
        self.assertFalse(blocks.is_verified(card))

    def test_unknown_command_not_verified(self):
        self.assertFalse(blocks.is_verified(BASE))

    def test_api_cards_not_judged_here(self):
        api = {"id": "q", "kind": "llm_api", "name": "通义", "provider": "bailian",
               "base_url": "https://x.example/v1", "model": "m", "key_account": "bailian"}
        self.assertFalse(blocks.is_verified(api))

    def test_invalid_card_raises(self):
        with self.assertRaises(BlockError):
            blocks.is_verified(dict(BASE, command="bash"))

    def test_command_preview_readable(self):
        text = blocks.command_preview(dict(BASE, run=["-p", "{prompt}", "--model", "{model}"], model="m1"))
        self.assertEqual(text, "demo -p （交代） --model m1")
        text = blocks.command_preview(dict(BASE, run=["--x", "a b", "{prompt}"]))
        self.assertIn("'a b'", text, "带空格的参数要看得出是一个整体")


class ReviewRoundThreeCards(unittest.TestCase):
    def bad(self, card):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        return str(ctx.exception)

    def test_model_name_shape(self):
        codex = blocks.builtin_card("codex")
        for model in ("--dangerously-bypass-approvals-and-sandbox", "-c", "a b", "", "x;y"):
            with self.subTest(model=model):
                self.bad(dict(codex, model=model))
        for model in ("gpt-6.1-sol", "qwen-plus", "deepseek/deepseek-flash", "claude@x", "a:b+c_1.2"):
            blocks.validate_card(dict(codex, model=model))
        with self.assertRaises(BlockError):
            blocks.build_args(codex, "hi", model="-c")
        api = {"id": "q", "kind": "llm_api", "name": "通义", "provider": "bailian",
               "base_url": "https://x.example/v1", "model": "--x", "key_account": "bailian"}
        self.bad(api)

    def test_secret_flags_by_name(self):
        for flag in ("--openai-api-key=abc123", "--anthropic-api-key", "--access-token=abc", "--key=abc123",
                     "--auth=x", "--client_secret"):
            with self.subTest(flag=flag):
                self.assertIn("密钥", self.bad(dict(BASE, run=["-p", "{prompt}", flag])))
        for flag in ("--tokens=100", "--max-tokens", "--author=me", "--no-bypass-cache", "--max_output_tokens=9"):
            with self.subTest(flag=flag):
                blocks.validate_card(dict(BASE, run=["-p", "{prompt}", flag]))

    def test_bearer_words_in_note_not_secret(self):
        blocks.validate_card(dict(BASE, note="用 Bearer token-based-authentication 登录"))

    def test_more_dangerous_commands(self):
        for name in ("caffeinate", "nice", "time", "script", "arch", "tmux", "vim", "expect", "sqlite3", "pkill",
                     "timeout", "make", "docker", "awk", "tar", "tee", "sandbox-exec", "uvx", "bunx", "swift"):
            with self.subTest(command=name):
                self.bad(dict(BASE, command=name))

    def test_preview_with_embedded_prompt(self):
        text = blocks.command_preview(dict(BASE, run=["--message={prompt}"]))
        self.assertNotIn("\x00", text)
        self.assertIn("（交代）", text)

    def test_preview_shows_executable(self):
        self.assertTrue(blocks.command_preview(BASE, executable="/opt/x/demo").startswith("/opt/x/demo "))
