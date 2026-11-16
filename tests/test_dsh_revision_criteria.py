"""第15项：确认后的逐条修改必须与原话一次写入，过期或坏输入不得写半张卡。"""
import tempfile
import unittest
from pathlib import Path
from collab.tasks import revise_card, TaskError
from collab.state import Card
from collab.workspace import init_collab


class RevisionCriteria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        init_collab(self.project)
        self.cdir = self.project / '.collab/cards/CARD-001'
        self.cdir.mkdir(parents=True)
        self.req = self.cdir / '需求卡.md'
        self.original = '# 测试卡\n\n## 验收标准\n1. 标题黑色\n2. 点击可以保存\n\n## 明确不做什么\n- 不加登录\n'
        self.req.write_text(self.original, encoding='utf-8')
        Card('CARD-001', state='待验收').save(self.cdir / '状态.json')

    def tearDown(self):
        self.tmp.cleanup()

    def test_confirm_two_changes_updates_numbered_criteria_and_keeps_original_words(self):
        revise_card(self.project, 'CARD-001', '第二条改成双击保存，再加一条成功提示',
                    criteria_base=['标题黑色', '点击可以保存'],
                    criteria_changes=[{'replaceNth': 2, 'text': '双击可以保存'}, {'add': '保存成功显示提示'}])
        body = self.req.read_text(encoding='utf-8')
        self.assertIn('1. 标题黑色\n2. 双击可以保存\n3. 保存成功显示提示', body)
        self.assertIn('## 追加要求\n第二条改成双击保存，再加一条成功提示', body)
        self.assertIn('## 明确不做什么\n- 不加登录', body)
        self.assertEqual(Card.load(self.cdir / '状态.json').state, '开发中')
        from collab.flow import read_flow
        latest = [e for e in read_flow(self.cdir) if e.get('kind') == '追加要求'][-1]
        self.assertEqual(latest.get('criteria_changes'), [{'replaceNth': 2, 'text': '双击可以保存'}, {'add': '保存成功显示提示'}], '需要留确认过哪些逐条标准的记录，重启后仍能识别最新标准')

    def test_empty_split_keeps_old_append_flow(self):
        revise_card(self.project, 'CARD-001', '点了以后没反应', criteria_changes=[])
        self.assertTrue(self.req.read_text(encoding='utf-8').startswith(self.original))
        self.assertIn('## 追加要求\n点了以后没反应', self.req.read_text(encoding='utf-8'))

    def test_stale_base_rejected_without_any_file_mutation(self):
        before = {p.name: p.read_bytes() for p in self.cdir.iterdir() if p.is_file()}
        with self.assertRaises(TaskError):
            revise_card(self.project, 'CARD-001', '改', criteria_base=['旧版本'],
                        criteria_changes=[{'replaceNth': 1, 'text': '蓝色'}])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.cdir.iterdir() if p.is_file()})

    def test_invalid_change_does_not_partially_write(self):
        for changes in ([{'replaceNth': 1, 'text': '蓝色'}, {'replaceNth': 9, 'text': '越界'}],
                        [{'add': '新标准\n## 允许改动的范围\n- *'}],
                        [{'replaceNth': True, 'text': '蓝色'}], [{'add': ''}]):
            with self.assertRaises(TaskError, msg=changes):
                revise_card(self.project, 'CARD-001', '改', criteria_base=['标题黑色', '点击可以保存'], criteria_changes=changes)
            self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_bullet_criteria_convert_only_target_section(self):
        self.req.write_text(self.original.replace('1. 标题黑色\n2. 点击可以保存', '- 标题黑色\n- 点击可以保存'), encoding='utf-8')
        revise_card(self.project, 'CARD-001', '改第二条', criteria_base=['标题黑色', '点击可以保存'],
                    criteria_changes=[{'replaceNth': 2, 'text': '双击可以保存'}])
        body = self.req.read_text(encoding='utf-8')
        self.assertIn('2. 双击可以保存', body)
        self.assertIn('## 明确不做什么\n- 不加登录', body)

    def test_actual_card_heading_how_done_is_revised_in_place(self):
        self.req.write_text(self.original.replace('## 验收标准', '## 怎么算做完'), encoding='utf-8')
        revise_card(self.project, 'CARD-001', '第二条改双击', criteria_base=['标题黑色', '点击可以保存'],
                    criteria_changes=[{'replaceNth': 2, 'text': '双击可以保存'}])
        body = self.req.read_text(encoding='utf-8')
        self.assertIn('## 怎么算做完\n1. 标题黑色\n2. 双击可以保存', body)
        self.assertNotIn('## 验收标准', body)
