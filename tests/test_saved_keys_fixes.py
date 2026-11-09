"""60 号单验收和复查后的修改：存过的密钥。

存的密钥只在“账号和地址对得上”时才用：卡片或行政设置被改过、地址和账号对不上时当没存，
免得点一下“检测”就把某家的密钥发到别处。交密钥时的各种坏格式都丢掉。
"""
import json
import unittest

from collab import blocks
from collab.config import _config_path
from test_acceptance_60_saved_keys import SAVED, SavedKeysBase as FixCase


class SavedKeyOnlyForMatchingAddress(FixCase):
    def test_check_card_with_wrong_address(self):
        card = {"id": "evil", "kind": "llm_api", "name": "坏卡", "provider": "other",
                "base_url": "https://evil.example/v1", "model": "m", "key_account": "tencent", "source": "clerk"}
        blocks.save_card(card)
        self.give({"tencent": SAVED})
        status, _ = self.bpost("/api/blocks/check", {"id": "evil"})
        self.assertEqual(status, 400, "账号和地址对不上：不能用存的")
        self.assertEqual(self.fake.seen, [])

    def test_check_known_provider_with_other_address(self):
        card = {"id": "evil2", "kind": "llm_api", "name": "坏卡", "provider": "tencent",
                "base_url": "https://evil.example/v1", "model": "m", "key_account": "tencent", "source": "clerk"}
        blocks.save_card(card)
        self.give({"tencent": SAVED})
        self.assertEqual(self.bpost("/api/blocks/check", {"id": "evil2"})[0], 400, "认得的服务商只能发到它自己的地址")
        self.assertEqual(self.fake.seen, [])

    def test_other_provider_matching_host_ok(self):
        card = {"id": "mine", "kind": "llm_api", "name": "自家", "provider": "other",
                "base_url": "https://api.example.com/v1", "model": "m", "key_account": "other:api.example.com",
                "source": "clerk"}
        blocks.save_card(card)
        self.give({"other:api.example.com": SAVED})
        status, data = self.bpost("/api/blocks/check", {"id": "mine"})
        self.assertEqual(status, 200, data)
        self.job(data["job"])
        self.assertEqual(self.fake.seen[-1][2], SAVED)

    def test_connect_with_tampered_setting(self):
        self.bind()
        self.server.blocks_clerk = None
        path = _config_path(str(self.home))
        data = json.loads(path.read_text(encoding="utf-8"))
        data["clerk"]["base_url"] = "https://evil.example/v1"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.give({"tencent": SAVED})
        self.assertEqual(self.bpost("/api/blocks/connect", {})[0], 400, "行政设置的地址被改过：不能用存的")
        self.assertNotIn(SAVED, [s[2] for s in self.fake.seen])

    def test_given_key_still_works_for_any_card(self):
        """手填的密钥照旧：用户自己贴的，自己负责（和 60 号单以前一样）"""
        card = {"id": "evil3", "kind": "llm_api", "name": "坏卡", "provider": "other",
                "base_url": "https://evil.example/v1", "model": "m", "key_account": "tencent", "source": "clerk"}
        blocks.save_card(card)
        self.give({"tencent": SAVED})
        status, data = self.bpost("/api/blocks/check", {"id": "evil3", "api_key": "sk-typed-by-user"})
        self.assertEqual(status, 200, data)
        self.job(data["job"])
        self.assertEqual(self.fake.seen[-1][2], "sk-typed-by-user")


class BadKeysDropped(FixCase):
    def test_more_bad_entries(self):
        self.give({"tencent": "a\rb", "bailian": "   ", "other:x.example": 5, "other:y.example": None})
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], [])
        status, _ = self.bpost("/api/blocks/keys", {"keys": {"tencent": SAVED, "5": "x"}})
        self.assertEqual(status, 200)
        self.assertEqual(self.bget("/api/blocks/state")[1]["saved_accounts"], ["tencent"])


class MacFixes(unittest.TestCase):
    def test_notify_only_after_success_and_follow_keychain(self):
        from pathlib import Path
        from test_acceptance_59_blocks_window import BLOCKS, MAIN, body_of
        main, win = MAIN.read_text(encoding="utf-8"), BLOCKS.read_text(encoding="utf-8")
        save = body_of(main, "static func save(")
        self.assertNotIn("defer", save, "存失败不通知")
        self.assertIn("if ok { NotificationCenter.default.post(name: ClerkKey.changed", save)
        changed = body_of(win, "func keysChanged(")
        self.assertIn("guard gaveKeys", changed, "积木库开过就跟着钥匙串变，关着也交：删掉的马上没了")
        self.assertNotIn("isOpen", changed.split("syncKeys")[0])
        self.assertIn("gaveKeys = true", body_of(win, "func syncKeys("))


if __name__ == "__main__":
    unittest.main()
