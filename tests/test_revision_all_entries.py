"""第23项：旧版拆分只准备，不写卡；确认复用现有标准写回。"""
import importlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from collab.state import Card
from collab.workspace import init_collab
from collab.flow import record


class Client:
    api_key = 'fixture-revision-private'

    def __init__(self, output):
        self.output = output
        self.calls = []

    def chat(self, messages, on_text=None, tools=None):
        self.calls.append(messages)
        return {'text': self.output, 'tool_calls': []}


class LegacyRevision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        init_collab(self.project)
        self.cdir = self.project / '.collab/cards/CARD-001'
        self.cdir.mkdir(parents=True)
        self.req = self.cdir / '需求卡.md'
        self.original = '# 测试卡\n\n## 怎么算做完\n1. 标题黑色\n2. 单击保存\n'
        self.req.write_text(self.original, encoding='utf-8')
        Card('CARD-001', state='待验收').save(self.cdir / '状态.json')
        record(self.cdir, '结束', state='待验收', summary='测试')

    def module(self):
        try:
            return importlib.import_module('desktop.clerk.revision')
        except ModuleNotFoundError:
            self.fail('缺少旧版共用的逐条拆分准备服务')

    def prepare(self, client, **kwargs):
        return self.module().prepare_revision(str(self.project), 'CARD-001', '第二条改双击，再加保存成功提示', client, **kwargs)

    def test_prepare_returns_two_changes_and_actual_summary_without_writing(self):
        c = Client('{"changes":[{"replaceNth":2,"text":"双击保存"},{"add":"保存成功提示"}]}')
        before = {p.name: p.read_bytes() for p in self.cdir.iterdir() if p.is_file()}
        result = self.prepare(c)
        self.assertEqual(result['criteria_base'], ['标题黑色', '单击保存'])
        self.assertEqual(result['criteria_changes'], [{'replaceNth': 2, 'text': '双击保存'}, {'add': '保存成功提示'}])
        self.assertIn('单击保存 → 双击保存', result['summary'])
        self.assertIn('新增第3条', result['summary'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.cdir.iterdir() if p.is_file()})
        self.assertEqual(len(c.calls), 1)

    def test_valid_empty_split_preserves_original_words_for_append(self):
        result = self.prepare(Client('{"changes":[]}'))
        self.assertEqual(result['criteria_changes'], [])
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_bad_output_and_out_of_range_are_rejected_without_writing(self):
        for output in ['不是JSON', '{"changes":[{"replaceNth":99,"text":"越界"}]}', '{"changes":[{"add":"新行\\n## 标题"}]}']:
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                self.prepare(Client(output))
            self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_model_echoed_key_does_not_leave_in_error_or_card(self):
        client = Client('{"changes":[{"add":"fixture-revision-private"}]}')
        with self.assertRaises(RuntimeError) as got:
            self.prepare(client)
        self.assertNotIn(client.api_key, str(got.exception))
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_ended_card_is_rejected_before_calling_model(self):
        Card('CARD-001', state='已完成').save(self.cdir / '状态.json')
        c = Client('{"changes":[]}')
        with self.assertRaises(RuntimeError):
            self.prepare(c)
        self.assertEqual(c.calls, [])

    def test_no_configured_old_model_is_explicit_error_not_silent_append(self):
        with self.assertRaises(RuntimeError):
            self.prepare(None)
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_standard_changed_while_model_running_is_rejected(self):
        owner = self
        class Concurrent(Client):
            def chat(self, *args, **kwargs):
                owner.req.write_text(owner.original.replace('单击保存', '另一次修改'), encoding='utf-8')
                return super().chat(*args, **kwargs)
        with self.assertRaises(RuntimeError):
            self.prepare(Concurrent('{"changes":[{"replaceNth":2,"text":"双击保存"}]}'))
        self.assertIn('另一次修改', self.req.read_text(encoding='utf-8'))

    def test_hard_deadline_even_if_model_does_not_return(self):
        mod = self.module()
        release, finished = threading.Event(), threading.Event()
        class Slow(Client):
            def chat(self, *args, **kwargs):
                try:
                    release.wait(3)
                    return super().chat(*args, **kwargs)
                finally:
                    finished.set()
        started = time.monotonic()
        try:
            with mock.patch.object(mod, 'REVISION_TIMEOUT_SECONDS', 0.05):
                with self.assertRaisesRegex(RuntimeError, '超时'):
                    self.prepare(Slow('{"changes":[]}'))
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)
        finally:
            release.set()
            finished.wait(1)

    def test_common_validator_sees_trailing_control_characters_before_trimming(self):
        for text in ['新标准\n', '新标准\u2028']:
            with self.subTest(text=repr(text)), self.assertRaises(RuntimeError):
                self.prepare(Client(json.dumps({'changes': [{'add': text}]}, ensure_ascii=False)))
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_hard_deadline_also_releases_real_reasoning_stream_reader(self):
        from fake_model_server import FakeModel
        from desktop.clerk.client import ModelClient
        mod = self.module()
        fake = FakeModel(slow_seconds=1.2)
        finished = threading.Event()
        class Watched(ModelClient):
            def _read(self, *args, **kwargs):
                try:
                    return super()._read(*args, **kwargs)
                finally:
                    finished.set()
        client = Watched(fake.base, 'fixture-model', 'good-key', timeout=2)
        try:
            with mock.patch.object(mod, 'REVISION_TIMEOUT_SECONDS', 0.05):
                with self.assertRaisesRegex(RuntimeError, '超时'):
                    mod.prepare_revision(str(self.project), 'CARD-001', '请先思考，再把第二条改双击', client)
            self.assertTrue(finished.wait(0.35), '准备超时后真实模型还在持续读取思考流，不能只让调用方返回')
        finally:
            finished.wait(2)
            fake.close()

    def test_hard_deadline_releases_reader_while_response_headers_drip(self):
        self._assert_hard_deadline_releases_dripping_response(b'HTTP/1.1 200 OK\r\nX-Drip: ')

    def test_hard_deadline_releases_reader_while_error_body_drips(self):
        self._assert_hard_deadline_releases_dripping_response(
            b'HTTP/1.1 500 Internal Server Error\r\nContent-Length: 4001\r\n\r\n')

    def test_hard_deadline_releases_https_proxy_connect_header_reader(self):
        self._assert_hard_deadline_releases_dripping_response(
            b'HTTP/1.1 200 Connection established\r\nX-Drip: ', https_proxy=True)

    def _assert_hard_deadline_releases_dripping_response(self, prefix, https_proxy=False):
        from contextlib import ExitStack
        import socketserver
        from desktop.clerk.client import ModelClient
        mod = self.module()
        stop, finished, disconnected = threading.Event(), threading.Event(), threading.Event()
        seen_request = []

        class HeaderDrip(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    seen_request.append(self.request.recv(65536))
                    self.request.sendall(prefix)
                    while not stop.wait(0.01):
                        self.request.sendall(b'a')
                except OSError:
                    disconnected.set()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        class Watched(ModelClient):
            def chat(self, *args, **kwargs):
                try:
                    return super().chat(*args, **kwargs)
                finally:
                    finished.set()

        server = Server(('127.0.0.1', 0), HeaderDrip)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f'http://127.0.0.1:{server.server_address[1]}'
        base = 'https://review-fixture.invalid/v1' if https_proxy else address + '/v1'
        client = Watched(base, 'fixture-model', 'good-key', timeout=2)
        try:
            with ExitStack() as patches:
                if https_proxy:
                    patches.enter_context(mock.patch('urllib.request.getproxies', return_value={'https': address}))
                    patches.enter_context(mock.patch('urllib.request.proxy_bypass', return_value=False))
                with mock.patch.object(mod, 'REVISION_TIMEOUT_SECONDS', 0.1):
                    with self.assertRaisesRegex(RuntimeError, '超时'):
                        self.prepare(client)
                if https_proxy:
                    self.assertTrue(seen_request and seen_request[0].startswith(b'CONNECT '), '必须真实走到HTTPS代理隧道')
                self.assertTrue(finished.wait(0.5), '硬截止必须覆盖代理隧道、响应头及错误正文阶段，不能仅让准备调用方返回')
                self.assertTrue(disconnected.wait(0.5), '超时后应关闭真实连接')
            self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)
        finally:
            stop.set()
            finished.wait(2)
            server.shutdown()
            server.server_close()
            thread.join(1)


if __name__ == '__main__':
    unittest.main()
