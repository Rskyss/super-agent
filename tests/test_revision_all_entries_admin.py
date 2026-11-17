"""旧版行政的真实提议/确认状态机也必须携带逐条改动。"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from collab.flow import record
from collab.state import Card
from collab.workspace import init_collab
from desktop.clerk.desk import ClerkDesk


WORDS = '第二条改为双击保存，加保存成功提示'


class ClerkClient:
    api_key = 'fixture-admin-revision'

    def __init__(self, changes):
        self.changes = changes
        self.revision_calls = []

    def chat(self, messages, on_text=None, tools=None):
        if '你负责把用户对任务卡的修改意见' in str(messages[0].get('content', '')):
            self.revision_calls.append(messages)
            return {'text': json.dumps({'changes': self.changes}, ensure_ascii=False), 'tool_calls': []}
        if tools and messages[-1].get('role') == 'user':
            return {'text': '', 'tool_calls': [{'id': 'fixture-call', 'name': 'propose_revise', 'arguments': json.dumps({'text': WORDS}, ensure_ascii=False)}]}
        return {'text': '已出示，请你确认。', 'tool_calls': []}


class AdminRevision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / 'project'; self.project.mkdir(); init_collab(self.project)
        self.cdir = self.project / '.collab/cards/CARD-001'; self.cdir.mkdir(parents=True)
        self.req = self.cdir / '需求卡.md'
        self.original = '# 按钮\n\n## 要做什么\n改按钮\n\n## 怎么算做完\n1. 标题黑色\n2. 单击保存\n'
        self.req.write_text(self.original, encoding='utf-8')
        Card('CARD-001', state='待验收').save(self.cdir / '状态.json')
        (self.cdir / '开工.json').write_text('{"test_command":"true"}', encoding='utf-8')
        (Path(str(self.project) + '-worktrees') / 'CARD-001').mkdir(parents=True)
        record(self.cdir, '结束', state='待验收', summary='测试')
        self.client = ClerkClient([{'replaceNth': 2, 'text': '双击保存'}, {'add': '保存成功提示'}])
        self.desk = ClerkDesk(lambda: self.client)

    def proposal(self):
        opened = self.desk.open_card(str(self.project), 'CARD-001')
        cid = opened['conversation']
        self.desk.send(str(self.project), cid, WORDS)
        until = time.monotonic() + 3
        while time.monotonic() < until:
            answer = self.desk.events(str(self.project), cid, 0, wait=0)
            if not answer['busy']:
                boxes = [e for e in answer['events'] if e.get('type') == 'proposal' and e.get('kind') == 'revise']
                self.assertTrue(boxes, answer)
                return cid, boxes[-1]
            time.sleep(0.01)
        self.fail('行政没有结束本轮')

    def test_admin_proposal_has_actual_old_to_new_before_confirmation(self):
        _, box = self.proposal()
        self.assertEqual(box.get('criteria_changes'), self.client.changes)
        self.assertIn('单击保存 → 双击保存', box.get('summary', ''))
        self.assertEqual(len(self.client.revision_calls), 1)
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_admin_confirm_updates_standard_and_original_words(self):
        cid, box = self.proposal()
        with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123):
            self.desk.decide_proposal(str(self.project), cid, box['id'], 'confirm')
        self.assertIn('2. 双击保存\n3. 保存成功提示', self.req.read_text(encoding='utf-8'))
        self.assertIn('## 追加要求\n' + WORDS, self.req.read_text(encoding='utf-8'))

    def test_admin_cancel_preserves_requirement_bytes(self):
        cid, box = self.proposal()
        self.desk.decide_proposal(str(self.project), cid, box['id'], 'cancel')
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_admin_empty_split_confirmation_keeps_append_behavior(self):
        self.client.changes = []
        cid, box = self.proposal()
        with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123):
            self.desk.decide_proposal(str(self.project), cid, box['id'], 'confirm')
        self.assertTrue(self.req.read_text(encoding='utf-8').startswith(self.original))
        self.assertEqual(len(self.client.revision_calls), 1)

    def test_saved_changes_offered_after_round_also_show_updated_criteria(self):
        from desktop.clerk.tasks import TaskWatcher
        cid = self.desk.open_card(str(self.project), 'CARD-001')['conversation']
        (self.cdir / '待转达.jsonl').write_text(json.dumps({'text': WORDS}, ensure_ascii=False) + '\n', encoding='utf-8')
        TaskWatcher(self.desk).tick(str(self.project))
        until, boxes = time.monotonic() + 2, []
        while time.monotonic() < until:
            events = self.desk.events(str(self.project), cid, 0, wait=0)['events']
            boxes = [e for e in events if e.get('type') == 'proposal' and e.get('kind') == 'revise']
            if boxes and boxes[-1].get('criteria_changes') is not None:
                break
            time.sleep(0.01)
        self.assertTrue(boxes)
        self.assertEqual(boxes[-1].get('criteria_changes'), self.client.changes)
        self.assertIn('单击保存 → 双击保存', boxes[-1].get('summary', ''))
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_pending_box_arriving_during_user_split_cannot_lose_its_standard(self):
        from desktop.clerk import tasks
        cid = self.desk.open_card(str(self.project), 'CARD-001')['conversation']
        conv = self.desk._conversation(str(self.project), cid)
        started, release = threading.Event(), threading.Event()
        old_words, new_words = '第一条改为绿色标题', '第二条改为双击保存'

        class Splitter(ClerkClient):
            def chat(inner, messages, on_text=None, tools=None):
                words = messages[-1]['content']
                if not inner.revision_calls:
                    started.set()
                    release.wait(3)
                inner.revision_calls.append(messages)
                changes = []
                if old_words in words:
                    changes.append({'replaceNth': 1, 'text': '绿色标题'})
                if new_words in words:
                    changes.append({'replaceNth': 2, 'text': '双击保存'})
                return {'text': json.dumps({'changes': changes}, ensure_ascii=False), 'tool_calls': []}

        client, result = Splitter([]), []
        worker = threading.Thread(target=lambda: result.append(tasks.propose_revise(conv, {'text': new_words}, client)), daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            tasks._append_pending(str(self.project), 'CARD-001', old_words)
            tasks._show_revise_box(conv, 'CARD-001', old_words, pending=1,
                                   criteria_base=['标题黑色', '单击保存'],
                                   criteria_changes=[{'replaceNth': 1, 'text': '绿色标题'}],
                                   summary='第1条：标题黑色 → 绿色标题')
        finally:
            release.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        boxes = tasks._open_in_conversation(str(self.project), cid)
        self.assertTrue(boxes)
        for box in boxes:
            changes = box.get('criteria_changes', [])
            if old_words in box.get('text', ''):
                self.assertIn({'replaceNth': 1, 'text': '绿色标题'}, changes)
            if new_words in box.get('text', ''):
                self.assertIn({'replaceNth': 2, 'text': '双击保存'}, changes)
        pending_box = next(b for b in boxes if b.get('pending'))
        with mock.patch('desktop.clerk.tasks.sync_copy', return_value=[]), mock.patch('desktop.clerk.tasks.launch', return_value=123):
            self.desk.decide_proposal(str(self.project), cid, pending_box['id'], 'confirm')
        self.assertIn('1. 绿色标题', self.req.read_text(encoding='utf-8'))
        self.assertEqual((self.cdir / '待转达.jsonl').read_text(encoding='utf-8'), '')

    def test_late_saved_box_cannot_replace_a_new_user_request(self):
        from desktop.clerk import tasks
        cid, _ = self.proposal()
        conv = self.desk._conversation(str(self.project), cid)
        old_words = '第一条改为绿色标题'
        tasks._append_pending(str(self.project), 'CARD-001', old_words)
        # 模拟后台完成“无打开框”检查后、真正发布前，用户的新框先到。
        tasks.show_saved_revise(conv, 'CARD-001', old_words, 1,
                                criteria_base=['标题黑色', '单击保存'],
                                criteria_changes=[{'replaceNth': 1, 'text': '绿色标题'}],
                                summary='第1条：标题黑色 → 绿色标题')
        boxes = tasks._open_in_conversation(str(self.project), cid)
        user_boxes = [b for b in boxes if WORDS in b.get('text', '')]
        self.assertTrue(user_boxes, '后台迟到的待转达框不能挤掉用户的新请求')
        for box in user_boxes:
            self.assertIn({'replaceNth': 2, 'text': '双击保存'}, box.get('criteria_changes', []))
        self.assertIn(old_words, (self.cdir / '待转达.jsonl').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
