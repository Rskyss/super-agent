"""71 号单验收：只用 Node 假页面、本机 HTTP 服务和临时资料夹，不访问外网。

指定的 test_acceptance_71_*.py 命令会收集本文件，并逐项调用 readfix71_check.js。
测试按施工单断言修复后的行为；产品尚未修复时应当失败，不能改成跳过。
"""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from collab import blocks, blocks_web as web


HERE = Path(__file__).resolve().parent


class PageReadfixTest(unittest.TestCase):
    def check_page(self, scenario):
        node = shutil.which("node")
        self.assertIsNotNone(node, "71 号单的页面验收需要 Node，不能跳过")
        run = subprocess.run(
            [node, str(HERE / "readfix71_check.js"), scenario],
            cwd=HERE.parent, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_s6_safe_link_protocols_and_whitespace(self):
        self.check_page("safe-link")

    def test_s6_ability_detail_list_links_and_titles(self):
        self.check_page("ability-links")

    def test_s6_chat_list_links_and_titles(self):
        self.check_page("chat-links")

    def test_s6_both_pages_use_shared_safe_link(self):
        self.check_page("shared-link")

    def test_c8_pending_then_done(self):
        self.check_page("trial-normal")

    def test_c8_stops_after_third_connection_failure(self):
        self.check_page("trial-errors")

    def test_c8_successful_pending_response_resets_failure_count(self):
        self.check_page("trial-reset")

    def test_c8_http_404_counts_as_failure(self):
        self.check_page("trial-404")

    def test_c8_other_non_2xx_counts_as_failure(self):
        self.check_page("trial-503")

    def test_c8_without_on_error_uses_on_done(self):
        self.check_page("trial-fallback")

    def test_c8_job_error_keeps_existing_callback_behavior(self):
        self.check_page("trial-job-error")

    def test_c8_failure_restores_buttons_and_allows_retry(self):
        self.check_page("trial-buttons")


class NpmCommandReadfixTest(unittest.TestCase):
    def verify(self, commands, command="", install="npm install -g foo"):
        # 只替换页面读取、包目录查询；保留真正的命令解析和核验。
        with mock.patch.object(web, "read_page", return_value={
            "url": "https://docs.example/install", "domain": "docs.example", "text": install,
        }), mock.patch.object(web, "npm_info", return_value={
            "exists": True, "commands": commands, "latest": "1.0.0", "new": False,
        }):
            return web.verify_install("https://docs.example/install", install, command=command)

    def test_c12_registered_command_passes(self):
        self.assertEqual(self.verify(["foo", "foo-cli"], "foo-cli")["command"], "foo-cli")

    def test_c12_unregistered_command_rejected_with_ordered_choices(self):
        for commands in (["foo", "foo-cli"], ["foo-cli", "foo"]):
            with self.subTest(commands=commands):
                with self.assertRaises(blocks.BlockError) as caught:
                    self.verify(commands, "ls")
                self.assertEqual(str(caught.exception),
                                 "装好后的命令名不在这个包登记的命令里：这个包登记的是 " + "、".join(commands))

    def test_c12_default_command_still_used(self):
        self.assertEqual(self.verify(["foo", "foo-cli"])["command"], "foo")
        self.assertEqual(self.verify(["foo-cli"])["command"], "foo-cli")

    def test_c12_empty_command_list_keeps_format_check(self):
        self.assertEqual(self.verify([], "another-cli")["command"], "another-cli")
        for command in ("bad command", "../foo", "--foo", ""):
            with self.subTest(command=command), self.assertRaises(blocks.BlockError):
                self.verify([], command)

    def test_c12_brew_and_script_keep_explicit_command_rule(self):
        for install in ("brew install foo", "curl https://docs.example/install.sh | sh"):
            with self.subTest(install=install), mock.patch.object(web, "read_page", return_value={
                "url": "https://docs.example/install", "domain": "docs.example", "text": install,
            }), mock.patch.object(web, "brew_info", return_value={"exists": True, "new": False}), \
                    mock.patch.object(web, "check_url"):
                result = web.verify_install("https://docs.example/install", install, command="custom-cli")
                self.assertEqual(result["command"], "custom-cli")


@contextmanager
def local_json_server(respond):
    """所有请求只到 127.0.0.1；记录跳转后的 GET 和 POST，避免漏掉转成 GET 的泄露。"""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append({"method": self.command, "path": self.path,
                             "headers": dict(self.headers), "body": body})
            status, headers, payload = respond(self.path)
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:%s" % server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class SearchRedirectReadfixTest(unittest.TestCase):
    def test_s1_search_redirect_does_not_send_anything_to_target(self):
        key = "tvly-" + "a1" * 12
        for code in (301, 302, 303, 307, 308):
            with self.subTest(status=code), local_json_server(
                lambda _path: (200, {}, {"results": []})
            ) as (target, received):
                # 回包也故意带假密钥，确认 HTTPError 的正文不会进入用户提示。
                with local_json_server(lambda _path: (
                    code, {"Location": target + "/must-not-receive"}, {"error": key},
                )) as (origin, sent), mock.patch.object(web, "SEARCH_URL", origin + "/search"):
                    error = None
                    try:
                        web.search("公园", key)
                    except blocks.BlockError as exc:
                        error = exc
                    self.assertEqual(received, [], "跳转目标不能收到请求或密钥")
                    self.assertIsNotNone(error, "搜索跳转必须报错")
                    self.assertIn("联网搜索出错", str(error))
                    self.assertNotIn(key, str(error))
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(sent[0]["headers"].get("Authorization"), "Bearer " + key)

    def test_s1_search_200_still_returns_results(self):
        payload = {"results": [{"title": "公园", "url": "https://example.com/park", "content": "摘要"}]}
        with local_json_server(lambda _path: (200, {}, payload)) as (origin, sent), \
                mock.patch.object(web, "SEARCH_URL", origin + "/search"):
            self.assertEqual(web.search("公园", "tvly-" + "b2" * 12), [
                {"title": "公园", "url": "https://example.com/park", "snippet": "摘要"},
            ])
            self.assertEqual(sent[0]["method"], "POST")
            self.assertEqual(json.loads(sent[0]["body"]), {"query": "公园", "max_results": 5})

    def test_s1_get_json_also_does_not_follow_redirects(self):
        with local_json_server(lambda _path: (200, {}, {"wrong": True})) as (target, received):
            with local_json_server(lambda _path: (302, {"Location": target + "/target"}, {})) as (origin, _):
                status, _body = web._get_json(origin + "/package")
                self.assertEqual(received, [], "npm 等 GET 查询也不能跟随跳转")
                self.assertEqual(status, 302)

    def test_s1_normal_get_and_http_error_remain_readable(self):
        for code in (200, 404):
            with self.subTest(status=code), local_json_server(
                lambda _path: (code, {}, {"message": "原来的正文"})
            ) as (origin, _):
                self.assertEqual(web._get_json(origin + "/package"), (code, {"message": "原来的正文"}))


class SecretReadfixTest(unittest.TestCase):
    def test_s5_new_formats_standalone_and_in_sentence(self):
        for prefix in ("pplx-", "gsk_", "fw_"):
            for text in (prefix + "a1" * 10, "备注：" + prefix + "a1" * 12 + "，请保密"):
                with self.subTest(prefix=prefix, sentence=text.startswith("备注")):
                    self.assertTrue(blocks._has_secret(text), "新增平台密钥必须识别")

    def test_s5_short_words_and_alphanumeric_left_boundary_not_secrets(self):
        words = ["task-configuration", "gsk", "fw_x"]
        for prefix in ("pplx-", "gsk_", "fw_"):
            words.extend([prefix + "a" * 19, "x" + prefix + "a1" * 12, "7" + prefix + "a1" * 12])
        for word in words:
            with self.subTest(word=word):
                self.assertFalse(blocks._has_secret(word), "普通词或字母数字紧邻前缀不应误判")

    def test_s5_content_and_notes_rejected_before_writing_files(self):
        for prefix in ("pplx-", "gsk_", "fw_"):
            secret = prefix + "a1" * 12
            for field in ("name", "note"):
                with self.subTest(prefix=prefix, field=field), tempfile.TemporaryDirectory() as tmp:
                    card = {"id": "safe-search", "kind": "search", "provider": "tavily",
                            "name": "测试搜索", "key_account": "tavily", field: "内容里有 " + secret + "。"}
                    with self.assertRaises(blocks.BlockError):
                        blocks.save_card(card, tmp)
                    self.assertEqual(list(Path(tmp).rglob("*")), [], "不能先写文件再报错")

    def test_s5_nested_record_content_is_blocked_before_writing(self):
        for prefix in ("pplx-", "gsk_", "fw_"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(blocks.BlockError):
                    blocks.add_record("safe-search", {"type": "trial", "output": [
                        {"note": "结果：" + prefix + "a1" * 12 + "。"},
                    ]}, tmp)
                self.assertEqual(list(Path(tmp).rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
