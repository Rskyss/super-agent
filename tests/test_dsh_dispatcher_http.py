"""接入检测并发时不得把另一张任务卡误认成本卡。"""
import json, os, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock
from desktop.chat import ChatHub
from desktop import server as web

class DispatcherHttpTest(unittest.TestCase):
 def test_other_card_does_not_reuse_first_cards_connect_job(self):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder)/'blocks';home.mkdir();project=Path(folder)/'project';project.mkdir();entered=threading.Event();release=threading.Event()
   def connect(*a,**kw):entered.set();release.wait(3);return {'ok':True,'status':'ready','card':kw['card_id']}
   srv=web.make_server(port=0,token='test',hub=ChatHub(factories={}))
   srv.terminal_path=lambda:''
   threading.Thread(target=srv.serve_forever,daemon=True).start()
   def post(card):
    req=urllib.request.Request(f'http://127.0.0.1:{srv.server_address[1]}/api/dsh/dispatcher/connect',data=json.dumps({'project':str(project),'tool':'codex','card':card,'async':True}).encode(),headers={'Content-Type':'application/json','X-Chat-Token':'test'},method='POST')
    try:
     with urllib.request.urlopen(req,timeout=3) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
   try:
    with mock.patch.dict(os.environ,{'SUPER_AGENT_BLOCKS_HOME':str(home)}),mock.patch.object(web,'connect_dispatcher',side_effect=connect):
     s1,b1=post('CARD-001');self.assertEqual(s1,200);self.assertTrue(entered.wait(1));s2,b2=post('CARD-002')
     if s2==200:self.assertNotEqual(b1['job'],b2['job'],'另一张卡不能复用首卡的接入结果')
     else:self.assertEqual(s2,409);self.assertTrue(b2.get('error'))
     release.set()
   finally:release.set();srv.shutdown();srv.server_close()
