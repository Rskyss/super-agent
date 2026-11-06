"""07 号单：后台开工、接着跑、停下，退出软件不影响施工。"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from acceptance_helpers import ServerCase, wait_until, write_script
from collab.flow import record
from collab.phase import card_phase
from desktop.clerk.tasks import running


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class RunCase(ServerCase):
    def setUp(self):
        super().setUp()
        docs = Path(self.project) / "docs"
        docs.mkdir()
        (docs / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")
        self.addCleanup(self.stop_everything)

    def stop_everything(self):
        pids = []
        path = self.collab / "后台" / "run.json"
        if path.exists():
            pids.append(json.loads(path.read_text(encoding="utf-8"))["pid"])
        tool = Path(self.tmp) / "tool.pid"
        if tool.exists() and tool.read_text().strip():
            pids.append(int(tool.read_text()))
        for pid in pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass

    def act(self, action, card="CARD-001"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": action})

    def slow_developer(self):
        self.tool_pid = Path(self.tmp) / "tool.pid"
        write_script(self.bin / "agent", f'echo $$ > "{self.tool_pid}"\nexec sleep 60\n')

    def tool(self):
        wait_until(lambda: self.tool_pid.exists() and self.tool_pid.read_text().strip(), 15, "开发员没有开工")
        return int(self.tool_pid.read_text())


class CliTest(RunCase):
    def test_blocked_card_needs_the_flag(self):
        self.make_card(state="需Claude介入", plan=True)
        code, _ = self.cli("run", self.project, "CARD-001", "--test-command", "true")
        self.assertEqual(code, 1)
        self.assertEqual(self.state(), "需Claude介入")

    def test_flag_carries_on(self):
        self.make_card(state="需Claude介入", plan=True)
        code, out = self.cli("run", self.project, "CARD-001", "--test-command", "true", "--resume-blocked")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state(), "待验收")

    def test_flag_does_not_start_an_unapproved_card(self):
        self.make_card(state="待对齐", plan=True)
        code, _ = self.cli("run", self.project, "CARD-001", "--test-command", "true", "--resume-blocked")
        self.assertEqual(code, 1)
        self.assertEqual(self.state(), "待对齐")


class ApproveTest(RunCase):
    def test_approve_builds_in_the_background(self):
        self.make_card(plan=True)
        status, answer = self.act("approve")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: self.state() == "待验收", 20, "没有施工完")
        self.assertIn("开跑", self.flow_kinds())

    def test_second_card_is_refused_and_keeps_its_place(self):
        self.make_card(plan=True)
        folder = self.collab / "后台"
        folder.mkdir()
        (folder / "run.json").write_text(json.dumps({"card": "CARD-009", "pid": os.getpid(), "time": 0}), encoding="utf-8")
        status, answer = self.act("approve")
        self.assertEqual(status, 409)
        self.assertIn("已有一张卡在施工", answer["error"])
        self.assertEqual(self.state(), "待对齐")
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "plan_ready")
        self.assertNotIn("批准", self.flow_kinds())
        (folder / "run.json").unlink()


class ResumeTest(RunCase):
    def test_interrupted_card_carries_on_only_when_asked(self):
        cdir = self.make_card(state="开发中", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=dead_pid())
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "interrupted")
        time.sleep(0.3)
        self.assertEqual(self.state(), "开发中")
        status, answer = self.act("resume")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: self.state() == "待验收", 20, "没有接着跑完")

    def test_blocked_card_carries_on(self):
        self.make_card(state="需Claude介入", plan=True)
        status, answer = self.act("resume")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: self.state() == "待验收", 20, "没有接着跑完")


class StopTest(RunCase):
    def test_stop_really_stops_the_tool(self):
        self.make_card(plan=True)
        self.slow_developer()
        self.act("approve")
        tool = self.tool()
        run = running(self.project, "run")["pid"]
        status, answer = self.act("stop")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: not alive(tool), 10, "开发员没有被停住")
        wait_until(lambda: running(self.project, "run") is None, 10, "施工没有结束")
        self.assertFalse(alive(run))
        self.assertIn("停下", self.flow_kinds())
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "interrupted")


class QuitTest(RunCase):
    def test_closing_the_service_does_not_stop_the_build(self):
        self.make_card(plan=True)
        self.slow_developer()
        self.act("approve")
        tool = self.tool()
        run = running(self.project, "run")["pid"]
        self.close_server()
        time.sleep(0.5)
        self.assertTrue(alive(tool))
        self.assertTrue(alive(run))
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "running")
