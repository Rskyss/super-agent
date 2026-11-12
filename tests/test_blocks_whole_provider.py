"""大模型 API 按整家接（docs/积木库/方案.md 第 5 节，2026-11-10 用户改）：

1. 接入卡没有“模型”一栏，一家只占一块。
2. 检测：用密钥拿这家的模型名单，再自动挑一个通用对话模型试通，不让用户选。
3. 基础层详情显示“这家的模型：N 个”和名单。
4. 腾讯云、阿里百炼的旧块（带模型的）自动变成整家一块。
5. 管家绑定自己的模型不变。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from collab import blocks, blocks_check
from collab.blocks import BlockError
from desktop.clerk import blocks_tools
from desktop.clerk.client import ModelError
from test_acceptance_55_blocks_server import KEY, BlocksCase

ROOT = Path(__file__).resolve().parent.parent
BAILIAN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TENCENT = "https://tokenhub.tencentmaas.com/v1"


def card(provider="bailian", **over):
    base = {"id": provider, "kind": "llm_api", "name": "阿里百炼", "provider": provider,
            "base_url": BAILIAN if provider == "bailian" else TENCENT, "key_account": provider}
    base.update(over)
    return base


class Pinger:
    """假的模型连接：记下试了哪些模型；replies 按模型名给结果（"ok" / "400" / "quota" / "empty" / "key"）。"""

    def __init__(self, replies=None, default="ok"):
        self.replies, self.default, self.tried = replies or {}, default, []

    def factory(self, base_url, model, api_key):
        owner = self

        class Client:
            def chat(self, messages, on_text=None, tools=None):
                owner.tried.append(model)
                how = owner.replies.get(model, owner.default)
                if how == "400":
                    err = ModelError("模型服务商暂时出错了（代码 400），请稍后再试。")
                    err.detail = getattr(owner, "detail", "")
                    raise err
                if how == "quota":
                    raise ModelError("模型额度用完了，或请求太频繁。请稍后再试，或到服务商那里检查余额。")
                if how == "key":
                    raise ModelError("模型密钥不对或已失效")
                return {"text": "" if how == "empty" else "收到", "tool_calls": [], "tokens": 1}
        return Client()


class CardTest(unittest.TestCase):
    def test_model_is_optional(self):
        clean = blocks.validate_card(card())
        self.assertNotIn("model", clean)

    def test_bad_model_still_refused(self):
        with self.assertRaises(BlockError):
            blocks.validate_card(card(model="-rf"))


class CheckWholeTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def check(self, pinger, names, the_card=None, **kw):
        def fetch(base_url, api_key):
            if isinstance(names, Exception):
                raise names
            return list(names)
        return blocks_check.check_api(the_card or card(), "k-0123456789", root=self.home, whole=True,
                                      client_factory=pinger.factory, models_fetcher=fetch, **kw)

    def test_picks_a_preset_model_and_keeps_the_list(self):
        p = Pinger()
        names = ["text-embedding-v4", "wan-video", "qwen-plus", "qwen-max"]
        result = self.check(p, names)
        self.assertTrue(result["ok"], result)
        self.assertEqual(p.tried, ["qwen-plus"], "先试这家预置的通用模型，一次通了就不再试")
        self.assertEqual(result["models"], names)
        self.assertEqual(result["model"], "qwen-plus")
        self.assertEqual(blocks.last_record("bailian", self.home, type="check")["models"], names)

    def test_tries_next_when_one_refuses(self):
        p = Pinger({"qwen-plus": "400", "qwen-flash": "empty"})
        result = self.check(p, ["qwen-plus", "qwen-flash", "qwen-max"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(p.tried, ["qwen-flash", "qwen-plus", "qwen-max"])
        self.assertEqual(result["model"], "qwen-max")

    def test_non_chat_models_go_last(self):
        p = Pinger()
        result = self.check(p, ["bge-embedding", "sd-image-xl", "speech-tts", "chat-x"], card("tencent"))
        self.assertTrue(result["ok"])
        self.assertEqual(p.tried, ["chat-x"])

    def test_list_alone_counts_as_connected(self):
        """拿到名单就算接上（用户 2026-11-11 选 A）：试的都不通只标黄，不拦；平台的原话要写出来"""
        p = Pinger(default="400")
        p.detail = "The model does not exist or you do not have access to it."
        result = self.check(p, ["m%d" % i for i in range(20)], card("tencent"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "listed")
        self.assertEqual(len(p.tried), blocks_check.PROBE_TRIES)
        self.assertIn("20 个", result["reason"])
        self.assertIn("do not have access", result["reason"])
        self.assertEqual(len(result["models"]), 20)

    def test_platform_words_are_masked_and_short(self):
        p = Pinger(default="400")
        p.detail = "bad key k-0123456789 " + "x" * 900 + "\nsecond line"
        result = self.check(p, ["a", "b"], card("tencent"))
        self.assertNotIn("k-0123456789", result["reason"])
        self.assertNotIn("\n", result["reason"])
        self.assertLess(len(result["reason"]), 400)

    def test_volcano_like_list_tries_its_own_chat_models(self):
        """真实的火山引擎名单（2026-11-11）：先试豆包自己的聊天模型，不试画图、视频、3D、向量、翻译"""
        names = ["kimi-k3-1-pro-261024", "deepseek-v4-1-flash-economy-261017", "deepseek-v4-1-flash-261017",
                 "doubao-seedream-5-0-flash-260915", "doubao-seed-2-1-pro-260915", "glm-5-3-flash-260828",
                 "doubao-seed-2-1-lite-260915", "doubao-seedance-2-5-260628", "doubao-seed-evolving",
                 "doubao-seed3d-2-0-260328", "hitem3d-2-0-251223", "doubao-embedding-vision-251215",
                 "doubao-seed-translation-250915", "doubao-smart-router-250928", "qwen3-14b-20250429",
                 "doubao-seedance-1-0-lite-t2v-250428", "doubao-embedding-text-240515"]
        other = {"id": "other-ark", "kind": "llm_api", "name": "火山引擎", "provider": "other",
                 "base_url": "https://ark.cn-beijing.volces.com/api/v3", "key_account": "other:ark.cn-beijing.volces.com"}
        order = blocks_check._probe_order(other, names)
        self.assertEqual(order[:2], ["doubao-seed-2-1-pro-260915", "doubao-seed-2-1-lite-260915"])
        for bad in ("seedream", "seedance", "seed3d", "3d", "embedding", "translation"):
            self.assertFalse([n for n in order if bad in n], bad)
        self.assertTrue([n for n in order if not n.startswith("doubao")], "也要留位置试别家的模型")

    def test_quota_counts_as_connected_and_stops(self):
        p = Pinger(default="quota")
        result = self.check(p, ["qwen-plus", "qwen-max"])
        self.assertEqual(result["status"], "quota")
        self.assertEqual(len(p.tried), 1)
        self.assertEqual(result["models"], ["qwen-plus", "qwen-max"])

    def test_bad_key_stops_at_the_list(self):
        p = Pinger()
        result = self.check(p, BlockError("密钥不对或已失效，问不到这家的模型名单"))
        self.assertFalse(result["ok"])
        self.assertIn("密钥", result["reason"])
        self.assertEqual(p.tried, [])

    def test_bad_key_on_ping_stops(self):
        p = Pinger(default="key")
        result = self.check(p, ["qwen-plus", "qwen-max"])
        self.assertFalse(result["ok"])
        self.assertIn("密钥", result["reason"])
        self.assertEqual(len(p.tried), 1)

    def test_no_list_asks_for_a_model_name(self):
        p = Pinger()
        result = self.check(p, BlockError("这家没给模型名单（返回代码 404）"), card("tencent"))
        self.assertFalse(result["ok"])
        self.assertIn("模型名单", result["reason"])
        self.assertIn("模型名", result["how"])
        self.assertEqual(p.tried, [])

    def test_no_list_but_card_names_a_model(self):
        other = {"id": "other-api-example-com", "kind": "llm_api", "name": "例子", "provider": "other",
                 "base_url": "https://api.example.com/v1", "model": "ex-chat", "key_account": "other:api.example.com"}
        p = Pinger()
        result = self.check(p, BlockError("这家没给模型名单（返回代码 404）"), other)
        self.assertTrue(result["ok"], result)
        self.assertEqual(p.tried, ["ex-chat"])
        self.assertEqual(result["models"], [])

    def test_card_model_is_tried_first(self):
        other = {"id": "other-api-example-com", "kind": "llm_api", "name": "例子", "provider": "other",
                 "base_url": "https://api.example.com/v1", "model": "ex-chat", "key_account": "other:api.example.com"}
        p = Pinger()
        self.check(p, ["a", "ex-chat", "b"], other)
        self.assertEqual(p.tried, ["ex-chat"])

    def test_key_never_in_result(self):
        p = Pinger(default="400")
        result = self.check(p, ["a", "b"])
        self.assertNotIn("k-0123456789", json.dumps(result, ensure_ascii=False))

    def test_single_model_check_unchanged(self):
        """管家绑定自己的模型：照旧只试这一个，不问名单"""
        p = Pinger()

        def boom(base_url, api_key):
            raise AssertionError("不该问名单")
        result = blocks_check.check_api(card(model="qwen-plus"), "k-0123456789", record=False,
                                        client_factory=p.factory, models_fetcher=boom)
        self.assertTrue(result["ok"])
        self.assertEqual(p.tried, ["qwen-plus"])
        self.assertNotIn("models", result)


class SecondReviewTest(unittest.TestCase):
    """Codex 第二轮复查（2026-11-11）的 11 条"""

    def setUp(self):
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_check(self, detail, names=("a", "b"), key="k-0123456789"):
        p = Pinger(default="400")
        p.detail = detail
        return blocks_check.check_api(card("tencent"), key, root=self.home, whole=True,
                                      client_factory=p.factory, models_fetcher=lambda b, k: list(names))

    def test_1_secret_at_the_cut_never_leaks(self):
        uuid_key = "12345678-90ab-cdef-1234-567890abcdef"
        for tail in (uuid_key, "someone@example.com", "sk-" + "a" * 40):
            with self.subTest(tail=tail):
                result = self.run_check("x" * 150 + " " + tail, key=uuid_key)
                text = json.dumps(result, ensure_ascii=False) + json.dumps(blocks.records("tencent", self.home),
                                                                           ensure_ascii=False)
                for piece in ("12345678", "someone@", "sk-aaaa"):
                    self.assertNotIn(piece, text)

    def test_2_odd_error_body_keeps_401(self):
        import io
        import urllib.error
        from unittest import mock
        from desktop.clerk import client
        for body in ("[]", "null", "3", '"oops"', '{"error": 5}', '{"error": null, "message": 7}'):
            with self.subTest(body=body):
                e = urllib.error.HTTPError("https://x/v1/chat/completions", 401, "no", {}, io.BytesIO(body.encode()))
                with mock.patch.object(client._OPENER, "open", side_effect=e):
                    c = client.ModelClient(base_url="https://x/v1", model="m", api_key="k-0123456789abcdef", timeout=5)
                    with self.assertRaises(client.ModelError) as got:
                        c.chat([{"role": "user", "content": "hi"}])
                self.assertEqual(got.exception.code, 401)
                self.assertIn("密钥", str(got.exception))

    def test_3_own_family_before_resold_preset(self):
        order = blocks_check._probe_order(card("tencent"), ["hunyuan-lite", "hunyuan-pro", "deepseek/deepseek-flash"])
        self.assertEqual(order[:2], ["hunyuan-lite", "hunyuan-pro"])
        self.assertIn("deepseek/deepseek-flash", order)

    def test_4_filter_by_whole_words(self):
        order = blocks_check._probe_order(card("other", base_url="https://x/v1"),
                                          ["swan-chat", "acme-image-chat", "acme-3d-reasoner", "acme-image-gen"])
        self.assertIn("swan-chat", order)
        self.assertIn("acme-image-chat", order)
        self.assertNotIn("acme-image-gen", order)
        qwen = blocks_check._probe_order(card("bailian"), ["qwen-image-plus", "wan2.1-t2v", "qwen-vl-max", "qwen3-coder-plus"])
        self.assertNotIn("qwen-image-plus", qwen)
        self.assertNotIn("wan2.1-t2v", qwen)
        self.assertIn("qwen-vl-max", qwen)
        drawn = blocks_check._probe_order(card("other", base_url="https://x/v1", model="doubao-seedream-5-0"),
                                          ["doubao-seedream-5-0", "doubao-seed-2-1-pro"])
        self.assertEqual(drawn[0], "doubao-seed-2-1-pro")
        self.assertNotIn("doubao-seedream-5-0", drawn)
        alone = blocks_check._probe_order(card("other", base_url="https://x/v1", model="only-one"), [])
        self.assertEqual(alone, ["only-one"])

    def test_5_list_forbidden_is_not_bad_key(self):
        import io
        import urllib.error
        from unittest import mock
        e = urllib.error.HTTPError("https://x/v1/models", 403, "no", {}, io.BytesIO(b"{}"))
        with mock.patch.object(blocks_check, "_open_models", side_effect=e):
            with self.assertRaises(BlockError) as got:
                blocks_check.fetch_models("https://x/v1", "k-0123456789")
        self.assertNotIn("密钥不对", str(got.exception))
        p = Pinger()

        def fetch(b, k):
            raise got.exception
        result = blocks_check.check_api(card("other", base_url="https://x/v1", model="chat-1"), "k-0123456789",
                                        root=self.home, whole=True, client_factory=p.factory, models_fetcher=fetch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(p.tried, ["chat-1"])

    def test_9_no_list_bad_key_says_key(self):
        p = Pinger(default="key")

        def fetch(b, k):
            raise BlockError("这家没给模型名单（返回代码 404）")
        result = blocks_check.check_api(card("other", base_url="https://x/v1", model="chat-1"), "k-0123456789",
                                        root=self.home, whole=True, client_factory=p.factory, models_fetcher=fetch)
        self.assertFalse(result["ok"])
        self.assertIn("密钥", result["how"])

    def test_10_local_trouble_is_not_platform_words(self):
        p = Pinger(default="400")
        result = blocks_check.check_api(card("tencent"), "k-0123456789", root=self.home, whole=True,
                                        client_factory=p.factory, models_fetcher=lambda b, k: ["a"])
        self.assertEqual(result["status"], "listed")
        self.assertNotIn("平台说", result["reason"])
        p.detail = "no access"
        result = self.run_check("no access")
        self.assertIn("平台说：no access", result["reason"])

    def test_11_capped_list_wording(self):
        names = ["m%d" % i for i in range(blocks_check.MODELS_LIMIT)]
        result = blocks_check.check_api(card("tencent"), "k-0123456789", root=self.home, whole=True,
                                        client_factory=Pinger().factory, models_fetcher=lambda b, k: names)
        self.assertIn("可能没拿全", result["reason"])
        text = blocks_tools.run(str(self.home), "list_blocks", {})
        self.assertNotIn(f"这家有 {blocks_check.MODELS_LIMIT} 个", text)


class ThirdReviewTest(unittest.TestCase):
    """Codex 第三轮复查（2026-11-11）"""

    def setUp(self):
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_1_read_limit_never_leaves_half_a_secret(self):
        import io
        import urllib.error
        from desktop.clerk import client
        for tail in ("12345678-90ab-cdef-1234-567890abcdef", "reviewer@example.com", "sk-" + "a" * 40):
            with self.subTest(tail=tail):
                body = ('{"error": ' + " " * 3970 + tail + '"}').encode()
                e = urllib.error.HTTPError("https://x", 400, "no", {}, io.BytesIO(body))
                words = client._platform_words(e)
                self.assertNotIn(tail[:8], words)

    def test_2_known_provider_own_family(self):
        order = blocks_check._probe_order(card("tencent"), ["deepseek/deepseek-flash", "deepseek-v4-pro", "hunyuan-lite"])
        self.assertEqual(order[0], "hunyuan-lite")

    def test_3_never_tries_nothing(self):
        p = Pinger(default="400")
        result = blocks_check.check_api(card("other", base_url="https://x/v1"), "k-0123456789", root=self.home,
                                        whole=True, client_factory=p.factory, models_fetcher=lambda b, k: ["acme-3d-reasoner"])
        self.assertEqual(p.tried, ["acme-3d-reasoner"], "过滤完一个不剩：名单里的照样试，不能说试了 0 个")
        self.assertNotIn("试了 0 个", result["reason"])

    def test_5_email_names_dropped(self):
        from unittest import mock
        import io
        body = json.dumps({"data": [{"id": "reviewer@example.com"}, {"id": "chat-1"}]}).encode()

        class R(io.BytesIO):
            pass
        with mock.patch.object(blocks_check, "_open_models", return_value=R(body)):
            names = blocks_check.fetch_models("https://x/v1", "k-0123456789")
        self.assertEqual(names, ["chat-1"])

    def test_6_slow_list_is_closed_and_capped(self):
        import threading
        from unittest import mock
        gate = threading.Event()
        closed = []

        class Slow:
            def read(self, n):
                gate.wait(5)
                return b""

            def close(self):
                closed.append(1)
                gate.set()
        with mock.patch.object(blocks_check, "_open_models", side_effect=lambda *a, **k: Slow()), \
                mock.patch.object(blocks_check, "MODELS_TIMEOUT", 0.05):
            with self.assertRaises(BlockError):
                blocks_check.fetch_models("https://x/v1", "k-0123456789")
        self.assertTrue(closed, "到点要把连接关掉，后台那次下载才会停")

    def test_7_bind_403_keeps_real_reason(self):
        class P(Pinger):
            def factory(self, base_url, model, api_key):
                class C:
                    def chat(self, messages, on_text=None, tools=None):
                        err = ModelError("模型密钥不对或已失效，请到设置里重新填写。")
                        err.code, err.detail = 403, "model not activated"
                        raise err
                return C()
        result = blocks_check.check_api(card("tencent", model="hy4"), "k-0123456789", need_tools=True, record=False,
                                        client_factory=P().factory)
        self.assertFalse(result["ok"])
        self.assertNotIn("400", result["reason"])
        self.assertIn("403", result["reason"])
        self.assertIn("model not activated", result["reason"])


class PlatformWordsTest(unittest.TestCase):
    """平台拒绝时的原话要留下来（火山引擎 2026-11-11：之前只说“都没回话”，不知道为什么）"""

    def err(self, code, body):
        import io
        import urllib.error
        return urllib.error.HTTPError("https://x/chat/completions", code, "no", {}, io.BytesIO(body.encode("utf-8")))

    def test_reads_common_shapes(self):
        from desktop.clerk import client
        e = self.err(404, json.dumps({"error": {"code": "InvalidEndpointOrModel.NotFound",
                                                "message": "The model or endpoint does not exist or you do not have access to it."}}))
        self.assertIn("do not have access", client._platform_words(e))
        self.assertEqual(client._platform_words(self.err(400, '{"message": "bad  model\n name"}')), "bad model name")
        self.assertEqual(client._platform_words(self.err(500, "<html>oops</html>")), "<html>oops</html>")
        # 这里不截短（先打码再截短，截短在 blocks_check 里做）；最多只读 4000 个字
        self.assertLessEqual(len(client._platform_words(self.err(500, "x" * 9000))), 4000)

    def test_client_keeps_code_and_words(self):
        from unittest import mock
        from desktop.clerk import client
        e = self.err(403, '{"error": {"message": "model not activated"}}')
        with mock.patch.object(client._OPENER, "open", side_effect=e):
            c = client.ModelClient(base_url="https://x/v1", model="m", api_key="k-0123456789abcdef", timeout=5)
            with self.assertRaises(client.ModelError) as got:
                c.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(got.exception.code, 403)
        self.assertEqual(got.exception.detail, "model not activated")

    def test_403_on_chat_is_not_bad_key(self):
        """403 常是“这个模型没开通”：换下一个试，最后算已接上，不说密钥坏了"""
        class P(Pinger):
            def factory(self, base_url, model, api_key):
                owner = self

                class C:
                    def chat(self, messages, on_text=None, tools=None):
                        owner.tried.append(model)
                        err = ModelError("模型密钥不对或已失效，请到设置里重新填写。")
                        err.code, err.detail = 403, "model not activated"
                        raise err
                return C()
        import tempfile
        p = P()
        result = blocks_check.check_api(card("tencent"), "k-0123456789", root=tempfile.mkdtemp(), whole=True,
                                        client_factory=p.factory, models_fetcher=lambda b, k: ["a", "b", "c"])
        self.assertEqual(result["status"], "listed")
        self.assertEqual(len(p.tried), 3)
        self.assertIn("model not activated", result["reason"])


class MigrateTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def old(self, block_id, provider, model, name):
        blocks.save_card(card(provider, id=block_id, name=name, model=model), self.home)

    def test_old_block_becomes_whole_provider(self):
        self.old("bailian-qwen-plus", "bailian", "qwen-plus", "阿里百炼 通义千问")
        blocks.add_record("bailian-qwen-plus", {"type": "check", "ok": True, "status": "ok", "reason": "通了",
                                                "how": "", "seconds": 1.2}, self.home)
        self.assertEqual(blocks.migrate_api_cards(self.home), 1)
        cards = blocks.list_cards(self.home)
        self.assertEqual([c["id"] for c in cards], ["bailian"])
        self.assertEqual(cards[0]["name"], "阿里百炼")
        self.assertNotIn("model", cards[0])
        self.assertEqual(cards[0]["key_account"], "bailian")
        last = blocks.last_record("bailian", self.home, type="check")
        self.assertTrue(last["ok"], "上次检测结果带过来")
        self.assertEqual(blocks.migrate_api_cards(self.home), 0, "再跑一次什么都不动")

    def test_same_provider_merges_and_keeps_the_good_result(self):
        self.old("tencent-hy4", "tencent", "hy4", "腾讯云混元 hy4")
        self.old("tencent-deepseek", "tencent", "deepseek/deepseek-flash", "腾讯云 DeepSeek")
        blocks.add_record("tencent-deepseek", {"type": "check", "ok": False, "status": "fail", "reason": "模型名不对",
                                               "how": "", "seconds": 1}, self.home)
        blocks.add_record("tencent-hy4", {"type": "check", "ok": True, "status": "ok", "reason": "通了", "how": "",
                                          "seconds": 1}, self.home)
        blocks.migrate_api_cards(self.home)
        cards = blocks.list_cards(self.home)
        self.assertEqual([(c["id"], c["name"]) for c in cards], [("tencent", "腾讯云")])
        self.assertTrue(blocks.last_record("tencent", self.home, type="check")["ok"])

    def test_others_untouched(self):
        other = {"id": "ex", "kind": "llm_api", "name": "例子", "provider": "other",
                 "base_url": "https://api.example.com/v1", "model": "ex-chat", "key_account": "other:api.example.com"}
        blocks.save_card(other, self.home)
        blocks.save_card(blocks.builtin_card("codex"), self.home)
        blocks.save_card(card("bailian"), self.home)
        self.assertEqual(blocks.migrate_api_cards(self.home), 0)
        self.assertEqual(sorted(c["id"] for c in blocks.list_cards(self.home)), ["bailian", "codex", "ex"])

    def test_broken_or_foreign_target_keeps_old_block(self):
        """占着“bailian”这个编号的卡坏了、或者地址被手改过：好的旧块不能删（复查第 3 条）"""
        self.old("bailian-qwen-plus", "bailian", "qwen-plus", "通义")
        path = blocks._card_path("bailian", self.home)
        path.write_text("{坏的", encoding="utf-8")
        self.assertEqual(blocks.migrate_api_cards(self.home), 0)
        self.assertTrue(blocks._card_path("bailian-qwen-plus", self.home).is_file())
        path.write_text(json.dumps(card("bailian", base_url="https://evil.example.com/v1")), encoding="utf-8")
        self.assertEqual(blocks.migrate_api_cards(self.home), 0)
        self.assertTrue(blocks._card_path("bailian-qwen-plus", self.home).is_file())

    def test_record_carried_over_on_retry(self):
        """上次新块写好了、记录还没带过来就断了：这次补上记录再删旧块"""
        self.old("bailian-qwen-plus", "bailian", "qwen-plus", "通义")
        blocks.add_record("bailian-qwen-plus", {"type": "check", "ok": True, "status": "ok", "reason": "通了",
                                                "how": "", "seconds": 1}, self.home)
        blocks.save_card(card("bailian"), self.home)
        self.assertEqual(blocks.migrate_api_cards(self.home), 1)
        self.assertTrue(blocks.last_record("bailian", self.home, type="check")["ok"])

    def test_6_newer_old_record_wins(self):
        """整家块已有一条旧的失败记录，旧编号的块刚检测过：带过来的是新的那条"""
        blocks.save_card(card("bailian"), self.home)
        blocks.add_record("bailian", {"type": "check", "ok": False, "status": "fail", "reason": "旧的", "how": ""}, self.home)
        self.old("bailian-qwen-plus", "bailian", "qwen-plus", "通义")
        path = blocks._record_path("bailian-qwen-plus", self.home)
        path.write_text(json.dumps({"type": "check", "ok": False, "status": "listed", "reason": "新的",
                                    "how": "", "models": ["qwen-plus"], "time": "2999-01-01T00:00:00"}) + "\n",
                        encoding="utf-8")
        blocks.migrate_api_cards(self.home)
        last = blocks.last_record("bailian", self.home, type="check")
        self.assertEqual(last["reason"], "新的")
        self.assertEqual(last["models"], ["qwen-plus"])

    def test_existing_whole_block_wins(self):
        blocks.save_card(card("bailian", name="阿里百炼"), self.home)
        self.old("bailian-qwen-plus", "bailian", "qwen-plus", "通义")
        blocks.migrate_api_cards(self.home)
        self.assertEqual([c["id"] for c in blocks.list_cards(self.home)], ["bailian"])


class ServerTest(BlocksCase):
    def setUp(self):
        super().setUp()
        self.names = ["qwen-plus", "qwen-max", "text-embedding-v4"]
        self.asked = []

        def fetch(base_url, api_key):
            self.asked.append(base_url)
            return list(self.names)
        self.server.blocks_models_fetcher = fetch

    def test_proposal_has_no_model(self):
        cid, pid = self.proposal("propose_connect_api", {"provider": "tencent", "name": "腾讯云混元 hy4",
                                                         "model": "hy4"})
        events = self.server.desk.events(str(self.home), cid) if hasattr(self.server.desk, "events") else None
        from desktop.clerk import tasks as clerk_tasks
        data = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertEqual(data["block"]["id"], "tencent")
        self.assertEqual(data["block"]["name"], "腾讯云")
        self.assertEqual(data["name"], "腾讯云")
        self.assertNotIn("model", data["block"])
        self.assertNotIn("model", data)
        del events

    def test_other_gets_one_block_per_site(self):
        cid, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "DeepSeek",
                                                         "base_url": "https://api.deepseek.com/v1"})
        from desktop.clerk import tasks as clerk_tasks
        block = clerk_tasks.load_proposal(str(self.home), pid)["block"]
        self.assertEqual(block["id"], "other-api-deepseek-com")
        self.assertEqual(block["name"], "DeepSeek")
        self.assertNotIn("model", block)

    def test_connect_then_detail_shows_models(self):
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "name": "阿里百炼"})
        _, data = self.confirm(cid, pid, api_key=KEY)
        result = self.job(data["job"])["result"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.asked, [BAILIAN])
        self.assertEqual(self.fake.seen[-1][1], "qwen-plus")
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 200)
        listed = self.bget("/api/blocks/list")[1]["blocks"]
        self.assertEqual([(b["id"], b["name"], b["models"]) for b in listed], [("bailian", "阿里百炼", 3)])
        detail = self.bget("/api/blocks/detail", id="bailian")[1]
        self.assertEqual(detail["last_check"]["models"], self.names)
        self.assertNotIn("model", detail["card"])

    def test_7_same_site_reuses_its_block(self):
        from desktop.clerk import tasks as clerk_tasks
        _, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "甲", "base_url": "https://a-b.example.com/v1"})
        first = clerk_tasks.load_proposal(str(self.home), pid)["block"]
        blocks.save_card(first, self.home)
        _, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "乙", "base_url": "https://a.b.example.com/v1"})
        second = clerk_tasks.load_proposal(str(self.home), pid)["block"]
        blocks.save_card(second, self.home)
        blocks.remove_card(first["id"], self.home)
        _, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "乙", "base_url": "https://a.b.example.com/v1"})
        again = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertEqual(again["block"]["id"], second["id"])
        self.assertTrue(again.get("replaces"))

    def test_8_listed_drops_model(self):
        from desktop.clerk import tasks as clerk_tasks
        blocks.add_record("bailian", {"type": "check", "ok": False, "status": "listed", "reason": "已接上", "how": ""},
                          self.home)
        _, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus"})
        self.assertNotIn("model", clerk_tasks.load_proposal(str(self.home), pid)["block"])

    def test_4_old_proposal_same_second_still_wins(self):
        """整家块有旧的失败记录，旧编号的提议同一秒检测完放进来：显示的要是这次的结果（第三轮复查第 4 条）"""
        blocks.save_card(card("bailian"), self.home)
        blocks.add_record("bailian", {"type": "check", "ok": False, "status": "fail", "reason": "旧的", "how": ""}, self.home)
        old = dict(self.api_card("bailian-qwen-plus"), model="qwen-plus")
        blocks.add_record("bailian-qwen-plus", {"type": "check", "ok": False, "status": "listed", "reason": "新的",
                                                "how": "", "models": ["qwen-plus"]}, self.home)
        self.server.blocks_jobs["j4"] = {"kind": "check", "card": old, "done": True, "saved": False,
                                         "result": {"ok": False, "status": "listed", "reason": "新的", "how": "",
                                                    "models": ["qwen-plus"]}}
        self.assertEqual(self.bpost("/api/blocks/save", {"job": "j4"})[0], 200)
        last = blocks.last_record("bailian", self.home, type="check")
        self.assertEqual((last["reason"], last["models"]), ("新的", ["qwen-plus"]))

    def test_same_provider_again_replaces(self):
        blocks.save_card(card("bailian"), self.home)
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "name": "阿里百炼"})
        from desktop.clerk import tasks as clerk_tasks
        self.assertEqual(clerk_tasks.load_proposal(str(self.home), pid)["replaces"], "阿里百炼")

    def test_old_blocks_migrate_when_window_opens(self):
        self.home.mkdir(parents=True, exist_ok=True)
        blocks.save_card(dict(self.api_card("bailian-qwen"), model="qwen-plus"), self.home)
        listed = self.bget("/api/blocks/list")[1]["blocks"]
        self.assertEqual([(b["id"], b["name"]) for b in listed], [("bailian", "阿里百炼")])

    def test_old_pending_proposal_merges_on_save(self):
        """升级前留下的旧提议（一块一个模型）：放进基础层后并成整家一块，不会一家两块（复查第 4 条）"""
        blocks.save_card(card("bailian"), self.home)
        self.bget("/api/blocks/list")  # 窗口打开过，合并已经做过一次
        old = dict(self.api_card("bailian-qwen-plus"), model="qwen-plus")
        job = self.server.blocks_jobs["j-old"] = {"kind": "check", "card": old, "done": True, "saved": False,
                                                 "result": {"ok": True, "status": "ok"}}
        status, data = self.bpost("/api/blocks/save", {"job": "j-old"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["id"], "bailian")
        self.assertEqual([b["id"] for b in self.bget("/api/blocks/list")[1]["blocks"]], ["bailian"])
        del job

    def test_user_model_kept_after_failed_check(self):
        """自动试的都不行 → 用户另给一个模型名 → 重新出卡要带着它（复查第 1 条）；平时管家顺手写的模型不要"""
        from desktop.clerk import tasks as clerk_tasks
        _, pid = self.proposal("propose_connect_api", {"provider": "tencent", "model": "hy4"})
        self.assertNotIn("model", clerk_tasks.load_proposal(str(self.home), pid)["block"])
        blocks.add_record("tencent", {"type": "check", "ok": False, "status": "fail", "reason": "都没回话", "how": ""},
                          self.home)
        _, pid = self.proposal("propose_connect_api", {"provider": "tencent", "model": "hy4"})
        self.assertEqual(clerk_tasks.load_proposal(str(self.home), pid)["block"]["model"], "hy4")

    def test_similar_sites_do_not_share_a_block(self):
        from desktop.clerk import tasks as clerk_tasks
        _, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "甲",
                                                       "base_url": "https://a-b.example.com/v1"})
        first = clerk_tasks.load_proposal(str(self.home), pid)["block"]
        blocks.save_card(first, self.home)
        _, pid = self.proposal("propose_connect_api", {"provider": "other", "name": "乙",
                                                       "base_url": "https://a.b.example.com/v1"})
        second = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertNotEqual(first["id"], second["block"]["id"])
        self.assertNotIn("replaces", second)
        blocks.validate_card(second["block"])

    def test_listed_block_can_be_saved_and_shows_yellow(self):
        self.fake.mode = "bad_model"
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian"})
        _, data = self.confirm(cid, pid, api_key=KEY)
        result = self.job(data["job"])["result"]
        self.assertEqual(result["status"], "listed", result)
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 200)
        self.assertEqual(self.bget("/api/blocks/list")[1]["blocks"][0]["status"], "listed")
        from desktop.clerk import blocks_tools as bt
        self.assertIn("已接上", bt.run(str(self.home), "list_blocks", {}))

    def test_recheck_from_detail_fetches_list(self):
        blocks.save_card(card("bailian"), self.home)
        _, data = self.bpost("/api/blocks/check", {"id": "bailian", "api_key": KEY})
        self.assertTrue(self.job(data["job"])["result"]["ok"])
        self.assertEqual(self.bget("/api/blocks/list")[1]["blocks"][0]["models"], 3)

    def test_bind_does_not_ask_for_the_list(self):
        _, data = self.bind()
        self.assertTrue(data["ok"], data)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.bget("/api/blocks/state")[1]["setting"]["model"], "deepseek/deepseek-flash")


class ButlerTest(unittest.TestCase):
    def test_tool_does_not_require_model(self):
        tool = next(t for t in blocks_tools.TOOLS if t["function"]["name"] == "propose_connect_api")
        self.assertNotIn("model", tool["function"]["parameters"]["required"])
        self.assertIn("整家", tool["function"]["description"])

    def test_manual_says_no_model_picking(self):
        text = (ROOT / "desktop/clerk/roles/积木库手册/api.md").read_text(encoding="utf-8")
        self.assertIn("整家", text)
        self.assertIn("不选模型", text)
        self.assertNotIn("挑一个", text)
        self.assertNotIn("出卡前先用 list_models", text)
        self.assertNotIn("出提议前先用 list_models", text)


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageTest(unittest.TestCase):
    def test_views(self):
        script = r"""
var assert = require("assert");
var B = require(process.argv[1] + "/desktop/static/blocks.js");
var C = require(process.argv[1] + "/desktop/static/chat.js");
function labels(rows) { return rows.map(function (r) { return r.label; }); }

// 接入卡：没有“模型”一栏
var v = C.blockCard({ proposalKind: "connect_api", info: { name: "腾讯云", provider: "tencent",
  block: { base_url: "https://tokenhub.tencentmaas.com/v1", key_account: "tencent" } } });
assert.strictEqual(v.title, "接进基础层：腾讯云");
assert.deepStrictEqual(labels(v.rows), ["地址"]);
// 平台不给名单时管家给的那个：写明只是试通用
v = C.blockCard({ proposalKind: "connect_api", info: { name: "例子", provider: "other", model: "ex-chat",
  block: { base_url: "https://api.example.com/v1", key_account: "other:api.example.com" } } });
assert.deepStrictEqual(labels(v.rows), ["地址", "试通用的模型"]);

// 基础层详情：这家的模型 N 个 + 名单
var d = B.detailView({ card: { id: "tencent", name: "腾讯云", kind: "llm_api", provider: "tencent",
  base_url: "https://tokenhub.tencentmaas.com/v1" }, key_account: "tencent", category: "llm_api",
  last_check: { ok: true, status: "ok", reason: "通了", time: "2026-11-10T10:00:00", seconds: 2, models: ["hy4", "hy3"] } }, null);
var row = d.rows.filter(function (r) { return r.label === "这家的模型"; })[0];
assert.ok(row && /2 个/.test(row.text), "显示个数");
var list = d.rows.filter(function (r) { return r.label === "名单"; })[0];
assert.ok(list && list.list && /hy4/.test(list.text) && /hy3/.test(list.text));
assert.ok(labels(d.rows).indexOf("模型") === -1, "不再有单个“模型”一栏");
// 旧块自动变过来、还没重新检测：说明怎么看到名单
d = B.detailView({ card: { id: "bailian", name: "阿里百炼", kind: "llm_api", provider: "bailian", base_url: "https://x" },
  key_account: "bailian", last_check: { ok: true, status: "ok", reason: "通了", time: "2026-11-09T10:00:00", seconds: 2 } }, null);
row = d.rows.filter(function (r) { return r.label === "这家的模型"; })[0];
assert.ok(row && /检测/.test(row.text));
// 额度不足的说法不再说“所选模型”
d = B.detailView({ card: { id: "tencent", name: "腾讯云", kind: "llm_api", provider: "tencent", base_url: "https://x" },
  last_check: { ok: false, status: "quota", reason: "额度用完", how: "", time: "2026-11-10T10:00:00", seconds: 2, models: ["hy4"] } }, null);
assert.ok(/额度不足/.test(d.status.text));
// 拿到名单、试的模型没通：黄色“已接上”，能放进基础层
assert.strictEqual(B.statusText({ status: "listed" }), "已接上");
assert.strictEqual(B.statusClass({ status: "listed" }), "warn");
d = B.detailView({ card: { id: "x", name: "火山引擎", kind: "llm_api", provider: "other", base_url: "https://x" },
  last_check: { ok: false, status: "listed", reason: "接上了，试的没通", how: "", time: "2026-11-11T10:00:00", seconds: 2, models: ["a"] } }, null);
assert.strictEqual(d.status.cls, "warn");
assert.ok(/已接上/.test(d.status.text) && !/不能用/.test(d.status.text));
var jv = C.jobView({ kind: "check", done: true, result: { ok: false, status: "listed", reason: "接上了，试的没通", how: "以后再验" } });
assert.strictEqual(jv.state, "warn");
assert.strictEqual(jv.canSave, true);
assert.ok(!/额度/.test(jv.text));
assert.ok(/已接上/.test(C.blockDoneTitle({ state: "confirmed", proposalKind: "connect_api", info: { name: "火山引擎" } }, jv)));
assert.ok(!/额度/.test(C.blockDoneTitle({ state: "confirmed", proposalKind: "connect_api", info: { name: "火山引擎" } }, jv)));
console.log("ok");
"""
        done = subprocess.run(["node", "-e", script, str(ROOT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
