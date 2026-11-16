"""第18项：方案未开始或失败，不冒充正在写；共享后台的真实公开输出。"""
import json
import tempfile
import unittest
from pathlib import Path
from collab.tasks import new_card
from collab.flow import record
from desktop.view import card_view

class DshProgressTruthTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'.git').mkdir()
    def make(self,deferred=False):
        card=new_card(self.root,'进度核对','只问进度','不开工','等方案',deferred_plan=deferred)
        return card,self.root/'.collab/cards'/card
    def station(self,card):
        v=card_view(self.root,card);return v,next(s for s in v['stations'] if s['key']=='disp')
    def test_waiting_for_dispatcher_does_not_claim_working(self):
        c,_=self.make(True);v,s=self.station(c)
        self.assertEqual(v['card']['phase']['code'],'plan_waiting')
        self.assertEqual(s['state'],'wait');self.assertIn('接',s['detail'])
        self.assertNotIn('正在',s['detail']);self.assertFalse(v['live'])
    def test_queued_plan_is_distinct_from_missing_dispatcher(self):
        c,_=self.make();v,s=self.station(c)
        self.assertEqual(v['card']['phase']['code'],'plan_waiting')
        self.assertEqual(s['state'],'wait');self.assertIn('排队',s['detail'])
        self.assertNotIn('接入',s['detail']);self.assertFalse(v['live'])
    def test_failed_plan_reports_failure_and_reason(self):
        c,d=self.make();record(d,'方案失败',summary='订阅连接超时，请重试')
        v,s=self.station(c);self.assertEqual(v['card']['phase']['code'],'plan_failed')
        self.assertEqual(s['state'],'fail');self.assertIn('订阅连接超时',s['detail'])
        self.assertFalse(v['live'])

    def test_legacy_card_without_source_waits_instead_of_claiming_active(self):
        c,d=self.make();(d/'来源.json').unlink(missing_ok=True)
        v,s=self.station(c)
        self.assertEqual(v['card']['phase']['code'],'plan_waiting')
        self.assertEqual(s['state'],'wait')
        self.assertIn('排队',s['detail']);self.assertFalse(v['live'])
