"""第14项：原图随追加要求保存、按路径交接，坏输入不得写半张卡。"""
import base64
import json
import re
import tempfile
import unittest
from pathlib import Path
from collab.tasks import revise_card, TaskError
from collab.state import Card
from collab.workspace import init_collab
from collab.flow import read_flow

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')
JPG = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDjKKKK+qPHP//Z')


def image(data=PNG, media='image/png'):
    return {'mediaType': media, 'data': base64.b64encode(data).decode('ascii')}


class RevisionAttachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'project'
        init_collab(self.project)
        self.cdir = self.project / '.collab/cards/CARD-001'
        self.cdir.mkdir(parents=True)
        self.req = self.cdir / '需求卡.md'
        self.original = '# 走查\n\n## 怎么算做完\n1. 标题黑色\n2. 单击保存\n'
        self.req.write_text(self.original, encoding='utf-8')
        Card('CARD-001', state='待验收').save(self.cdir / '状态.json')

    def tearDown(self):
        self.tmp.cleanup()

    def test_png_jpg_original_bytes_and_relative_paths_recorded(self):
        revise_card(self.project, 'CARD-001', '按两张图修改', attachments=[image(), image(JPG, 'image/jpeg')])
        files = list((self.cdir / '附件').iterdir())
        self.assertEqual(len(files), 2)
        self.assertEqual({p.suffix for p in files}, {'.png', '.jpg'})
        self.assertEqual({p.read_bytes() for p in files}, {PNG, JPG})
        body = self.req.read_text(encoding='utf-8')
        for p in files:
            self.assertRegex(p.name, r'^\d[\d_-]*\.(png|jpg)$', '只用时间生成文件名')
            self.assertIn('附件/' + p.name, body)
        event = [e for e in read_flow(self.cdir) if e.get('kind') == '追加要求'][-1]
        self.assertEqual(set(event.get('attachments', [])), {'附件/' + p.name for p in files})
        self.assertNotIn(image()['data'], json.dumps(event))

    def test_bad_type_signature_or_base64_is_rejected_before_any_write(self):
        for payload in [image(b'GIF89a', 'image/gif'), image(PNG, 'image/jpeg'), image(b'not an image'), {'mediaType':'image/png','data':'%%%'}]:
            with self.assertRaises(TaskError, msg=payload['mediaType']):
                revise_card(self.project, 'CARD-001', '改', attachments=[payload])
            self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)
            self.assertFalse((self.cdir / '附件').exists())

    def test_ten_megabytes_inclusive_and_one_byte_more_rejected(self):
        data = PNG + b'\0' * (10 * 1024 * 1024 - len(PNG))
        revise_card(self.project, 'CARD-001', '边界图', attachments=[image(data)])
        self.assertEqual(next((self.cdir/'附件').iterdir()).read_bytes(), data)
        before = self.req.read_bytes()
        with self.assertRaises(TaskError):
            revise_card(self.project, 'CARD-001', '超限图', attachments=[image(data+b'\0')])
        self.assertEqual(self.req.read_bytes(), before)
        self.assertEqual(len(list((self.cdir/'附件').iterdir())), 1)

    def test_attachment_directory_symlink_is_rejected_without_outside_write(self):
        outside = Path(self.tmp.name)/'outside';outside.mkdir()
        (self.cdir/'附件').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(TaskError):
            revise_card(self.project, 'CARD-001', '改', attachments=[image()])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_stale_criteria_with_image_creates_no_attachment(self):
        with self.assertRaises(TaskError):
            revise_card(self.project, 'CARD-001', '改', attachments=[image()], criteria_base=['旧标准'],criteria_changes=[{'add':'新标准'}])
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)
        self.assertFalse((self.cdir/'附件').exists())

    def test_invalid_second_image_does_not_leave_first_image(self):
        with self.assertRaises(TaskError):
            revise_card(self.project,'CARD-001','改',attachments=[image(),image(b'bad')])
        self.assertFalse((self.cdir/'附件').exists())
        self.assertEqual(self.req.read_text(encoding='utf-8'), self.original)

    def test_all_three_construction_roles_receive_absolute_image_and_read_instruction(self):
        from collab.dispatch import _dispatch
        from collab.agents import AgentResult
        revise_card(self.project,'CARD-001','按图改',attachments=[image()])
        original_image = next((self.cdir/'附件').iterdir()).resolve()
        worktree = Path(self.tmp.name)/'worktree';worktree.mkdir()
        seen=[]
        def ask(adapter,prompt,cwd,**kwargs):
            seen.append(prompt)
            return AgentResult(ok=True, output='做完了', attempts=1)
        for role,adapter,step in [('测试员','codex','写测试'),('开发员','agy','开发'),('复查员','codex','复查')]:
            _dispatch(ask,self.cdir,role,adapter,step,'请处理本卡',worktree,{},1)
        for prompt in seen:
            self.assertIn(str(original_image),prompt)
            self.assertIn('先实际打开',prompt)
        self.assertEqual(list(worktree.iterdir()),[], '图片不得复制进施工副本')

    def test_dispatcher_plan_prompt_also_reads_absolute_image(self):
        from collab.planner import _build_prompt
        revise_card(self.project,'CARD-001','按图改',attachments=[image()])
        original_image = next((self.cdir/'附件').iterdir()).resolve()
        prompt = _build_prompt(self.cdir,self.req.read_text(encoding='utf-8'),False)
        self.assertIn(str(original_image),prompt)
        self.assertIn('先实际打开',prompt)
