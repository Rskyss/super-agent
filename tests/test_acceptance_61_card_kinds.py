"""61 号单：接法卡扩展和“联网搜索”积木。

接法卡分“AI 助手（agent）/ 普通工具（tool）”：普通工具没有交代，用一条检测命令（probe）判断能不能用；
卡里只记要哪几把密钥、交给工具时叫什么名字（secrets），不记密钥本身；
新的积木种类“联网搜索”（kind=search），预置 Tavily 一张，接法写死在程序里；
老卡（Codex、Cursor、agy 和用户基础层里已有的卡）原样能用。
依据：docs/积木库/沙盒接入/方案.md 技术设计“接法卡新增”。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab import blocks, blocks_env
from collab.blocks import BlockError


def agent_card(**over):
    card = {"id": "demo", "kind": "cli", "name": "演示助手", "command": "demo-cli",
            "run": ["-p", "{prompt}", "--model", "{model}"], "model": "m1"}
    card.update(over)
    return card


def tool_card(**over):
    card = {"id": "maptool", "kind": "cli", "role": "tool", "name": "地图工具", "command": "map-tool",
            "probe": ["search", "--keyword", "天安门", "--limit", "1"]}
    card.update(over)
    return card


class RootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "积木库"

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefused(self, card, *words):
        with self.assertRaises(BlockError) as ctx:
            blocks.validate_card(card)
        msg = str(ctx.exception)
        self.assertRegex(msg, r"[一-鿿]", "拒收原因要用中文说")
        for w in words:
            self.assertIn(w, msg)
        return msg


class OldCardsUnchanged(RootCase):
    """老卡一个字段都不用加，读出来和以前一样，核实照旧。"""

    def test_builtins_still_valid_and_verified(self):
        for name in ("codex", "cursor", "agy"):
            card = blocks.builtin_card(name)
            self.assertEqual(blocks.category(card), "agent")
            self.assertTrue(blocks.is_verified(card), name)
            self.assertEqual(card["done_when"], {"file": blocks.DEFAULT_DONE_FILE})

    def test_old_saved_card_without_role_loads(self):
        folder = self.root / blocks.CARD_DIR
        folder.mkdir(parents=True)
        old = agent_card()
        (folder / "demo.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        card = blocks.load_card("demo", self.root)
        self.assertEqual(card["command"], "demo-cli")
        self.assertEqual(blocks.category(card), "agent")
        self.assertEqual(blocks.build_args(card, "你好")[:3], ["demo-cli", "-p", "你好"])

    def test_agent_role_written_explicitly_is_same(self):
        card = blocks.validate_card(agent_card(role="agent"))
        self.assertEqual(blocks.category(card), "agent")

    def test_old_api_card_category(self):
        card = blocks.validate_card({"id": "qwen", "kind": "llm_api", "name": "通义", "provider": "bailian",
                                     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                     "model": "qwen-plus", "key_account": "bailian"})
        self.assertEqual(blocks.category(card), "llm_api")


class RoleField(RootCase):
    def test_unknown_role_refused(self):
        self.assertRefused(agent_card(role="robot"))

    def test_role_must_be_text(self):
        self.assertRefused(agent_card(role=["tool"]))

    def test_agent_cannot_carry_probe(self):
        self.assertRefused(agent_card(probe=["status"]))

    def test_agent_cannot_carry_ok_words(self):
        self.assertRefused(agent_card(ok_words=["成功"]))


class ToolCard(RootCase):
    def test_minimal_tool_card_ok(self):
        card = blocks.validate_card(tool_card())
        self.assertEqual(blocks.category(card), "tool")
        self.assertNotIn("done_when", card, "普通工具没有“新建文件”这种检测，不能补上 done_when")
        self.assertNotIn("run", card)

    def test_tool_saved_and_listed(self):
        blocks.save_card(tool_card(), self.root)
        listed = blocks.list_cards(self.root)
        self.assertEqual([c["id"] for c in listed], ["maptool"])
        self.assertNotIn("problem", listed[0])

    def test_tool_needs_probe(self):
        card = tool_card()
        del card["probe"]
        self.assertRefused(card)

    def test_tool_probe_must_be_nonempty_list_of_text(self):
        self.assertRefused(tool_card(probe=[]))
        self.assertRefused(tool_card(probe="search 天安门"))
        self.assertRefused(tool_card(probe=["search", 3]))

    def test_tool_probe_has_no_placeholders(self):
        self.assertRefused(tool_card(probe=["search", "{prompt}"]))
        self.assertRefused(tool_card(probe=["search", "--model={model}"]))

    def test_tool_probe_cannot_write_secret_flag(self):
        self.assertRefused(tool_card(probe=["search", "--api-key", "x"]))
        self.assertRefused(tool_card(probe=["search", "--token=abc"]))

    def test_tool_probe_cannot_turn_off_safety(self):
        self.assertRefused(tool_card(probe=["run", "--dangerously-skip-permissions"]))

    def test_tool_probe_odd_text_refused(self):
        self.assertRefused(tool_card(probe=["search", "a\nb"]))
        self.assertRefused(tool_card(probe=["search", "x" * 501]))

    def test_tool_probe_not_too_long(self):
        self.assertRefused(tool_card(probe=["a"] * 21))

    def test_tool_dangerous_command_refused(self):
        self.assertRefused(tool_card(command="bash"))
        self.assertRefused(tool_card(command="npm"))

    def test_tool_cannot_carry_run_model_or_done_when(self):
        self.assertRefused(tool_card(run=["{prompt}"]))
        self.assertRefused(tool_card(model="m1"))
        self.assertRefused(tool_card(done_when={"file": "a.txt"}))

    def test_ok_words(self):
        card = blocks.validate_card(tool_card(ok_words=["success", "成功"]))
        self.assertEqual(card["ok_words"], ["success", "成功"])
        self.assertRefused(tool_card(ok_words=[]))
        self.assertRefused(tool_card(ok_words=["a"]))  # 太短的判断字样什么输出都能碰上
        self.assertRefused(tool_card(ok_words=["ok", 1]))
        self.assertRefused(tool_card(ok_words=["word"] * 6))

    def test_tool_never_verified(self):
        self.assertFalse(blocks.is_verified(tool_card()))

    def test_tool_build_args_refused_probe_args_ok(self):
        card = tool_card()
        with self.assertRaises(BlockError):
            blocks.build_args(card, "你好")
        self.assertEqual(blocks.probe_args(card), ["map-tool", "search", "--keyword", "天安门", "--limit", "1"])

    def test_probe_args_refused_for_agent(self):
        with self.assertRaises(BlockError):
            blocks.probe_args(agent_card())

    def test_preview_of_tool_shows_probe(self):
        text = blocks.command_preview(tool_card(), "/usr/local/bin/map-tool")
        self.assertEqual(text, "/usr/local/bin/map-tool search --keyword '天安门' --limit 1")  # 和 AI 助手的预览同一种加引号规则

    def test_tool_login_check_still_allowed(self):
        card = blocks.validate_card(tool_card(login_check={"args": ["whoami"], "ok_words": ["Logged"]}))
        self.assertEqual(card["login_check"]["args"], ["whoami"])


class VersionArgs(RootCase):
    def test_empty_version_args_means_no_version_command(self):
        for card in (tool_card(version_args=[]), agent_card(version_args=[])):
            self.assertEqual(blocks.validate_card(card)["version_args"], [])

    def test_default_still_dash_dash_version(self):
        self.assertEqual(blocks.validate_card(tool_card())["version_args"], ["--version"])

    def test_other_forms_still_refused(self):
        self.assertRefused(tool_card(version_args=["info"]))


class Secrets(RootCase):
    def test_secrets_ok_and_word_secrets_is_not_a_key(self):
        card = blocks.validate_card(tool_card(secrets=[{"env": "AMAP_KEY", "label": "Key"},
                                                       {"env": "AMAP_SECURITY_KEY", "label": "安全密钥"}]))
        self.assertEqual([s["env"] for s in card["secrets"]], ["AMAP_KEY", "AMAP_SECURITY_KEY"])

    def test_agent_may_have_secrets_but_then_not_verified(self):
        card = dict(blocks.builtin_card("codex"), source="clerk",
                    secrets=[{"env": "OPENAI_API_KEY", "label": "API Key"}])
        self.assertEqual(blocks.validate_card(card)["secrets"][0]["env"], "OPENAI_API_KEY")
        self.assertFalse(blocks.is_verified(card), "带了密钥就和预置卡不一样，要亮出来让用户确认")

    def test_secret_value_never_in_card(self):
        self.assertRefused(tool_card(secrets=[{"env": "AMAP_KEY", "label": "Key", "value": "abc"}]))
        self.assertRefused(tool_card(secrets=[{"env": "OPENAI_API_KEY", "label": "sk-" + "a" * 30}]))

    def test_shape(self):
        self.assertRefused(tool_card(secrets=[]))
        self.assertRefused(tool_card(secrets={"env": "AMAP_KEY", "label": "Key"}))
        self.assertRefused(tool_card(secrets=["AMAP_KEY"]))
        self.assertRefused(tool_card(secrets=[{"env": "AMAP_KEY"}]))
        self.assertRefused(tool_card(secrets=[{"env": "AMAP_KEY", "label": ""}]))
        self.assertRefused(tool_card(secrets=[{"env": "AMAP_KEY", "label": "很" * 21}]))

    def test_at_most_five_and_no_duplicates(self):
        many = [{"env": f"KEY_{i}", "label": f"第{i}把"} for i in range(6)]
        self.assertRefused(tool_card(secrets=many))
        dup = [{"env": "AMAP_KEY", "label": "a"}, {"env": "AMAP_KEY", "label": "b"}]
        self.assertRefused(tool_card(secrets=dup))

    def test_env_name_format(self):
        for bad in ("amap_key", "1KEY", "A", "AMAP-KEY", "AMAP KEY", "K" * 65, "密钥"):
            self.assertRefused(tool_card(secrets=[{"env": bad, "label": "Key"}]))

    def test_dangerous_env_names_refused(self):
        for bad in ("PATH", "HOME", "SHELL", "NODE_OPTIONS", "BASH_ENV", "ENV", "DYLD_INSERT_LIBRARIES",
                    "LD_PRELOAD", "PYTHONPATH", "PYTHONSTARTUP", "NPM_CONFIG_PREFIX", "HOMEBREW_PREFIX",
                    "GIT_SSH_COMMAND", "SSH_AUTH_SOCK", "HTTPS_PROXY", "SUPER_AGENT_BLOCKS_HOME"):
            self.assertRefused(tool_card(secrets=[{"env": bad, "label": "Key"}]))

    def test_secret_account(self):
        self.assertEqual(blocks.secret_account("maptool", "AMAP_KEY"), "cli:maptool:AMAP_KEY")
        with self.assertRaises(BlockError):
            blocks.secret_account("Bad Id", "AMAP_KEY")
        with self.assertRaises(BlockError):
            blocks.secret_account("maptool", "PATH")


class SearchBlock(RootCase):
    def test_preset_tavily(self):
        card = blocks.search_card("tavily")
        self.assertEqual(card["id"], "web-search")
        self.assertEqual(card["kind"], "search")
        self.assertEqual(card["provider"], "tavily")
        self.assertEqual(card["key_account"], "tavily")
        self.assertEqual(card["source"], "builtin")
        self.assertEqual(blocks.category(card), "search")
        self.assertIn("联网搜索", card["name"])

    def test_unknown_preset(self):
        with self.assertRaises(BlockError):
            blocks.search_card("google")

    def test_saved_and_listed(self):
        blocks.save_card(blocks.search_card("tavily"), self.root)
        listed = blocks.list_cards(self.root)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("problem", listed[0])
        self.assertEqual(listed[0]["kind"], "search")

    def test_only_tavily_and_fixed_account(self):
        base = dict(blocks.search_card("tavily"))
        self.assertRefused(dict(base, provider="bing"))
        self.assertRefused(dict(base, key_account="other:x.com"))

    def test_no_address_or_extra_fields(self):
        base = dict(blocks.search_card("tavily"))
        self.assertRefused(dict(base, base_url="https://evil.example.com"))
        self.assertRefused(dict(base, command="tavily"))

    def test_search_not_cli(self):
        card = blocks.search_card("tavily")
        self.assertFalse(blocks.is_verified(card))
        with self.assertRaises(BlockError):
            blocks.build_args(card, "x")

    def test_kind_message_lists_search(self):
        msg = self.assertRefused(agent_card(kind="mcp"))
        self.assertIn("search", msg)


class CategoryLabels(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(blocks.CATEGORY_LABELS, {"agent": "AI 助手", "tool": "普通工具",
                                                  "llm_api": "大模型 API", "search": "联网搜索"})


class EnvScanWithNewCards(RootCase):
    """体检：没有查版本命令的工具不去跑，写明查不了；联网搜索不是命令，不进体检。"""

    def runner(self, args, path, timeout):
        self.calls.append(list(args))
        return 0, "map-tool 1.2.3"

    def scan(self, cards):
        for c in cards:
            blocks.save_card(c, self.root)
        self.calls = []
        bindir = Path(self._tmp.name) / "bin"
        bindir.mkdir(exist_ok=True)
        exe = bindir / "map-tool"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        return blocks_env.scan(root=self.root, app_path=str(bindir), terminal_path=None,
                               runner=self.runner, record=False)

    def test_tool_without_version_not_run(self):
        result = self.scan([tool_card(version_args=[])])
        item = next(i for i in result["items"] if i["name"] == "maptool")
        self.assertEqual(item["status"], "有")
        self.assertEqual(item["version"], "")
        self.assertIn("查不了版本", item["note"])
        self.assertNotIn([str(Path(self._tmp.name) / "bin" / "map-tool")], self.calls,
                         "没有查版本命令时不能光跑程序本身")

    def test_tool_with_version_runs_it(self):
        result = self.scan([tool_card()])
        item = next(i for i in result["items"] if i["name"] == "maptool")
        self.assertEqual(item["version"], "map-tool 1.2.3")

    def test_search_block_not_in_scan(self):
        result = self.scan([blocks.search_card("tavily")])
        self.assertNotIn("web-search", [i["name"] for i in result["items"]])


if __name__ == "__main__":
    unittest.main()
