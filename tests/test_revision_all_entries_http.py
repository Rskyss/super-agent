"""旧版按钮：准备→用户确认→现有写回；准备/取消不写需求。"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from collab.flow import record
from collab.state import Card
from collab.workspace import init_collab
from desktop import server as web
from desktop.chat import ChatHub
from test_revision_all_entries import Client


class RevisionHttpEntries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / 'project'; self.project.mkdir(); init_collab(self.project)
        self.cdir = self.project / '.collab/cards/CARD-001'; self.cdir.mkdir(parents=True)
        self.req = self.cdir / '需求卡.md'
        self.original = '# 按钮\n\n## 怎么算做完\n1. 标题黑色\n2. 单击保存\n'
        self.req.write_text(self.original, encoding='utf-8')
        Card('CARD-001', state='待验收').save(self.cdir / '状态.json')
        (self.cdir / '开工.json').write_text('{"test_command":"true"}', encoding='utf-8')
        (Path(str(self.project) + '-worktrees') / 'CARD-001').mkdir(parents=True)
        record(self.cdir, '结束', state='待验收', summary='测试')
        self.server = web.make_server(port=0, token='fixture', hub=ChatHub(factories={}))
        self.server.terminal_path = lambda: ''
        self.server.clerk = Client('{"changes":[{"replaceNth":2,"text":"双击保存"},{"add":"保存成功提示"}]}')
        self.server.clerk_project = str(self.project)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def post(self, action, **extra):
        payload = dict(project=str(self.project), card='CARD-001', action=action, text='第二条改双击，再加成功提示', **extra)
        r = urllib.request.Request('http://127.0.0.1:%s/api/clerk/card' % self.server.server_port,
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'X-Chat-Token': 'fixture'}, method='POST')
        try:
            with urllib.request.urlopen(r, timeout=3) as answer:
                return answer.status, json.load(answer)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_old_button_prepare_then_confirm_updates_both_standard_and_words(self):
        status, result = self.post('prepare_revise')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['criteria_base'], ['标题黑色', '单击保存'])
        self.assertIn('单击保存 → 双击保存', result['summary'])
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)
        with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123):
            status, result = self.post('revise', criteria_base=result['criteria_base'], criteria_changes=result['criteria_changes'])
        self.assertEqual(status, 200, result)
        self.assertIn('2. 双击保存\n3. 保存成功提示', self.req.read_text(encoding='utf-8'))
        self.assertIn('## 追加要求\n第二条改双击，再加成功提示', self.req.read_text(encoding='utf-8'))

    def test_old_button_empty_split_can_follow_original_append_path(self):
        self.server.clerk = Client('{"changes":[]}')
        status, result = self.post('prepare_revise')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['criteria_changes'], [])
        with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123):
            status, answer = self.post('revise', criteria_base=result['criteria_base'], criteria_changes=[])
        self.assertEqual(status, 200, answer)
        self.assertTrue(self.req.read_text(encoding='utf-8').startswith(self.original))

    def test_old_button_missing_model_fails_without_writing(self):
        self.server.clerk = None
        status, result = self.post('prepare_revise')
        self.assertIn(status, (400, 409), result)
        self.assertIn('模型', str(result.get('error', '')))
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_old_button_model_bound_to_other_project_is_not_used(self):
        self.server.clerk_project = str(self.project / 'other')
        client = self.server.clerk
        status, result = self.post('prepare_revise')
        self.assertIn(status, (400, 409), result)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)


if __name__ == '__main__':
    unittest.main()
