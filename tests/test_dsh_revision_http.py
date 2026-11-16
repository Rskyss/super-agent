"""第15项：经正式HTTP确认写回的两条改动不能丢在转发层。"""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import base64
import http.client
from pathlib import Path
from unittest import mock
from collab.state import Card
from collab.workspace import init_collab
from collab.flow import record
from desktop import server as web
from desktop.chat import ChatHub
from test_dsh_revision_attachments import PNG, image


class RevisionHttp(unittest.TestCase):
    def test_http_confirm_updates_both_criteria_and_original_text(self):
        self.check(False)

    def test_http_stale_confirm_is_rejected_without_writing(self):
        self.check(True)

    def test_image_larger_than_old_one_megabyte_body_limit_reaches_card(self):
        self.check(False, attachments=[image(PNG + b'\0' * (1100 * 1024))])

    def test_stale_confirm_with_image_leaves_no_file(self):
        self.check(True, attachments=[image()])

    def test_bad_image_via_http_is_rejected_without_writing(self):
        self.check(False, attachments=[image(b'not an image')], invalid=True)

    def test_ordinary_chat_keeps_old_one_megabyte_limit(self):
        self.check(False, ordinary=True)

    def check(self, stale, attachments=None, invalid=False, ordinary=False):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'project'
            project.mkdir()
            init_collab(project)
            c = project / '.collab/cards/CARD-001'
            c.mkdir(parents=True)
            req = c / '需求卡.md'
            before = '# 走查\n\n## 怎么算做完\n1. 标题黑色\n2. 单击保存\n'
            req.write_text(before, encoding='utf-8')
            Card('CARD-001', state='待验收').save(c / '状态.json')
            (Path(str(project) + '-worktrees') / 'CARD-001').mkdir(parents=True)
            (c / '开工.json').write_text('{"test_command":"true"}')
            record(c, '结束', state='待验收', summary='受控测试')
            srv = web.make_server(port=0, token='test', hub=ChatHub(factories={}))
            srv.terminal_path = lambda: ''
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            body = {'project': str(project), 'card': 'CARD-001', 'action': 'revise', 'text': '第二条双击，增加成功提示',
                    'criteria_base': ['过期'] if stale else ['标题黑色', '单击保存'],
                    'criteria_changes': [{'replaceNth': 2, 'text': '双击保存'}, {'add': '保存成功提示'}]}
            if attachments is not None:
                body['attachments'] = attachments
            if ordinary:
                body['text'] = 'a' * (1100 * 1024)
            route = '/api/chat/send' if ordinary else '/api/clerk/card'
            request = urllib.request.Request('http://127.0.0.1:%s%s' % (srv.server_address[1], route),
                    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'X-Chat-Token': 'test'}, method='POST')
            try:
                with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123) as launch:
                    connection = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=5)
                    try:
                        try:
                            connection.request('POST', route, body=json.dumps(body).encode(), headers={'Content-Type':'application/json','X-Chat-Token':'test'})
                        except BrokenPipeError:
                            # 413可在服务端尚未收完body时返回；仍读真实响应，不把断管推断成状态码。
                            pass
                        answer = connection.getresponse()
                        status = answer.status
                        answer.read()
                    finally:
                        connection.close()
                    result = req.read_text(encoding='utf-8')
                    if stale or invalid or ordinary:
                        self.assertIn(status, (413,) if ordinary else (400, 409))
                        self.assertEqual(result, before)
                        launch.assert_not_called()
                        self.assertFalse((c/'附件').exists())
                    else:
                        self.assertEqual(status, 200)
                        self.assertIn('2. 双击保存\n3. 保存成功提示', result)
                        self.assertIn('## 追加要求\n第二条双击，增加成功提示', result)
                        launch.assert_called_once()
                        if attachments:
                            files = list((c/'附件').iterdir())
                            self.assertEqual(len(files), len(attachments))
                            self.assertEqual(files[0].read_bytes(), base64.b64decode(attachments[0]['data']))
                            self.assertIn('附件/'+files[0].name, result)
            finally:
                srv.shutdown()
                srv.server_close()
