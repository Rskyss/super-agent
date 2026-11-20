"""小修卡“右栏显示”验收测试（Claude 写；施工方不许改，补充测试写进 tests/test_panelfix_extra*.py）。

依据 docs/跑卡流程调整/施工单-右栏显示小修.md：
P1 先红检查时“跑测试”格转圈 · P2 顶部和标题下那一行不露“调度员” · P3 复查结论半角冒号不通过不亮绿勾
（P4 待验收时写方案格：和“最终核验时写方案格转圈”的老口径冲突，撤出，仍记以后再修）
"""
import json
import os
import unittest
from pathlib import Path

import test_redfirst_acceptance as rf
from collab.flow import read_flow, record
from collab.judge import FAIL
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view
import tempfile

RED_WORDS = "写代码前先跑一遍新测试"


def _station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


class P1RecordsStart(rf.Base):
    def test_start_recorded_before_result(self):
        self.verdicts = [FAIL]
        self.go()
        kinds = [e.get("kind") for e in read_flow(self.cdir)]
        self.assertIn("先红开始", kinds, "开始跑先红检查时要记一条，右栏才知道在跑")
        self.assertLess(kinds.index("先红开始"), kinds.index("先红检查"))
        start = next(e for e in read_flow(self.cdir) if e.get("kind") == "先红开始")
        self.assertEqual(start.get("src"), "程序")
        self.assertIn(RED_WORDS, start.get("summary", ""))


class ViewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        self.collab = init_collab(self.project)

    def card(self, state="开发中", pid=None):
        Path(self.project).parent.joinpath(self.project.name + "-worktrees", "CARD-001").mkdir(parents=True, exist_ok=True)
        cdir = self.collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "需求卡.md").write_text("# 改一处显示\n", encoding="utf-8")
        Card("CARD-001", state=state).save(cdir / "状态.json")
        record(cdir, "需求", src="你", dst="调度员", summary="改一处显示")
        record(cdir, "批准", src="你", dst="调度员", summary="批准方案，可以开工")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=pid or os.getpid(),
               tools={"测试员": "codex", "开发员": "codex", "复查员": "codex"})
        return cdir

    def _mark_tested(self, cdir):
        sent = record(cdir, "派活", src="调度员", dst="测试员", tool="codex", step="写测试", round=1, summary="写测试")
        record(cdir, "回复", src="测试员", dst="调度员", tool="codex", step="写测试", round=1, ok=True,
               summary="写好了", reply_to=sent["seq"])


class P1View(ViewBase):
    def test_spins_while_red_check_runs(self):
        cdir = self.card()
        self._mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        view = card_view(self.project, "CARD-001")
        check = _station(view, "check")
        self.assertEqual(check["state"], "active", "先红检查在跑：跑测试格转圈")
        self.assertIn(RED_WORDS, check["detail"])
        self.assertIsNotNone(check.get("elapsed"), "转圈的格子要计时")
        self.assertIn(RED_WORDS, view["now"]["text"])
        self.assertNotIn("安排下一步", view["now"]["text"])

    def test_stops_after_result(self):
        cdir = self.card()
        self._mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        record(cdir, "先红检查", src="程序", step="写测试", result=FAIL, summary="还没改代码，本卡测试没过（正常）")
        view = card_view(self.project, "CARD-001")
        self.assertNotEqual(_station(view, "check")["state"], "active")
        self.assertNotIn(RED_WORDS, view["now"]["text"])

    def test_interrupted_does_not_spin(self):
        cdir = self.card(pid=999999)   # 不存在的进程：跑卡程序已经退出
        self._mark_tested(cdir)
        record(cdir, "先红开始", src="程序", step="写测试", summary=RED_WORDS)
        view = card_view(self.project, "CARD-001")
        self.assertNotEqual(_station(view, "check")["state"], "active", "程序已退出就不能还在转圈")


class P2NoDispatcherWord(ViewBase):
    def test_live_detail_has_no_dispatcher(self):
        cdir = self.card()
        sent = record(cdir, "派活", src="调度员", dst="开发员", tool="codex", step="开发", round=1,
                      summary="写代码", live=True)
        (cdir / sent["live"]).write_text("codex\n改完了，准备把结果汇报给调度员\n", encoding="utf-8")
        view = card_view(self.project, "CARD-001")
        self.assertNotIn("调度员", json.dumps(view["now"], ensure_ascii=False), "标题下那一行不露旧称")

    def test_chat_only_view_has_no_dispatcher(self):
        from desktop.chat import ChatSession

        class Dummy:
            name = "Codex"
            thread_id = "t1"
            busy = True
            def start(self, opening, resume=False, thread_id=None): pass
            def send(self, text): pass
            def answer(self, ask_id, decision): pass
            def interrupt(self): pass
            def close(self): pass

        session = ChatSession(str(self.project), "codex", lambda p, emit, bg, m: Dummy())
        session.adapter = Dummy()
        session.emit({"type": "user", "text": "问一下"})
        session.emit({"type": "activity", "kind": "command", "label": "运行 告诉调度员结果", "status": "started"})
        view = card_view(str(self.project), dispatcher="codex", chat_session=session)
        self.assertNotIn("调度员", json.dumps(view["now"], ensure_ascii=False), "没建卡时顶部也不露旧称")


class P3HalfWidthReject(ViewBase):
    def test_half_width_reject_then_full_fail_not_green(self):
        cdir = self.card(state="需Claude介入")
        sent = record(cdir, "派活", src="调度员", dst="复查员", tool="codex", step="复查", round=3, summary="复查")
        record(cdir, "回复", src="复查员", dst="调度员", tool="codex", step="复查", round=3, ok=True,
               summary="结论:不通过", reply_to=sent["seq"])
        record(cdir, "全量", src="程序", phase="结果", result=FAIL, summary="全部测试超时")
        record(cdir, "判定", src="程序", result=FAIL, summary="全部测试没过")
        record(cdir, "转人工", src="程序", dst="你", summary="全部测试没有全部通过（或没跑成），退回次数已用完，需要你看看")
        view = card_view(self.project, "CARD-001")
        self.assertNotEqual(_station(view, "rev")["state"], "done", "复查写的是不通过（半角冒号），不能亮绿勾")

    def test_full_width_pass_still_green(self):
        cdir = self.card(state="待验收")
        sent = record(cdir, "派活", src="调度员", dst="复查员", tool="codex", step="复查", round=1, summary="复查")
        record(cdir, "回复", src="复查员", dst="调度员", tool="codex", step="复查", round=1, ok=True,
               summary="结论:通过", reply_to=sent["seq"])
        view = card_view(self.project, "CARD-001")
        self.assertEqual(_station(view, "rev")["state"], "done")


if __name__ == "__main__":
    unittest.main()
