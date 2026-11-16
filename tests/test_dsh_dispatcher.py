"""DSH第10项：没有通过真实检测不能启动方案，检测只用隔离积木库。"""
import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from collab.config import save_team_member,save_models,DEFAULT_MODELS
from desktop.clerk import tasks

class DshDispatcherTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.project=Path(self.tmp.name)/'项目';self.project.mkdir();self.home=Path(self.tmp.name)/'隔离积木库';self.home.mkdir();subprocess.run(['git','init','-q'],cwd=self.project,check=True)
 def detector(self,checker):
  try:m=importlib.import_module('desktop.dsh_dispatcher')
  except ModuleNotFoundError:self.fail('缺少带隔离目录的真实调度员检测')
  return m.DispatcherDetector(checker=checker)
 def test_deferred_card_is_created_without_starting_plan(self):
  with mock.patch.object(tasks,'launch') as launch:
   result=tasks.create_card(str(self.project),'测试','加一个按钮','不改别的','按钮出现',request_id='deferred-1',start_plan=False)
  self.assertTrue((self.project/'.collab/cards'/result['card']/'需求卡.md').is_file());self.assertFalse(result['planning']);launch.assert_not_called()
 def test_configured_name_is_not_readiness_and_failed_real_check_stays_missing(self):
  save_team_member(self.project,'dispatcher','codex');checker=mock.Mock(return_value={'ok':False,'reason':'没有登录','status':'fail'});d=self.detector(checker)
  self.assertNotEqual(d.status(str(self.project),str(self.home))['status'],'ready')
  result=d.detect(str(self.project),str(self.home));self.assertEqual(result['status'],'missing');self.assertIn('没有登录',result['reason']);self.assertEqual(Path(checker.call_args.kwargs['root']).resolve(),self.home.resolve());self.assertFalse(checker.call_args.kwargs['record'])
 def test_success_is_cached_but_model_change_requires_new_real_check(self):
  save_team_member(self.project,'dispatcher','codex');checker=mock.Mock(return_value={'ok':True,'reason':'通了','status':'ok'});d=self.detector(checker)
  self.assertEqual(d.detect(str(self.project),str(self.home))['status'],'ready');d.detect(str(self.project),str(self.home));self.assertEqual(checker.call_count,1)
  save_models(self.project,{**DEFAULT_MODELS,'codex':'test-other-model'});self.assertNotEqual(d.status(str(self.project),str(self.home))['status'],'ready');self.assertEqual(d.detect(str(self.project),str(self.home))['status'],'ready');self.assertEqual(checker.call_count,2)
 def test_no_home_and_real_legacy_home_are_refused_before_check(self):
  from collab import blocks
  save_team_member(self.project,'dispatcher','codex');checker=mock.Mock();d=self.detector(checker)
  for home in ['',str(blocks.DEFAULT_ROOT)]:
   with self.assertRaises((ValueError,RuntimeError)):d.detect(str(self.project),home)
  checker.assert_not_called()
 def test_concurrent_detection_joins_one_real_check(self):
  import threading,time
  from concurrent.futures import ThreadPoolExecutor
  save_team_member(self.project,'dispatcher','codex');entered=threading.Event();release=threading.Event()
  def check(*args,**kwargs):entered.set();release.wait(2);return {'ok':True,'reason':'通了','status':'ok'}
  checker=mock.Mock(side_effect=check);d=self.detector(checker)
  with ThreadPoolExecutor(max_workers=2) as pool:
   first=pool.submit(d.detect,str(self.project),str(self.home));self.assertTrue(entered.wait(1));second=pool.submit(d.detect,str(self.project),str(self.home));time.sleep(.03);release.set();self.assertEqual(first.result(3)['status'],'ready');self.assertEqual(second.result(3)['status'],'ready')
  self.assertEqual(checker.call_count,1)
 def test_ready_cache_expires_without_background_cli_call(self):
  m=importlib.import_module('desktop.dsh_dispatcher');save_team_member(self.project,'dispatcher','codex');now=[0.0];checker=mock.Mock(return_value={'ok':True,'status':'ok','reason':'通了'});d=m.DispatcherDetector(checker=checker,clock=lambda:now[0],cache_ttl=60)
  self.assertEqual(d.detect(str(self.project),str(self.home))['status'],'ready');now[0]=61;self.assertNotEqual(d.status(str(self.project),str(self.home))['status'],'ready');self.assertEqual(checker.call_count,1);d.detect(str(self.project),str(self.home));self.assertEqual(checker.call_count,2)
 def test_unsafe_card_is_rejected_before_connect_probe(self):
  m=importlib.import_module('desktop.dsh_dispatcher');detector=mock.Mock();detector.detect_tool.return_value={'status':'missing','reason':'fixture'}
  for card in ['../outside','a/b','/tmp/not-a-card']:
   with self.subTest(card=card):
    with self.assertRaises((ValueError,RuntimeError)):m.connect_dispatcher(str(self.project),str(self.home),'codex',card_id=card,detector=detector)
  detector.detect_tool.assert_not_called()
 def test_child_of_legacy_vault_is_also_refused(self):
  from collab import blocks
  m=importlib.import_module('desktop.dsh_dispatcher');legacy=Path(self.tmp.name)/'模拟旧库'
  with mock.patch.object(blocks,'DEFAULT_ROOT',legacy):
   with self.assertRaises((ValueError,RuntimeError)):m.require_isolated_blocks_home(str(legacy/'子目录'))
 def test_watcher_cannot_start_between_card_creation_and_deferred_mark(self):
  watcher=tasks.TaskWatcher(mock.Mock());original=tasks.new_card
  def create_and_tick(*a,**kw):
   card=original(*a,**kw);watcher._tick_card(str(self.project),self.project/'.collab/cards'/card);return card
  with mock.patch.object(tasks,'new_card',side_effect=create_and_tick),mock.patch.object(tasks,'launch') as launch:
   tasks.create_card(str(self.project),'等接入','做事','不改别的','做好',request_id='atomic-defer',start_plan=False)
  launch.assert_not_called()
 def test_busy_planner_eventually_starts_only_requested_deferred_card(self):
  import threading
  m=importlib.import_module('desktop.dsh_dispatcher');save_team_member(self.project,'dispatcher','codex');d=self.detector(mock.Mock(return_value={'ok':True,'status':'ok','reason':'通了'}));card=tasks.create_card(str(self.project),'排队方案','做事','不改别的','做好',request_id='queue-defer',start_plan=False)['card'];started=threading.Event();calls=[]
  def launch(*args):
   calls.append(args)
   if len(calls)==1:raise tasks.TaskBusy('前一卡在写方案')
   started.set();return 0
  with mock.patch.object(tasks,'launch',side_effect=launch):
   result=m.connect_dispatcher(str(self.project),str(self.home),'codex',card_id=card,detector=d);self.assertTrue(result['ok']);self.assertTrue(started.wait(3),'接好后排队的卡没有自动开始写方案')
  self.assertTrue(all(call[2]==card for call in calls))
 def test_empty_source_file_is_not_visible_before_deferred_flag(self):
  watcher=tasks.TaskWatcher(mock.Mock());original=Path.write_text
  def interrupted(path,data,*a,**kw):
   if path.name=='来源.json' and 'deferred_plan' in data:
    path.touch();watcher._tick_card(str(self.project),path.parent)
   return original(path,data,*a,**kw)
  with mock.patch.object(Path,'write_text',new=interrupted),mock.patch.object(tasks,'launch') as launch:
   tasks.create_card(str(self.project),'来源原子可见','做事','不改别的','做好',request_id='source-visible',start_plan=False)
  launch.assert_not_called()
