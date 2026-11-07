"""30 号单：服务商清单，每家密钥各存一把。不花额度。"""
from acceptance_helpers import SWIFT, ServerCase
from desktop.clerk.client import DEFAULT_BASE_URL, DEFAULT_MODEL
from desktop.clerk.providers import PROVIDERS, account_for, find


class ProvidersTest(ServerCase):
    def test_order_and_addresses(self):
        self.assertEqual([p["id"] for p in PROVIDERS], ["tencent", "bailian", "other"])
        self.assertEqual(find("tencent")["base_url"], DEFAULT_BASE_URL)
        self.assertEqual(find("bailian")["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertIsNone(find("没有这家"))

    def test_models_measured_in_the_real_trial_are_offered(self):
        self.assertIn(DEFAULT_MODEL, find("tencent")["models"])
        for name in ("qwen-flash", "qwen-plus", "deepseek-v4.1-flash", "deepseek-v4-pro"):
            self.assertIn(name, find("bailian")["models"])
        self.assertEqual(find("other")["models"], [])

    def test_only_the_measured_providers_turn_thinking_off(self):
        self.assertTrue(find("tencent")["thinking_off"])
        self.assertTrue(find("bailian")["thinking_off"])
        self.assertFalse(find("other")["thinking_off"])

    def test_each_provider_has_its_own_key_slot(self):
        self.assertEqual(account_for("tencent", ""), "tencent")
        self.assertEqual(account_for("bailian", ""), "bailian")
        self.assertEqual(account_for("other", "https://API.Example.com/v1"), "other:api.example.com")
        self.assertNotEqual(account_for("other", "https://a.example.com/v1"), account_for("other", "https://b.example.com/v1"))
        with self.assertRaises(ValueError):
            account_for("other", "不是地址")
        with self.assertRaises(ValueError):
            account_for("没有这家", "")

    def test_page_can_read_the_list_with_the_token_only(self):
        status, _ = self.get("/api/clerk/providers")
        self.assertEqual(status, 403)
        status, answer = self.get("/api/clerk/providers", token="口令")
        self.assertEqual(status, 200)
        self.assertEqual([p["id"] for p in answer["providers"]], ["tencent", "bailian", "other"])


class KeychainTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.swift = SWIFT.read_text(encoding="utf-8")
        self.key = self.swift[self.swift.index("enum ClerkKey"):self.swift.index("enum ChatMode")]

    def test_keys_are_kept_per_provider(self):
        self.assertIn("kSecAttrAccount as String: account", self.key)
        for call in ("static func read(account: String)", "static func save(_ key: String, account: String)",
                     "static func delete(account: String)"):
            self.assertIn(call, self.key)

    def test_the_key_in_use_today_is_moved_not_lost(self):
        self.assertIn('"api-key"', self.key, "旧账号要认得")
        self.assertIn("static func migrateOldKey", self.key)
        self.assertIn("ClerkKey.migrateOldKey", self.swift[:self.swift.index("clerkReady = savedClerkKey() != nil")])
