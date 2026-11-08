"""06 号单：Codex 只读写方案、卡片阶段、批准和不做了。"""
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, STATIC, SWIFT, ProjectCase, ServerCase, needs_node, run_node, wait_until
from collab.flow import read_flow, record
from collab.phase import card_phase
from collab.scope import read_allowed
from collab.state import Card, InvalidTransition
from collab.workspace import has_blanks
from desktop.clerk.tasks import TaskBusy, launch, running


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class PlanCommandTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(allowed=False)
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")

    def plan(self, **changes):
        os.environ["FAKE_PLAN"] = json.dumps(dict(GOOD_PLAN, **changes), ensure_ascii=False)
        return self.cli("plan", self.project, "CARD-001")

    def test_success_writes_everything(self):
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        self.assertIn("把按钮改成蓝色", plan)
        self.assertFalse(has_blanks(plan))
        need = (self.cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertEqual(read_allowed(need), ["src/app.py"])
        self.assertEqual(json.loads((self.cdir / "开工.json").read_text(encoding="utf-8")), {"test_command": "true"})
        self.assertIn("方案", self.flow_kinds())

    def test_codex_is_read_only_with_a_fixed_shape(self):
        self.plan()
        args = json.loads(self.codex_log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(args[0], "exec")
        self.assertEqual(args[args.index("-s") + 1], "read-only")
        for flag in ("--output-schema", "-o", "--skip-git-repo-check"):
            self.assertIn(flag, args)

    def test_project_files_are_not_touched_by_a_plan(self):
        self.plan()
        changed = subprocess.run(["git", "status", "--short", "--untracked-files=no"], cwd=self.project,
                                 capture_output=True, text=True).stdout
        self.assertEqual(changed, "")

    def test_questions_are_listed_and_no_question_says_none(self):
        self.plan(questions=["用蓝还是绿？推荐蓝"])
        self.assertIn("推荐蓝", (self.cdir / "方案.md").read_text(encoding="utf-8"))
        self.plan()
        self.assertIn("无", (self.cdir / "方案.md").read_text(encoding="utf-8"))

    def test_replanning_replaces_the_scope(self):
        self.plan()
        self.plan(allowed=["src/other.py"])
        self.assertEqual(read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8")), ["src/other.py"])

    def assert_failed(self, code):
        self.assertEqual(code, 2)
        self.assertEqual(self.flow_kinds()[-1], "方案失败")
        self.assertFalse((self.cdir / "开工.json").exists())

    def test_codex_failure(self):
        code, _ = self.cli("plan", self.project, "CARD-001")
        self.assert_failed(code)

    def test_unreadable_answer(self):
        os.environ["FAKE_PLAN"] = "这不是约定的格式"
        code, _ = self.cli("plan", self.project, "CARD-001")
        self.assert_failed(code)

    def test_unsafe_scope_is_refused(self):
        for bad in ("/etc/hosts", "../外面.py", "src/../../x", ".git/config", ".env", ".collab/配置.json", "desk/.env"):
            code, _ = self.plan(allowed=[bad])
            self.assert_failed(code)

    def test_missing_pieces_are_refused(self):
        for changes in ({"allowed": []}, {"summary": " "}, {"test_command": ""}):
            code, _ = self.plan(**changes)
            self.assert_failed(code)

    def test_new_project_gets_a_draft_profile(self):
        path = Path(self.project) / "docs" / "项目说明.md"
        path.unlink()
        code, _ = self.plan()
        self.assert_failed(code)
        code, _ = self.plan(profile="# 项目说明\n\n- 怎么测试：（待填）\n")
        self.assert_failed(code)
        code, _ = self.plan(profile="# 项目说明\n\n- 做视频\n")
        self.assertEqual(code, 0)
        self.assertIn("做视频", path.read_text(encoding="utf-8"))
        self.assertIs(next(e for e in read_flow(self.cdir) if e.get("kind") == "方案")["new_profile"], True)


class PhaseTest(ProjectCase):
    def phase(self, card="CARD-001"):
        return card_phase(self.project, card)

    def mark(self, kind, pid, card="CARD-001"):
        folder = self.collab / "后台"
        folder.mkdir(exist_ok=True)
        (folder / f"{kind}.json").write_text(json.dumps({"card": card, "pid": pid, "time": 0}), encoding="utf-8")

    def assert_phase(self, code, label, tone, needs, actions):
        got = self.phase()
        self.assertEqual((got["code"], got["label"], got["tone"], got["needs_you"], got["actions"]),
                         (code, label, tone, needs, actions))

    def test_waiting_for_a_plan(self):
        self.make_card(allowed=False)
        self.assert_phase("plan_waiting", "等待写方案", "run", False, [])

    def test_planning(self):
        self.make_card(allowed=False)
        self.mark("plan", os.getpid())
        self.assert_phase("planning", "写方案中", "run", False, [])

    def test_plan_lost(self):
        self.make_card(allowed=False)
        self.mark("plan", dead_pid())
        self.assert_phase("plan_failed", "方案没写成", "bad", True, ["replan", "discard"])

    def test_plan_failed_has_a_reason(self):
        cdir = self.make_card(allowed=False)
        record(cdir, "方案失败", src="程序", dst="你", summary="Codex 没有登录")
        self.assert_phase("plan_failed", "方案没写成", "bad", True, ["replan", "discard"])
        self.assertIn("没有登录", self.phase()["detail"])

    def test_plan_ready(self):
        self.make_card(plan=True)
        self.assert_phase("plan_ready", "等你批准方案", "wait", True, ["approve", "revise", "discard"])

    def test_good_plan_after_a_failure_counts(self):
        cdir = self.make_card(plan=True)
        record(cdir, "方案失败", src="程序", dst="你", summary="第一次失败")
        self.assertEqual(self.phase()["code"], "plan_failed")
        record(cdir, "方案", src="Codex", dst="你", summary="第二次成功")
        self.assertEqual(self.phase()["code"], "plan_ready")

    def test_ready(self):
        self.make_card(state="待派发", plan=True)
        self.assert_phase("ready", "准备开工", "run", False, [])

    def test_running_and_interrupted(self):
        for state in ("开发中", "待测试", "测试中"):
            cdir = self.make_card(state=state)
            record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=os.getpid())
            self.assert_phase("running", "进行中", "run", False, ["stop"])
            record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=dead_pid())
            self.assert_phase("interrupted", "中断了", "bad", True, ["resume", "discard"])

    def test_blocked_has_a_reason(self):
        cdir = self.make_card(state="需Claude介入")
        record(cdir, "转人工", src="程序", dst="调度员", summary="返工 3 次后测试仍没过")
        self.assert_phase("blocked", "卡住了", "bad", True, ["resume", "revise", "discard"])
        self.assertIn("测试仍没过", self.phase()["detail"])

    def test_finished_states(self):
        self.make_card(state="待验收")
        self.assertEqual(self.phase()["code"], "review")
        self.assertEqual(self.phase()["label"], "等你确认")
        self.assertTrue(self.phase()["needs_you"])
        self.make_card(state="已合并")
        self.assert_phase("done", "已完成", "done", False, [])
        self.make_card(state="已放弃")
        self.assert_phase("dropped", "不做了", "done", False, [])

    def test_old_card_without_records_still_works(self):
        cdir = self.make_card(state="待验收")
        (cdir / "流转.jsonl").unlink()
        self.assertEqual(self.phase()["code"], "review")


class DroppedStateTest(ProjectCase):
    def test_can_drop_from_these_states(self):
        for state in ("待对齐", "待派发", "待验收", "需Claude介入"):
            card = Card("C", state=state)
            card.transition("已放弃")
            self.assertEqual(card.state, "已放弃")

    def test_dropped_is_final_and_running_cards_cannot_drop(self):
        with self.assertRaises(InvalidTransition):
            Card("C", state="已放弃").transition("开发中")
        with self.assertRaises(InvalidTransition):
            Card("C", state="已合并").transition("已放弃")


class LaunchTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.make_card()

    def start(self):
        pid = launch(self.project, "plan", "CARD-001", ["watch", self.project, "CARD-001", "--interval", "1"])
        self.addCleanup(self.kill, pid)
        return pid

    def kill(self, pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass

    def test_runs_apart_and_records(self):
        pid = self.start()
        self.assertEqual(os.getsid(pid), pid)
        record_now = running(self.project, "plan")
        self.assertEqual((record_now["card"], record_now["pid"]), ("CARD-001", pid))
        wait_until((self.collab / "cards" / "CARD-001" / "后台输出.log").exists)

    def test_only_one_at_a_time(self):
        self.start()
        with self.assertRaises(TaskBusy):
            launch(self.project, "plan", "CARD-001", ["status", self.project])

    def test_simultaneous_launches(self):
        results = []

        def go():
            try:
                results.append(self.start())
            except TaskBusy:
                results.append(None)

        threads = [threading.Thread(target=go) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len([r for r in results if r]), 1)

    def test_finished_task_is_not_running_while_the_service_stays_open(self):
        pid = self.start()
        os.killpg(pid, signal.SIGKILL)
        wait_until(lambda: running(self.project, "plan") is None, 5, "结束的任务还被当成在跑")
        self.start()

    def test_different_kinds_do_not_block_each_other(self):
        self.start()
        pid = launch(self.project, "run", "CARD-001", ["watch", self.project, "CARD-001", "--interval", "1"])
        self.addCleanup(self.kill, pid)
        self.assertIsNotNone(running(self.project, "run"))


class ActionsTest(ServerCase):
    def act(self, action, card="CARD-001", token="口令"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": action}, token=token)

    def profile(self):
        folder = Path(self.project) / "docs"
        folder.mkdir(exist_ok=True)
        (folder / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")

    def test_needs_the_token(self):
        self.make_card(plan=True)
        status, _ = self.act("discard", token="错的")
        self.assertEqual(status, 403)
        self.assertEqual(self.state(), "待对齐")

    def test_action_must_fit_the_phase(self):
        self.make_card(plan=True)
        for action in ("resume", "stop", "replan", "乱写"):
            status, answer = self.act(action)
            self.assertEqual(status, 409, action)
            self.assertTrue(answer["error"])

    def test_approve_confirms_the_profile_and_moves_on(self):
        self.make_card(plan=True)
        self.profile()
        status, answer = self.act("approve")
        self.assertEqual(status, 200, answer)
        self.assertNotEqual(self.state(), "待对齐")
        self.assertIn("批准", self.flow_kinds())
        saved = json.loads((self.collab / "配置.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["project_profile_confirmed"])

    def test_approve_without_a_profile_changes_nothing(self):
        self.make_card(plan=True)
        status, answer = self.act("approve")
        self.assertEqual(status, 409)
        self.assertIn("项目说明", answer["error"])
        self.assertEqual(self.state(), "待对齐")

    def test_discard(self):
        self.make_card(plan=True)
        status, _ = self.act("discard")
        self.assertEqual(status, 200)
        self.assertEqual(self.state(), "已放弃")
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "dropped")
        self.assertIn("CARD-001", (Path(self.project) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8"))

    def test_replan(self):
        cdir = self.make_card(allowed=False)
        record(cdir, "方案失败", src="程序", dst="你", summary="失败了")
        self.profile()
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        status, answer = self.act("replan")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: card_phase(self.project, "CARD-001")["code"] == "plan_ready", 10, "方案没有写好")

    def test_plan_address(self):
        self.make_card(plan=True)
        status, answer = self.get("/api/plan", project=self.project, card="CARD-001")
        self.assertEqual(status, 200)
        self.assertIn("改颜色", answer["plan"])
        self.assertEqual(answer["allowed"], ["src/app.py"])
        self.assertEqual(answer["test_command"], "true")
        self.assertIs(answer["new_profile"], False)

    def test_view_carries_the_phase(self):
        self.make_card(plan=True)
        _, view = self.get("/api/view", project=self.project, card="CARD-001")
        self.assertEqual(view["card"]["phase"]["code"], "plan_ready")
        self.assertEqual(view["cards"][0]["phase"]["label"], "等你批准方案")
        _, cards = self.get("/api/cards", project=self.project)
        self.assertEqual(cards[0]["phase"]["code"], "plan_ready")


class PageTest(ProjectCase):
    def test_panel_action_bar(self):
        self.assertIn('id="actions"', (STATIC / "panel.html").read_text(encoding="utf-8"))
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        for words in ("/api/clerk/card", "/api/plan", "批准并开工", "放弃这个需求", "重写方案", "接着跑", "停下", "X-Chat-Token"):
            self.assertIn(words, js)

    def test_window_gives_the_panel_a_token(self):
        swift = SWIFT.read_text(encoding="utf-8")
        load = swift[swift.index("func loadPanel()"):swift.index("func startTerminal")]
        first = next(line for line in load.splitlines() if "&card=" in line)
        self.assertIn("token=", first)

    @needs_node()
    def test_left_column_reads_the_phase(self):
        proc = run_node("""
        const N = require('./desktop/static/nav.js');
        const mark = N.dotFor({ state: '待对齐', phase: { tone: 'bad', label: '方案没写成' } });
        if (mark.tone !== 'bad' || mark.label !== '方案没写成') throw new Error(JSON.stringify(mark));
        """)
        self.assertEqual(proc.returncode, 0, proc.stderr)
