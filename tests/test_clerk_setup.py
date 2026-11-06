"""第一屏：密钥先测试再保存；本机服务只把密钥放内存，不写文件、不回传。Swift 部分用文字检查。"""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fake_model_server import FakeModel  # noqa: E402

from desktop.chat import ChatHub  # noqa: E402
from desktop.server import make_server  # noqa: E402

SWIFT = HERE.parent / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


class ClerkSetupTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeModel()
        self.server = make_server(port=0, token="口令", hub=ChatHub(factories={}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fake.close()

    def post(self, path, body, token="口令"):
        headers = {"Content-Type": "application/json", "X-Chat-Token": urllib.request.quote(token)}
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        return json.loads(urllib.request.urlopen(request, timeout=10).read())

    def form(self, key):
        return {"base_url": self.fake.base, "model": "deepseek/deepseek-flash", "api_key": key}

    def test_good_key_passes_the_test(self):
        self.assertEqual(self.post("/api/clerk/test", self.form("good-key")), {"ok": True})

    def test_wrong_key_gets_a_plain_message_without_the_key(self):
        answer = self.post("/api/clerk/test", self.form("sk-wrong-value"))
        self.assertFalse(answer["ok"])
        self.assertIn("密钥", answer["error"])
        self.assertNotIn("sk-wrong-value", json.dumps(answer, ensure_ascii=False))

    def test_config_stays_in_memory_and_is_never_returned(self):
        answer = self.post("/api/clerk/config", self.form("good-key"))
        self.assertEqual(answer, {"ok": True})
        self.assertEqual(self.server.clerk.api_key, "good-key")
        self.assertEqual(self.server.clerk.model, "deepseek/deepseek-flash")

    def test_clerk_addresses_need_the_window_token(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/clerk/config", self.form("good-key"), token="错的")
        self.assertEqual(caught.exception.code, 403)
        self.assertIsNone(self.server.clerk)

    def test_window_saves_the_key_only_in_the_keychain_after_a_passing_test(self):
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("import Security", swift)
        self.assertIn("kSecClassGenericPassword", swift)
        self.assertIn("NSSecureTextField", swift)
        self.assertLess(swift.index('"/api/clerk/test"'), swift.index("ClerkKey.save(key)"))
        for words in ("UserDefaults.standard.set(key", "SUPERAGENT_CLERK_KEY"):
            self.assertNotIn(words, swift)


if __name__ == "__main__":
    unittest.main()
