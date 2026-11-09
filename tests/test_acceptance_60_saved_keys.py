"""60 号单：存过的密钥哪里都不用再填。

积木库窗口打开时，Mac 窗口把钥匙串里的密钥整份交给本机服务，只记在内存里（用户 2026-11-09 定）。
绑定、重新连上、详情检测、提议卡确认：没带密钥就用存的；带了用带的；都没有照旧报错。
钥匙串是唯一的来源：本机服务不自己记页面带来的密钥。密钥不出现在任何返回内容、记录和文件里。
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from collab import blocks
from collab.providers import TENCENT_BASE_URL
from test_acceptance_55_blocks_server import KEY, BlocksCase

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "desktop" / "static"
SOURCES = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent"
SAVED = "sk-saved60secretKEYabcdefghij"
SAVED_B = "sk-saved60bailianKEYabcdefgh"


def read(path):
    return path.read_text(encoding="utf-8")


class SavedKeysBase(BlocksCase):
    """交密钥的写法和收尾查泄漏；不带测试，别的文件可以接着用"""

    def tearDown(self):
        text = json.dumps(self.answers, ensure_ascii=False)
        for secret in (SAVED, SAVED_B):
            self.assertNotIn(secret, text, "存的密钥出现在了返回内容里")
            if self.home.exists():
                for path in self.home.rglob("*"):
                    if path.is_file():
                        self.assertNotIn(secret, path.read_text(encoding="utf-8", errors="replace"), f"写进了 {path}")
        super().tearDown()

    def give(self, keys):
        return self.bpost("/api/blocks/keys", {"keys": keys})


class SavedKeysServer(SavedKeysBase):
    def test_give_keys_and_state_lists_accounts_only(self):
        status, data = self.give({"tencent": SAVED, "bailian": SAVED_B})
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("count"), 2)
        _, state = self.bget("/api/blocks/state")
        self.assertEqual(state["saved_accounts"], ["bailian", "tencent"])

    def test_bad_entries_dropped(self):
        status, data = self.give({"tencent": SAVED, "Tencent": "x", "other:a b": "x", "other:": "x", "bailian": "",
                                  "other:ok.example": "a\nb", "other:long.example": "k" * 501, "api-key": "x"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], ["tencent"])
        self.assertEqual(self.bpost("/api/blocks/keys", {"keys": ["tencent"]})[0], 400)
        self.assertEqual(self.bpost("/api/blocks/keys", {})[0], 400)

    def test_whole_set_replaced(self):
        self.give({"tencent": SAVED, "bailian": SAVED_B})
        self.give({"bailian": SAVED_B})
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], ["bailian"], "钥匙串里删掉的服务里也没了")
        self.give({})
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], [])

    def test_needs_token(self):
        status, _ = self.post("/api/blocks/keys", {"keys": {"tencent": SAVED}}, token="不对")
        self.assertEqual(status, 403)
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], [])

    def test_bind_uses_saved(self):
        self.give({"tencent": SAVED})
        status, data = self.bpost("/api/blocks/bind", {"provider": "tencent", "model": "deepseek/deepseek-flash"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        self.assertEqual(self.fake.seen[0], (TENCENT_BASE_URL, "deepseek/deepseek-flash", SAVED))

    def test_bind_given_key_wins(self):
        self.give({"tencent": SAVED})
        self.bpost("/api/blocks/bind", {"provider": "tencent", "model": "m", "api_key": KEY})
        self.assertEqual(self.fake.seen[0][2], KEY)

    def test_bind_other_by_address(self):
        self.give({"other:api.example.com": SAVED})
        _, data = self.bpost("/api/blocks/bind", {"provider": "other", "model": "m",
                                                  "base_url": "https://api.example.com/v1"})
        self.assertTrue(data["ok"], data)
        self.assertEqual(self.fake.seen[-1][2], SAVED)
        status, _ = self.bpost("/api/blocks/bind", {"provider": "other", "model": "m",
                                                    "base_url": "https://else.example.com/v1"})
        self.assertEqual(status, 400, "别的地址不能用这一把")

    def test_bind_without_any_key(self):
        self.give({"bailian": SAVED_B})
        status, _ = self.bpost("/api/blocks/bind", {"provider": "tencent", "model": "m"})
        self.assertEqual(status, 400)
        self.assertEqual(self.fake.seen, [])

    def test_saved_key_fails_kept(self):
        self.give({"tencent": SAVED})
        self.fake.mode = "bad_key"
        _, data = self.bpost("/api/blocks/bind", {"provider": "tencent", "model": "m"})
        self.assertFalse(data["ok"])
        self.assertIn("密钥", data["reason"])
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], ["tencent"], "没通过也不删存的那把")

    def test_server_does_not_learn_page_keys(self):
        self.bind()  # 带着密钥绑定成功
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], [], "钥匙串是唯一来源")

    def test_connect_uses_saved(self):
        self.bind()
        self.server.blocks_clerk = None
        self.assertEqual(self.bpost("/api/blocks/connect", {})[0], 400)
        self.give({"tencent": SAVED})
        status, data = self.bpost("/api/blocks/connect", {})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.bget("/api/blocks/state")[1]["connected"])
        self.assertEqual(self.fake.seen[-1][2], SAVED)

    def test_connect_check_with_saved(self):
        self.bind()
        self.server.blocks_clerk = None
        self.give({"tencent": SAVED})
        self.fake.mode = "bad_key"
        _, data = self.bpost("/api/blocks/connect", {"check": True})
        self.assertFalse(data["ok"])
        self.assertFalse(self.bget("/api/blocks/state")[1]["connected"])

    def test_check_api_uses_saved(self):
        blocks.save_card(self.api_card())
        self.give({"tencent": SAVED})
        self.assertEqual(self.bpost("/api/blocks/check", {"id": "tongyi"})[0], 400, "存的是别家")
        self.give({"bailian": SAVED_B})
        status, data = self.bpost("/api/blocks/check", {"id": "tongyi"})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.job(data["job"])["result"]["ok"])
        self.assertEqual(self.fake.seen[-1][2], SAVED_B)

    def test_proposal_uses_saved(self):
        self.give({"bailian": SAVED_B})
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus", "name": "通义"})
        status, data = self.confirm(cid, pid)
        self.assertEqual(status, 200, data)
        self.assertTrue(self.job(data["job"])["result"]["ok"])
        self.assertEqual(self.fake.seen[-1][2], SAVED_B)

    def test_proposal_without_saved_still_needs_key(self):
        self.give({"tencent": SAVED})
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus", "name": "通义"})
        self.assertEqual(self.confirm(cid, pid)[0], 400)

    def test_keys_never_written(self):
        self.give({"tencent": SAVED, "bailian": SAVED_B})
        self.bpost("/api/blocks/bind", {"provider": "tencent", "model": "m"})
        blocks.save_card(self.api_card())
        _, data = self.bpost("/api/blocks/check", {"id": "tongyi"})
        self.job(data["job"])
        # tearDown 查返回内容和资料夹里的每个文件


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageRules(unittest.TestCase):
    def test_rules(self):
        proc = subprocess.run(["node", str(HERE / "blocks_saved_keys_check.js")], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class PageWiring(unittest.TestCase):
    def test_blocks_page(self):
        html = read(STATIC / "blocks.html")
        self.assertEqual(html.count('class="saved-key"'), 2, "绑定页、详情页各一行")
        self.assertEqual(html.count("已保存的密钥"), 2)
        self.assertEqual(html.count("换一把"), 2)
        js = read(STATIC / "blocks.js")
        self.assertIn("window.blocksKeysChanged", js)
        self.assertIn("saved_accounts", js)
        self.assertIn("存的这把没通过，可以换一把", js)
        self.assertIn(".saved-key", read(STATIC / "blocks.css"))

    def test_chat_card(self):
        js = read(STATIC / "chat.js")
        for words in ("已保存的密钥", "换一把", "存的这把没通过，可以换一把", "/api/blocks/state", "saved_accounts",
                      "keyAccount"):
            self.assertIn(words, js)


class MacWiring(unittest.TestCase):
    def setUp(self):
        self.main = read(SOURCES / "main.swift")
        self.win = read(SOURCES / "BlocksWindow.swift")

    def test_keychain_accounts_and_change_notice(self):
        from test_acceptance_59_blocks_window import body_of
        accounts = body_of(self.main, "static func accounts(")
        self.assertIn("kSecMatchLimitAll", accounts)
        self.assertIn("kSecReturnAttributes", accounts)
        self.assertNotIn("kSecReturnData", accounts, "列账号不取密钥")
        self.assertIn("clerk-key-dir", accounts + body_of(self.main, "static func testFile("), "临时密钥文件夹也能列")
        for fn in ("static func save(", "static func delete("):
            self.assertRegex(body_of(self.main, fn), r"post\(name: ClerkKey\.changed", f"{fn} 之后要发通知")
        self.assertIn('Notification.Name("SuperAgentClerkKeysChanged")', self.main)

    def test_window_gives_keys(self):
        from test_acceptance_59_blocks_window import body_of
        sync = body_of(self.win, "func syncKeys(")
        for words in ("/api/blocks/keys", "X-Chat-Token", "ClerkKey.accounts()", "isValidAccount", "ClerkKey.read("):
            self.assertIn(words, sync)
        self.assertNotIn("print(", sync)
        self.assertIn("syncKeys", body_of(self.win, "func load("), "加载前先交密钥")
        self.assertIn("ClerkKey.changed", self.win, "钥匙串一变就再交一次")
        self.assertIn("blocksKeysChanged", self.win, "交完叫页面重新取状态")


if __name__ == "__main__":
    unittest.main()
