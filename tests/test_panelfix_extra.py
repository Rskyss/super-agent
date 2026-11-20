"""右栏显示小修的补充测试：只测施工单里的三个显示口径。"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import test_redfirst_acceptance as rf
from collab.agents import AgentResult
from collab.dispatch import _dispatch
from collab.flow import read_flow, record, text_of
from collab.judge import FAIL
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view


RED_WORDS = "写代码前先跑一遍新测试，确认它们现在真的不过"


def _station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


class RedCheckRecordsStart(rf.Base):
    def test_start_record_carries_reply_and_exact_words(self):
        self.verdicts = [FAIL]
        self.go()
        events = read_flow(self.cdir)
        starts = [e for e in events if e.get("kind") == "先红开始"]
        self.assertTrue(starts, "开始跑先红检查时要先记一条“先红开始”")
        start = starts[0]
        tester_reply = next(e for e in reversed(events)
                            if e.get("kind") == "回复" and e.get("src") == "测试员"
                            and e.get("step") == "写测试")
        self.assertEqual(start.get("src"), "程序")
        self.assertEqual(start.get("step"), "写测试")
        self.assertEqual(start.get("summary"), RED_WORDS)
        self.assertEqual(start.get("reply_to"), tester_reply.get("seq"))


class ViewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        self.collab = init_collab(self.project)

    def card(self, state="开发中", pid=None):
        worktree = self.project.parent / f"{self.project.name}-worktrees" / "CARD-001"
        worktree.mkdir(parents=True, exist_ok=True)
        cdir = self.collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "需求卡.md").write_text("# 改右栏显示\n", encoding="utf-8")
        Card("CARD-001", state=state).save(cdir / "状态.json")
        record(cdir, "需求", src="你", dst="调度员", summary="改右栏显示")
        record(cdir, "批准", src="你", dst="调度员", summary="批准方案，可以开工")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=pid or os.getpid(),
               tools={"测试员": "codex", "开发员": "codex", "复查员": "codex"})
        return cdir

    def mark_tested(self, cdir):
        sent = record(cdir, "派活", src="调度员", dst="测试员", tool="codex",
                      step="写测试", round=1, summary="写测试")
        record(cdir, "回复", src="测试员", dst="调度员", tool="codex", step="写测试",
               round=1, ok=True, summary="写好了", reply_to=sent["seq"])


class RedCheckView(ViewBase):
    def test_start_after_old_result_spins_and_counts_elapsed(self):
        cdir = self.card()
        self.mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        record(cdir, "先红检查", src="程序", step="写测试", result=FAIL,
               summary="上一轮测试没过")
        started = record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        now = datetime.fromtimestamp(started["ts"]) + timedelta(seconds=90)
        view = card_view(self.project, "CARD-001", now=now)
        check = _station(view, "check")
        self.assertEqual(check["state"], "active")
        self.assertTrue(check["live"])
        self.assertEqual(check["elapsed"], 90)
        self.assertIn("写代码前先跑一遍新测试", check["detail"])
        self.assertEqual(view["now"]["text"], "程序在写代码前先跑一遍新测试")
        self.assertEqual(view["now"]["tone"], "run")

    def test_busy_worker_takes_precedence_over_red_check_view(self):
        cdir = self.card()
        self.mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        sent = record(cdir, "派活", src="调度员", dst="开发员", tool="codex",
                      step="开发", round=1, summary="写代码", live=True)
        (cdir / sent["live"]).write_text("codex\n正在写代码\n", encoding="utf-8")
        view = card_view(self.project, "CARD-001")
        self.assertEqual(_station(view, "dev")["state"], "active")
        self.assertNotEqual(_station(view, "check")["state"], "active")
        self.assertNotIn("写代码前先跑一遍新测试", view["now"]["text"])

    def test_terminal_card_does_not_show_red_check_spinning(self):
        cdir = self.card(state="待验收")
        self.mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        record(cdir, "先红检查", src="程序", step="写测试", result=FAIL,
               summary="本卡测试没过")
        view = card_view(self.project, "CARD-001")
        self.assertNotEqual(_station(view, "check")["state"], "active")
        self.assertNotIn("写代码前先跑一遍新测试", view["now"]["text"])


class PlainDispatcherWords(ViewBase):
    def test_live_detail_and_title_line_both_replace_dispatcher(self):
        cdir = self.card()
        sent = record(cdir, "派活", src="调度员", dst="开发员", tool="codex",
                      step="开发", round=1, summary="写代码", live=True)
        (cdir / sent["live"]).write_text(
            "codex\n改完了，准备把结果汇报给调度员\n", encoding="utf-8")
        view = card_view(self.project, "CARD-001")
        self.assertEqual(_station(view, "dev")["detail"],
                         "改完了，准备把结果汇报给程序")
        self.assertEqual(view["now"]["detail"], "改完了，准备把结果汇报给程序")

    def test_chat_only_now_text_replaces_dispatcher(self):
        from desktop.chat import ChatSession

        class DummyAdapter:
            name = "Codex"
            thread_id = "thread-1"
            busy = True

            def start(self, opening, resume=False, thread_id=None):
                pass

            def send(self, text):
                pass

            def answer(self, ask_id, decision):
                pass

            def interrupt(self):
                pass

            def close(self):
                pass

        session = ChatSession(self.project, "codex", lambda p, emit, bg, m: DummyAdapter())
        session.adapter = DummyAdapter()
        session.emit({"type": "user", "text": "问一下"})
        session.emit({"type": "activity", "kind": "command",
                      "label": "运行 告诉调度员结果", "status": "started"})
        view = card_view(self.project, dispatcher="codex", chat_session=session)
        self.assertEqual(view["now"]["text"], "Codex 正在干活：运行 告诉程序结果…")


class HalfWidthReviewConclusion(ViewBase):
    def review(self, state, conclusion):
        cdir = self.card(state=state)
        sent = record(cdir, "派活", src="调度员", dst="复查员", tool="codex",
                      step="复查", round=1, summary="复查")
        record(cdir, "回复", src="复查员", dst="调度员", tool="codex",
               step="复查", round=1, ok=True, summary=conclusion,
               reply_to=sent["seq"])
        return card_view(self.project, "CARD-001")

    def test_half_width_fail_with_space_is_not_green(self):
        view = self.review("需Claude介入", "结论: 不通过")
        self.assertEqual(_station(view, "rev")["state"], "fail")

    def test_half_width_pass_is_green(self):
        view = self.review("待验收", "结论:通过")
        self.assertEqual(_station(view, "rev")["state"], "done")

    def test_full_width_fail_with_space_is_not_green(self):
        view = self.review("需Claude介入", "结论： 不通过")
        self.assertEqual(_station(view, "rev")["state"], "fail")


if __name__ == "__main__":
    unittest.main()
