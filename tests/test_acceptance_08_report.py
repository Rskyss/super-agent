"""08 号单：行政汇报和“等你处理”置顶。汇报用固定话，不调用模型。"""
import json
import os
import subprocess
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, STATIC, ProjectCase, ScriptedClient, needs_node, run_node, wait_idle, wait_until
from collab.flow import record
from collab.phase import card_phase
from desktop.clerk.desk import ClerkDesk
from desktop.clerk.tasks import TaskWatcher


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class WatchCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient([{"text": "你好"}])
        self.desk = ClerkDesk(lambda: self.client)
        self.cid = self.desk.send(self.project, "", "你好")
        wait_idle(self.desk, self.project, self.cid)
        self.calls = len(self.client.calls)
        self.watcher = TaskWatcher(self.desk)

    def said(self):
        events = self.desk.events(self.project, self.cid, 0, wait=0)["events"]
        return [e["text"] for e in events if e["type"] == "message" and e["text"] != "你好"]


class ReportTest(WatchCase):
    def test_plan_ready_is_reported_once(self):
        self.make_card(plan=True, source=self.cid, reported="planning", title="改按钮")
        self.watcher.tick(self.project)
        self.watcher.tick(self.project)
        self.assertEqual(self.said(), ["「改按钮」的方案写好了，点左边的卡片看方案，批准后才开工。"])
        self.assertEqual(len(self.client.calls), self.calls)

    def test_report_is_saved(self):
        self.make_card(plan=True, source=self.cid, reported="planning")
        self.watcher.tick(self.project)
        fresh = ClerkDesk(lambda: self.client)
        events = fresh.events(self.project, self.cid, 0, wait=0)["events"]
        self.assertTrue(any("方案写好了" in e.get("text", "") for e in events))

    def test_each_outcome_has_its_words(self):
        cdir = self.make_card(state="待验收", source=self.cid, reported="running", title="甲")
        self.watcher.tick(self.project)
        self.assertIn("「甲」做完了，等你确认。", self.said())
        cdir = self.make_card("CARD-002", state="需Claude介入", source=self.cid, reported="running", title="乙")
        record(cdir, "转人工", src="程序", dst="调度员", summary="测试一直不过")
        self.watcher.tick(self.project)
        self.assertIn("「乙」卡住了：测试一直不过。", self.said())
        cdir = self.make_card("CARD-003", allowed=False, source=self.cid, reported="planning", title="丙")
        record(cdir, "方案失败", src="程序", dst="你", summary="Codex 没有登录")
        self.watcher.tick(self.project)
        self.assertIn("「丙」的方案没写成：Codex 没有登录。可以点卡片重写。", self.said())

    def test_interrupted_is_reported_but_never_restarted(self):
        cdir = self.make_card(state="开发中", source=self.cid, reported="running", title="丁")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=dead_pid())
        self.watcher.tick(self.project)
        self.assertIn("「丁」中断了，可以点卡片接着跑。", self.said())
        self.assertEqual(self.state(), "开发中")
        self.assertFalse((self.collab / "后台" / "run.json").exists())

    def test_user_stop_is_not_reported(self):
        cdir = self.make_card(state="开发中", source=self.cid, reported="running")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=dead_pid())
        record(cdir, "停下", src="你", dst="程序", summary="用户停下")
        self.watcher.tick(self.project)
        self.assertEqual(self.said(), [])

    def test_old_card_is_not_reported(self):
        cdir = self.make_card(state="待验收", source=self.cid)
        self.watcher.tick(self.project)
        self.assertEqual(self.said(), [])
        self.assertEqual(json.loads((cdir / "已汇报.json").read_text(encoding="utf-8"))["code"], "review")

    def test_change_while_closed_is_caught_up(self):
        self.make_card(state="待验收", source=self.cid, reported="running", title="戊")
        TaskWatcher(ClerkDesk(lambda: self.client)).tick(self.project)
        fresh = ClerkDesk(lambda: self.client)
        self.assertTrue(any("「戊」做完了" in e.get("text", "")
                            for e in fresh.events(self.project, self.cid, 0, wait=0)["events"]))

    def test_missing_conversation_is_skipped(self):
        cdir = self.make_card(state="待验收", source="0" * 32, reported="running")
        self.watcher.tick(self.project)
        self.assertEqual(json.loads((cdir / "已汇报.json").read_text(encoding="utf-8"))["code"], "review")

    def test_broken_files_do_not_stop_the_round(self):
        bad = self.make_card("CARD-001", state="待验收", source=self.cid, reported="running")
        (bad / "已汇报.json").write_text("坏的", encoding="utf-8")
        self.make_card("CARD-002", state="待验收", source=self.cid, reported="running", title="好卡")
        self.watcher.tick(self.project)
        self.assertTrue(any("好卡" in text for text in self.said()))


class PlanStartTest(WatchCase):
    def test_waiting_card_from_the_clerk_gets_its_plan(self):
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n", encoding="utf-8")
        self.make_card(allowed=False, source=self.cid, reported="new")
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        self.watcher.tick(self.project)
        wait_until(lambda: card_phase(self.project, "CARD-001")["code"] == "plan_ready", 10, "没有写方案")

    def test_hand_made_card_never_spends_quota_by_itself(self):
        self.make_card(allowed=False)
        self.watcher.tick(self.project)
        self.assertFalse(self.codex_log.exists())


class LeftColumnTest(ProjectCase):
    def test_page_has_the_group(self):
        self.assertIn("等你处理", (STATIC / "nav.js").read_text(encoding="utf-8"))

    @needs_node()
    def test_needs_you_rule(self):
        proc = run_node("""
        const N = require('./desktop/static/nav.js');
        const cards = [
          { id: 'a', phase: { needs_you: true } }, { id: 'b', phase: { needs_you: false } }, { id: 'c', phase: { needs_you: true } },
        ];
        const ids = N.needsYou(cards).map(c => c.id).join();
        if (ids !== 'a,c') throw new Error(ids);
        if (N.needsYou([]).length !== 0) throw new Error('空列表');
        """)
        self.assertEqual(proc.returncode, 0, proc.stderr)
