"""DSH 改版第 3 项：新界面的确认卡点“确认，建任务”→ 本机服务建卡并开始写方案。
和原来行政点确认建卡共用 clerk_tasks.create_card（不重复写一套）。"""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from desktop.chat import ChatHub  # noqa: E402
from desktop.clerk import tasks as clerk_tasks  # noqa: E402
from desktop.server import make_server  # noqa: E402

FIELDS = {"title": "待办清单加 clear 命令", "do": "加 clear 命令，清掉已完成的",
          "not_do": "不改其他命令", "done_when": "① clear 后已完成的没了 ② 没完成的还在"}


class DshCardNewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.server = make_server(port=0, token="口令", hub=ChatHub(factories={}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.launched = []
        patcher = mock.patch.object(clerk_tasks, "launch", side_effect=lambda *a: self.launched.append(a) or 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, body, token="口令"):
        headers = {"Content-Type": "application/json", "X-Chat-Token": urllib.request.quote(token)}
        request = urllib.request.Request(self.base + "/api/cards/new", data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=10)
            return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_creates_card_and_starts_plan(self):
        status, body = self.post({"project": str(self.project), **FIELDS})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        card = body["card"]
        self.assertTrue((self.project / ".collab" / "cards" / card).is_dir())
        # 建卡后立刻开始写方案（和行政点确认一致）。
        self.assertEqual(len(self.launched), 1)
        project, kind, card_id, args = self.launched[0]
        self.assertEqual((kind, card_id, args), ("plan", card, ["plan", str(self.project), card]))
        self.assertTrue(body["planning"])

    def test_missing_field_is_rejected_without_creating(self):
        status, body = self.post({"project": str(self.project), **{**FIELDS, "done_when": "  "}})
        self.assertEqual(status, 400)
        self.assertIn("验收标准", body["error"])
        self.assertFalse((self.project / ".collab" / "cards").exists() and any((self.project / ".collab" / "cards").iterdir()))
        self.assertEqual(self.launched, [])

    def test_needs_token(self):
        status, _ = self.post({"project": str(self.project), **FIELDS}, token="错的")
        self.assertEqual(status, 403)
        self.assertEqual(self.launched, [])

    def test_project_must_exist_and_be_git(self):
        status, body = self.post({"project": str(self.project / "不存在"), **FIELDS})
        self.assertEqual(status, 400)
        plain = Path(self.tmp.name) / "不是git"
        plain.mkdir()
        status, body = self.post({"project": str(plain), **FIELDS})
        self.assertEqual(status, 409)
        self.assertIn("git", body["error"])

    def test_same_request_id_makes_one_card(self):
        """网络断了重试同一张确认卡：只建一张（复查第 3 项中等 1）。"""
        body = {"project": str(self.project), "request_id": "确认卡-1", **FIELDS}
        s1, b1 = self.post(body)
        s2, b2 = self.post(body)
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(b1["card"], b2["card"])
        self.assertTrue(b2.get("repeat"))
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(len(list((self.project / ".collab" / "cards").iterdir())), 1)
        s3, b3 = self.post({**body, "request_id": "确认卡-2"})
        self.assertNotEqual(b3["card"], b1["card"])
        # 请求号记在卡自己的来源里：卡在、记录就在（不再单独记一份，记录写失败也不会重复建卡）。
        source = json.loads((self.project / ".collab" / "cards" / b1["card"] / "来源.json").read_text(encoding="utf-8"))
        self.assertEqual(source["request_id"], "确认卡-1")
        self.assertFalse((self.project / ".collab" / "dsh请求.json").exists())

    def test_half_written_card_is_rolled_back_and_retry_makes_one(self):
        """建卡写到一半失败（比如来源记录写不进去）：半张卡整个删掉，再点一次只建一张（复查第 3 项第 3 轮）。"""
        real_write = Path.write_text

        def flaky(path, *args, **kwargs):
            if path.name == "来源.json":
                raise OSError("磁盘写不进去")
            return real_write(path, *args, **kwargs)

        body = {"project": str(self.project), "request_id": "确认卡-半张", **FIELDS}
        with mock.patch.object(Path, "write_text", flaky):
            with self.assertRaises(OSError):
                clerk_tasks.create_card(str(self.project), FIELDS["title"], FIELDS["do"], FIELDS["not_do"],
                                        FIELDS["done_when"], request_id="确认卡-半张")
        cards = self.project / ".collab" / "cards"
        self.assertEqual([p.name for p in cards.iterdir()] if cards.exists() else [], [])
        status, b = self.post(body)
        self.assertEqual(status, 200)
        status2, b2 = self.post(body)
        self.assertEqual(b["card"], b2["card"])
        self.assertEqual(len(list(cards.iterdir())), 1)

    def test_plan_start_error_still_reports_the_card(self):
        with mock.patch.object(clerk_tasks, "launch", side_effect=OSError("起不来")):
            status, body = self.post({"project": str(self.project), "request_id": "r1", **FIELDS})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["planning"])
        self.assertIn("起不来", body["message"])
        self.assertTrue((self.project / ".collab" / "cards" / body["card"]).is_dir())

    def test_busy_planner_still_creates_card(self):
        with mock.patch.object(clerk_tasks, "launch", side_effect=clerk_tasks.TaskBusy("已有同类任务在跑")):
            status, body = self.post({"project": str(self.project), **FIELDS})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["planning"])


from acceptance_helpers import ScriptedClient, ServerCase, tool_call, wait_idle  # noqa: E402

TASK = {"title": "按钮改蓝色", "do": "首页按钮改蓝", "not_do": "不改别的页面", "done_when": "打开首页按钮是蓝的"}


class ClerkConfirmStartErrorTest(ServerCase):
    """抽出 create_card 后，行政点确认仍是“先记下已建卡，再开始写方案”（复查第 3 项严重 1）。"""

    def setUp(self):
        super().setUp()
        self.server.clerk = ScriptedClient([{"tool_calls": [tool_call("propose_task", TASK)]}, {"text": "你看行吗"}])
        self.cid = self.server.desk.send(self.project, "", "把按钮改蓝")
        events = wait_idle(self.server.desk, self.project, self.cid)
        self.pid = next(e["id"] for e in events if e["type"] == "proposal")

    def test_start_error_keeps_card_recorded_and_retry_makes_no_second_card(self):
        with mock.patch.object(clerk_tasks, "launch", side_effect=OSError("起不来")):
            with self.assertRaises(OSError):
                clerk_tasks.decide(self.project, self.cid, self.pid, "confirm")
        saved = clerk_tasks.load_proposal(self.project, self.pid)
        self.assertEqual((saved["state"], saved["card"]), ("confirmed", "CARD-001"))
        again = clerk_tasks.decide(self.project, self.cid, self.pid, "confirm")
        self.assertTrue(again["repeat"])
        self.assertEqual(sorted(p.name for p in (self.collab / "cards").iterdir()), ["CARD-001"])


if __name__ == "__main__":
    unittest.main()
