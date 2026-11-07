"""行政人员的模型连接：逐字回复、工具调用、各种失败都有大白话，密钥不外泄。用假模型测，不花额度。"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_model_server import FakeModel  # noqa: E402

from desktop.clerk.client import ModelClient, ModelError  # noqa: E402


class ModelClientTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeModel(slow_seconds=2)
        self.client = ModelClient(self.fake.base, "deepseek/deepseek-flash", "good-key", timeout=5)

    def tearDown(self):
        self.fake.close()

    def say(self, text, client=None, tools=None):
        pieces = []
        result = (client or self.client).chat([{"role": "user", "content": text}], on_text=pieces.append, tools=tools)
        return result, pieces

    def failure(self, text, client=None):
        with self.assertRaises(ModelError) as caught:
            self.say(text, client)
        return str(caught.exception)

    def test_reply_arrives_piece_by_piece(self):
        result, pieces = self.say("你好")
        self.assertEqual(result["text"], "好的，收到：你好")
        self.assertEqual("".join(pieces), result["text"])
        self.assertGreater(len(pieces), 3)
        self.assertEqual(result["tokens"], 20)

    def test_request_turns_off_thinking_and_uses_the_model_name(self):
        self.say("你好")
        sent = self.fake.requests[-1]
        self.assertEqual(sent["model"], "deepseek/deepseek-flash")
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["thinking"], {"type": "disabled"})

    def test_tool_call_is_put_back_together(self):
        tools = [{"type": "function", "function": {"name": "get_cards", "parameters": {"type": "object"}}}]
        result, _ = self.say("查工具", tools=tools)
        self.assertEqual(result["tool_calls"], [{"id": "c1", "name": "get_cards", "arguments": '{"a":1}'}])
        self.assertEqual(self.fake.requests[-1]["tools"], tools)

    def test_wrong_key_says_so_and_never_shows_the_key(self):
        client = ModelClient(self.fake.base, "m", "sk-secret-value", timeout=5)
        message = self.failure("你好", client)
        self.assertIn("密钥", message)
        self.assertNotIn("sk-secret-value", message)

    def test_quota_server_error_and_broken_reply_have_plain_messages(self):
        self.assertIn("额度", self.failure("额度"))
        self.assertIn("服务商", self.failure("崩了"))
        self.assertIn("中断", self.failure("半截"))

    def test_broken_reply_keeps_what_already_arrived(self):
        pieces = []
        with self.assertRaises(ModelError) as caught:
            self.client.chat([{"role": "user", "content": "半截"}], on_text=pieces.append)
        self.assertEqual(caught.exception.partial, "".join(pieces))
        self.assertTrue(caught.exception.partial)

    def test_no_network_and_timeout_have_plain_messages(self):
        gone = ModelClient("http://127.0.0.1:9/v1", "m", "good-key", timeout=2)
        self.assertIn("网络", self.failure("你好", gone))
        hurried = ModelClient(self.fake.base, "m", "good-key", timeout=0.3)
        self.assertIn("超时", self.failure("慢", hurried))

    def test_key_with_stray_spaces_or_newline_still_works(self):
        client = ModelClient(self.fake.base, "m", " good-key\n", timeout=5)
        self.assertEqual(self.say("你好", client)[0]["text"], "好的，收到：你好")

    def test_badly_formed_key_gets_a_plain_message_and_is_never_shown(self):
        for key in ("sk-secret\nvalue", "sk-secret 中文", "sk-全角　空格"):
            message = self.failure("你好", ModelClient(self.fake.base, "m", key, timeout=5))
            self.assertIn("密钥", message)
            self.assertNotIn("secret", message)

    def test_redirect_never_carries_the_key_to_another_address(self):
        self.assertIn("服务商", self.failure("跳转"))
        self.assertEqual(self.fake.leaked, [])

    def test_error_inside_the_reply_is_not_treated_as_success(self):
        with self.assertRaises(ModelError) as caught:
            self.say("流错")
        self.assertIn("服务商", str(caught.exception))
        self.assertEqual(caught.exception.partial, "半句")
        self.assertIn("服务商", self.failure("假成功"))

    def test_strange_lines_in_the_reply_are_skipped(self):
        self.assertEqual(self.say("乱码")[0]["text"], "正常")

    def test_finished_reply_counts_even_if_the_end_marker_never_comes(self):
        result, _ = self.say("尾断")
        self.assertEqual(result["text"], "说完了")
        self.assertEqual(result["tool_calls"][0]["name"], "get_cards")

    def test_heartbeats_alone_do_not_wait_forever(self):
        client = ModelClient(self.fake.base, "m", "good-key", timeout=0.4)
        started = time.time()
        with self.assertRaises(ModelError) as caught:
            self.say("心跳", client)
        self.assertIn("超时", str(caught.exception))
        self.assertEqual(caught.exception.partial, "开头")
        self.assertLess(time.time() - started, 1.5)

    def test_empty_fragments_do_not_count_as_progress(self):
        client = ModelClient(self.fake.base, "m", "good-key", timeout=0.4)
        started = time.time()
        with self.assertRaises(ModelError) as caught:
            self.say("空心跳", client)
        self.assertEqual(caught.exception.partial, "开头")
        self.assertLess(time.time() - started, 1.5)

    def test_finished_reply_returns_even_if_heartbeats_continue(self):
        client = ModelClient(self.fake.base, "m", "good-key", timeout=0.4)
        started = time.time()
        self.assertEqual(self.say("尾心跳", client)[0]["text"], "开头")
        self.assertLess(time.time() - started, 1.5)

    def test_strange_tool_call_fields_do_not_lose_the_text(self):
        result, _ = self.say("工具乱")
        self.assertEqual(result["text"], "前半")
        self.assertIn("b", [call["name"] for call in result["tool_calls"]])

    def test_bad_address_gets_a_plain_message(self):
        for base in ("tokenhub.tencentmaas.com/v1", "https://has space/v1", "", "http://[bad/v1"):
            self.assertIn("地址", self.failure("你好", ModelClient(base, "m", "good-key", timeout=2)))

    def test_gateway_that_does_not_speak_http_gets_a_plain_message(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def junk():
            conn, _ = server.accept()
            conn.recv(4096)
            conn.sendall(b"not http at all\r\n\r\n")
            conn.close()

        threading.Thread(target=junk, daemon=True).start()
        base = f"http://127.0.0.1:{server.getsockname()[1]}/v1"
        self.assertIn("看不懂", self.failure("你好", ModelClient(base, "m", "good-key", timeout=3)))
        server.close()

    def test_empty_key_stops_before_sending(self):
        client = ModelClient(self.fake.base, "m", "", timeout=5)
        self.assertIn("密钥", self.failure("你好", client))
        self.assertEqual(self.fake.requests, [])


if __name__ == "__main__":
    unittest.main()
