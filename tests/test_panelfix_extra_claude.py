"""右栏显示小修：Claude 接手后补的测试（CARD-017 第 3 次复查第 1、2 条）。"""
import os
import tempfile
import unittest
from pathlib import Path

from collab.flow import record
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view


class ReplyRowMatchesStation(unittest.TestCase):
    def test_click_review_station_finds_reply(self):
        """点右栏某一格按名字筛交接：回复那一行的名字必须和格子上的名字对得上（不能一个“Codex”一个“codex”）。"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "项目"
            project.mkdir()
            collab = init_collab(project)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 改一处\n", encoding="utf-8")
            Card("CARD-001", state="测试中").save(cdir / "状态.json")
            record(cdir, "需求", src="你", dst="调度员", summary="改一处")
            record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
                   tools={"测试员": "qwen", "开发员": "qwen", "复查员": "codex"})
            for role, tool, step in (("测试员", "qwen", "写测试"), ("复查员", "codex", "复查")):
                sent = record(cdir, "派活", src="调度员", dst=role, tool=tool, step=step, round=1, summary=step)
                record(cdir, "回复", src=role, dst="调度员", tool=tool, step=step, round=1, ok=True,
                       summary="结论：通过" if role == "复查员" else "写好了", reply_to=sent["seq"])
            view = card_view(project, "CARD-001")
            for key, role in (("test", "测试员"), ("rev", "复查员")):
                who = next(s for s in view["stations"] if s["key"] == key)["who"]
                replies = [h for h in view["handoffs"] if h["kind"] == "回复" and role in h["from"]]
                self.assertTrue(replies)
                for h in replies:
                    self.assertIn(who, h["from"], f"{role} 的回复行要带格子上的名字“{who}”，点格子才筛得到")


class Round4(unittest.TestCase):
    """Claude 接手后 Codex 复查：加粗、引用写法的结论；追加要求后新一轮先红；接着跑前的旧先红。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "项目"
        self.project.mkdir()
        collab = init_collab(self.project)
        self.cdir = collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 改一处\n", encoding="utf-8")

    def start(self, state, pid=None):
        Card("CARD-001", state=state).save(self.cdir / "状态.json")
        record(self.cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=pid or os.getpid(),
               tools={"测试员": "qwen", "开发员": "qwen", "复查员": "codex"})

    def test_red_check_after_revision_beats_old_full_result(self):
        self.start("开发中")
        record(self.cdir, "全量", src="程序", phase="开始", summary="跑全部测试")
        record(self.cdir, "全量", src="程序", phase="结果", result="通过", summary="全部测试都过了")
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="再改一处")
        sent = record(self.cdir, "派活", src="调度员", dst="测试员", tool="qwen", step="补测试", round=2, summary="补测试")
        record(self.cdir, "回复", src="测试员", dst="调度员", tool="qwen", step="补测试", round=2, ok=True,
               summary="写好了", reply_to=sent["seq"])
        record(self.cdir, "先红开始", src="程序", step="补测试", summary="写代码前先跑一遍新测试")
        check = next(s for s in card_view(self.project, "CARD-001")["stations"] if s["key"] == "check")
        self.assertEqual(check["state"], "active", "新一轮先红在跑，不能被上一轮全量结果盖成绿勾")

    def test_old_red_start_before_resume_does_not_spin(self):
        self.start("开发中")
        record(self.cdir, "先红开始", src="程序", step="写测试", summary="写代码前先跑一遍新测试")
        self.start("开发中")   # 中断后接着跑：新进程只写了“开跑”
        check = next(s for s in card_view(self.project, "CARD-001")["stations"] if s["key"] == "check")
        self.assertNotEqual(check["state"], "active", "接着跑的新进程还没开始先红检查，不能拿旧记录转圈")


if __name__ == "__main__":
    unittest.main()
