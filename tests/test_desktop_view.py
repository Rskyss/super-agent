"""右边流转面板的数据：从协作记录整理出“现在 / 流程各站 / 交接 / 卡片”。只读，不改任何文件。"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from collab.flow import record
from collab.judge import FAIL, PASS
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view, event_text, list_cards


def _station(view, key):
    return [s for s in view["stations"] if s["key"] == key][0]


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "视频项目"
        self.project.mkdir()
        self.collab = init_collab(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def _card(self, card_id="CARD-003", state="开发中", rework=0):
        cdir = self.collab / "cards" / card_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "需求卡.md").write_text("# 粘贴链接后显示处理进度\n", encoding="utf-8")
        Card(card_id, state=state, rework=rework).save(cdir / "状态.json")
        return cdir

    def _start(self, cdir):
        record(cdir, "需求", src="你", dst="调度员", summary="粘贴链接后显示处理进度")
        record(cdir, "批准", src="你", dst="调度员", summary="批准方案，可以开工")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工",
               tools={"测试员": "codex", "开发员": "cursor", "复查员": "agy"})

    def test_tester_at_work(self):
        cdir = self._card()
        self._start(cdir)
        sent = record(cdir, "派活", src="调度员", dst="测试员", tool="codex", step="写测试",
                      round=1, summary="按验收标准先写测试", live=True)
        (cdir / sent["live"]).write_text("读取需求卡\n新建测试文件\n写第 9 条测试\n", encoding="utf-8")
        view = card_view(self.project)
        self.assertEqual(view["card"]["id"], "CARD-003")
        self.assertEqual(view["card"]["title"], "粘贴链接后显示处理进度")
        # 开跑没记下进程时阶段是「中断了」，顶部跟阶段走（第二轮返工 R7）
        self.assertEqual(view["now"]["text"], "中断了")
        self.assertEqual(_station(view, "test")["state"], "active")
        self.assertEqual(_station(view, "test")["who"], "Codex")
        self.assertEqual(_station(view, "test")["detail"], "写第 9 条测试")
        self.assertTrue(_station(view, "test")["live"])
        self.assertEqual(_station(view, "dev")["state"], "idle")
        self.assertEqual(_station(view, "disp")["state"], "done")
        self.assertFalse(view["back"])
        self.assertFalse(view["stale"])

    def test_rework_after_failed_test(self):
        cdir = self._card(rework=1)
        self._start(cdir)
        a = record(cdir, "派活", src="调度员", dst="测试员", tool="codex", step="写测试", round=1, summary="写测试")
        record(cdir, "回复", src="测试员", dst="调度员", tool="codex", reply_to=a["seq"], ok=True,
               seconds=185, summary="写好 22 条测试")
        b = record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写代码")
        record(cdir, "回复", src="开发员", dst="调度员", tool="cursor", reply_to=b["seq"], ok=True,
               seconds=70, summary="汇报：新增进度步骤")
        record(cdir, "范围", src="程序", ok=True, summary="改动范围正常（3 个文件）")
        record(cdir, "判定", src="程序", dst="开发员", result=FAIL, round=1, summary="第 1 轮：测试没过（退出码 1）",
               text="2 条没过")
        record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="返工修改", round=2, summary="退回修改",
               live=True)
        view = card_view(self.project, "CARD-003")
        self.assertEqual(view["now"]["text"], "中断了")
        self.assertEqual(_station(view, "dev")["state"], "active")
        self.assertEqual(_station(view, "check")["state"], "fail")
        self.assertIn("没过", _station(view, "check")["detail"])
        self.assertEqual(_station(view, "test")["state"], "done")
        self.assertIn("写好 22 条测试", _station(view, "test")["detail"])
        self.assertTrue(view["back"])
        self.assertEqual(view["card"]["round"], 2)
        tones = {h["summary"]: h["tone"] for h in view["handoffs"]}
        self.assertEqual(tones["第 1 轮：测试没过（退出码 1）"], "bad")

    def test_tests_running(self):
        cdir = self._card(state="测试中")
        self._start(cdir)
        b = record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写代码")
        record(cdir, "回复", src="开发员", dst="调度员", tool="cursor", reply_to=b["seq"], ok=True, summary="汇报：好了")
        view = card_view(self.project)
        self.assertEqual(_station(view, "check")["state"], "active")
        self.assertEqual(view["now"]["text"], "中断了")

    def test_review_in_progress(self):
        cdir = self._card(state="测试中")
        self._start(cdir)
        record(cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")
        record(cdir, "派活", src="调度员", dst="复查员", tool="agy", step="复查", round=1, summary="只读复查")
        view = card_view(self.project)
        self.assertEqual(_station(view, "rev")["state"], "active")
        self.assertEqual(_station(view, "check")["state"], "done")
        self.assertEqual(view["now"]["text"], "中断了")

    def test_waiting_for_acceptance(self):
        cdir = self._card(state="待验收", rework=1)
        self._start(cdir)
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了，等你验收（返工 1 次）")
        view = card_view(self.project)
        self.assertEqual(_station(view, "accept")["state"], "wait")
        self.assertEqual(view["now"], {"text": "做完了，等你验收", "tone": "ok"})
        self.assertEqual(view["handoffs"][-1]["to"], "你")
        self.assertEqual(view["handoffs"][-1]["tone"], "ok")

    def test_escalation_shows_the_reason(self):
        cdir = self._card(state="需Claude介入")
        self._start(cdir)
        record(cdir, "转人工", src="程序", dst="调度员", summary="复查员（agy）复查不通过，需要调度员看原因")
        view = card_view(self.project)
        self.assertEqual(view["now"]["tone"], "bad")
        self.assertIn("复查不通过", view["now"]["text"])
        self.assertEqual(_station(view, "disp")["state"], "fail")

    def test_waiting_for_plan_approval(self):
        cdir = self._card(state="待对齐")
        record(cdir, "需求", src="你", dst="调度员", summary="粘贴链接后显示处理进度")
        view = card_view(self.project)
        self.assertEqual(_station(view, "disp")["state"], "active")
        self.assertIn("方案", view["now"]["text"])

    def test_old_card_without_flow_records_still_shows(self):
        self._card(state="开发中")
        view = card_view(self.project)
        self.assertEqual(_station(view, "dev")["state"], "active")
        self.assertEqual(view["handoffs"], [])
        self.assertEqual(view["now"]["text"], "开发中（这张卡没有流转记录，只能看到大致状态）")

    def test_quiet_for_more_than_ten_minutes_is_flagged(self):
        cdir = self._card()
        self._start(cdir)
        sent = record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1,
                      summary="写代码", live=True)
        old = time.time() - 11 * 60
        os.utime(cdir / sent["live"], (old, old))
        view = card_view(self.project, now=datetime.now())
        # 开跑没有进程时阶段已是中断；顶部跟阶段，不再叠“可能卡住”
        self.assertEqual(view["now"]["tone"], "bad")
        self.assertEqual(view["now"]["text"], "中断了")

    def test_dispatcher_shown_is_the_one_the_user_picked(self):
        cdir = self._card()
        self._start(cdir)
        codex = _station(card_view(self.project, dispatcher="codex"), "disp")
        self.assertEqual((codex["who"], codex["face"]), ("Codex", "Cx"))
        custom = _station(card_view(self.project, dispatcher="custom:qwen --yolo"), "disp")
        self.assertEqual(custom["who"], "qwen")
        # 没告诉面板是谁时，不乱猜，只写“调度员”
        self.assertEqual(_station(card_view(self.project), "disp")["who"], "调度员")

    def test_no_cards_yet(self):
        view = card_view(self.project)
        self.assertIsNone(view["card"])
        # 标题已经写着“还没有需求卡”，这一行说下一步该做什么
        self.assertEqual(view["now"]["text"], "等你提需求")

    def test_project_without_super_agent(self):
        bare = Path(self.tmp.name) / "没装的项目"
        bare.mkdir()
        view = card_view(bare)
        self.assertFalse(view["installed"])

    def test_cards_are_listed_newest_first(self):
        old = self._card("CARD-001", state="已合并")
        record(old, "需求", src="你", summary="旧的")
        os.utime(old / "状态.json", (time.time() - 3600, time.time() - 3600))
        os.utime(old / "流转.jsonl", (time.time() - 3600, time.time() - 3600))
        self._card("CARD-002", state="开发中")
        cards = list_cards(self.project)
        self.assertEqual([c["id"] for c in cards], ["CARD-002", "CARD-001"])
        self.assertEqual(cards[1]["state"], "已合并")
        # 不指定卡片时显示最近的一张
        self.assertEqual(card_view(self.project)["card"]["id"], "CARD-002")

    def test_full_text_by_sequence_number(self):
        cdir = self._card()
        ev = record(cdir, "派活", src="调度员", dst="测试员", summary="写测试", text="完整的派活原文")
        self.assertEqual(event_text(self.project, "CARD-003", ev["seq"]), "完整的派活原文")
        self.assertIsNone(event_text(self.project, "CARD-003", 999))
        self.assertIsNone(event_text(self.project, "../../etc", 1))

    def test_view_does_not_change_any_file(self):
        cdir = self._card()
        self._start(cdir)
        before = {p: p.stat().st_mtime for p in self.project.rglob("*") if p.is_file()}
        card_view(self.project)
        list_cards(self.project)
        after = {p: p.stat().st_mtime for p in self.project.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_a_chosen_card_ignores_what_the_dispatcher_is_doing_now(self):
        """主对话正在聊别的事，不能把调度员的动作画到一张选中的旧卡上。"""
        from desktop.chat import ChatSession

        class DummyDispatcher:
            name = "Codex"
            thread_id = "t1"
            busy = True
            def start(self, opening, resume=False): pass
            def send(self, text): pass
            def answer(self, ask_id, decision): pass
            def interrupt(self): pass
            def close(self): pass

        cdir = self.project / ".collab" / "cards" / "CARD-009"
        cdir.mkdir(parents=True)
        (cdir / "状态.json").write_text('{"card_id": "CARD-009", "state": "待验收", "rework": 0}', encoding="utf-8")
        (cdir / "需求卡.md").write_text("# 旧卡\n", encoding="utf-8")
        session = ChatSession(self.project, "codex", lambda p, emit, bg, m: DummyDispatcher())
        session.adapter = DummyDispatcher()
        session.emit({"type": "user", "text": "随便聊聊"})
        session.emit({"type": "activity", "kind": "command", "label": "运行 ls", "status": "started", "detail": "ls"})

        view = card_view(self.project, "CARD-009", dispatcher="codex", chat_session=session)
        self.assertEqual(view["card"]["id"], "CARD-009")
        self.assertEqual(_station(view, "disp")["state"], "done")
        self.assertNotIn("调度员", view["now"]["text"])

    def test_live_dispatcher_session_drives_panel_without_cards(self):
        from desktop.chat import ChatSession

        class DummyDispatcher:
            name = "Codex"
            thread_id = "t1"
            busy = True
            def start(self, opening, resume=False): pass
            def send(self, text): pass
            def answer(self, ask_id, decision): pass
            def interrupt(self): pass
            def close(self): pass

        session = ChatSession(self.project, "codex", lambda p, emit, bg, m: DummyDispatcher())
        session.adapter = DummyDispatcher()
        session.emit({"type": "user", "text": "你检查一下看看该项目有什么bug"})
        session.emit({"type": "activity", "kind": "command", "label": "运行 npm test", "status": "started", "detail": "npm test"})
        session.emit({"type": "message", "id": "m1", "text": "构建和现有的 142 项测试全部通过，正在检查页面行为。"})

        view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(view["card"]["id"], "调度中")
        self.assertEqual(view["card"]["title"], "你检查一下看看该项目有什么bug")
        self.assertEqual(_station(view, "you")["state"], "done")
        disp = _station(view, "disp")
        self.assertEqual(disp["state"], "active")
        self.assertTrue(disp["live"])
        self.assertIn("运行 npm test", disp["detail"])
        self.assertIn("调度员 Codex 正在干活：运行 npm test", view["now"]["text"])
        self.assertTrue(len(view["handoffs"]) >= 3)
        seq = view["handoffs"][-1]["seq"]
        self.assertIn("142 项测试", event_text(self.project, "调度中", seq, chat_session=session))

        session.adapter.busy = False
        session.emit({"type": "turn_done"})
        done_view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(_station(done_view, "disp")["state"], "wait")
        self.assertFalse(_station(done_view, "disp")["live"])

    def test_live_dispatcher_session_updates_aligning_card(self):
        from desktop.chat import ChatSession

        class DummyDispatcher:
            name = "Codex"
            thread_id = "t1"
            busy = True
            def start(self, opening, resume=False): pass
            def send(self, text): pass
            def answer(self, ask_id, decision): pass
            def interrupt(self): pass
            def close(self): pass

        cdir = self._card(state="待对齐")
        record(cdir, "需求", src="你", dst="调度员", summary="粘贴链接后显示处理进度")
        session = ChatSession(self.project, "codex", lambda p, emit, bg, m: DummyDispatcher())
        session.adapter = DummyDispatcher()
        session.emit({"type": "user", "text": "粘贴链接后显示处理进度"})
        session.emit({"type": "activity", "kind": "file", "label": "读了 2 个文件", "status": "done"})

        view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(view["card"]["id"], "CARD-003")
        disp = _station(view, "disp")
        self.assertEqual(disp["state"], "active")
        self.assertTrue(disp["live"])
        self.assertEqual(disp["detail"], "读了 2 个文件")
        self.assertIn("调度员 Codex 正在干活：读了 2 个文件", view["now"]["text"])

    def test_title_ignores_internal_worker_instructions_and_keeps_requirement_name(self):
        cdir = self._card("CARD-001", state="待验收")
        (cdir / "需求卡.md").write_text(
            "# 执行员务必先看：不要调用 Shell 工具\n本机 Cursor 会拒绝命令。\n\n# 三处显示问题修复 · 需求共识\n",
            encoding="utf-8",
        )
        record(cdir, "需求", src="你", dst="调度员", summary="三处显示问题修复 · 需求共识")
        view = card_view(self.project, "CARD-001")
        self.assertEqual(view["card"]["title"], "三处显示问题修复 · 需求共识")
        self.assertEqual(list_cards(self.project)[0]["title"], "三处显示问题修复 · 需求共识")

    def test_final_verification_by_dispatcher_keeps_panel_active_before_acceptance(self):
        from desktop.chat import ChatSession

        class DummyDispatcher:
            name = "Codex"
            thread_id = "t1"
            busy = True
            def start(self, opening, resume=False, thread_id=None): pass
            def send(self, text): pass
            def answer(self, ask_id, decision): pass
            def interrupt(self): pass
            def close(self): pass

        cdir = self._card(state="待验收")
        self._start(cdir)
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了，等你验收（返工 0 次）")

        session = ChatSession(self.project, "codex", lambda p, emit, bg, m: DummyDispatcher())
        session.adapter = DummyDispatcher()
        session.emit({"type": "user", "text": "那请帮我修复好"})
        session.emit({"type": "activity", "kind": "command", "label": "运行 npm --prefix grove run dev", "status": "started"})

        # 左边调度员还在开网页做最终核验时，右边不提前报“做完了”
        view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(_station(view, "disp")["state"], "active")
        self.assertTrue(_station(view, "disp")["live"])
        self.assertEqual(_station(view, "accept")["state"], "idle")
        self.assertIn("正在最终核验", view["now"]["text"])

        # 等调度员核验完毕停下来，右边才亮起“做完了，等你验收”
        session.adapter.busy = False
        session.emit({"type": "turn_done"})
        done_view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(_station(done_view, "disp")["state"], "done")
        self.assertEqual(_station(done_view, "accept")["state"], "wait")
        self.assertEqual(done_view["now"], {"text": "做完了，等你验收", "tone": "ok"})

    def test_interrupted_run_is_detected_when_process_exited(self):
        cdir = self._card(state="开发中")
        record(cdir, "需求", src="你", dst="调度员", summary="粘贴链接后显示处理进度")
        record(cdir, "批准", src="你", dst="调度员", summary="批准方案")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=999999)
        record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写代码")
        view = card_view(self.project)
        self.assertEqual(_station(view, "dev")["state"], "wait")
        self.assertFalse(_station(view, "dev")["live"])
        self.assertIn("中断", _station(view, "dev")["detail"])
        self.assertEqual(view["now"]["tone"], "bad")
        self.assertEqual(view["now"]["text"], "中断了")


class ServerTest(unittest.TestCase):
    def test_server_answers_health_view_and_page(self):
        from desktop.server import make_server
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            init_collab(project)
            server = make_server(port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{port}"
                health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=5).read())
                self.assertEqual(health, {"ok": True})
                q = urllib.parse.quote(str(project))
                view = json.loads(urllib.request.urlopen(f"{base}/api/view?project={q}", timeout=5).read())
                self.assertTrue(view["installed"])
                page = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
                self.assertIn("<!DOCTYPE html>", page)
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + "/static/../../collab/cli.py", timeout=5)
            finally:
                server.shutdown()
                server.server_close()

    def test_server_starts_without_looking_up_the_computer_name(self):
        """Python 3.15 在有的网络下查本机名字要 35 秒，窗口会一直停在“正在启动”。"""
        import socket
        from unittest import mock
        from desktop.server import make_server
        with mock.patch.object(socket, "getfqdn", side_effect=AssertionError("不该查本机名字")):
            server = make_server(port=0)
            server.server_close()

    def test_server_only_listens_on_this_computer(self):
        from desktop.server import make_server
        server = make_server(port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
