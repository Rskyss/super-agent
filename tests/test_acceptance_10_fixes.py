"""第一批验收发现的问题。这些都是真实环境或走查里复现过的，改完才能进第二批。"""
import json
import os
import subprocess
import threading
import time
import unittest
from pathlib import Path

from acceptance_helpers import (GOOD_PLAN, ROOT, STATIC, SWIFT, ProjectCase, ScriptedClient, ServerCase, needs_node,
                                run_node, tool_call, wait_idle, wait_until, write_script)
from desktop.clerk.desk import ClerkDesk

CONV = "a" * 32

# 和真实服务一样严格：输出格式写得不全就拒绝
STRICT_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args, ensure_ascii=False) + "\n")
schema = json.load(open(args[args.index("--output-schema") + 1], encoding="utf-8"))
props = schema.get("properties")
if schema.get("type") != "object" or not props or schema.get("additionalProperties") is not False \
        or sorted(schema.get("required") or []) != sorted(props):
    print("Reading additional input from stdin...")
    print('ERROR: {"error":{"message":"Invalid schema for response_format"}}')
    sys.exit(1)
for name in ("steps", "questions", "allowed"):
    if props[name].get("type") != "array" or props[name].get("items", {}).get("type") != "string":
        print("ERROR: bad array"); sys.exit(1)
for name in ("summary", "result", "not_do", "test_command", "profile"):
    if props[name].get("type") != "string":
        print("ERROR: bad text"); sys.exit(1)
with open(args[args.index("-o") + 1], "w", encoding="utf-8") as fh:
    fh.write(os.environ["FAKE_PLAN"])
'''


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class Base(ProjectCase):
    def new_card(self):
        from collab.tasks import new_card
        return new_card(self.project, "改按钮", "改颜色", "不改别的", "变蓝", conversation=CONV)

    def plan_calls(self):
        if not self.codex_log.exists():
            return 0
        return sum("--output-schema" in line for line in self.codex_log.read_text(encoding="utf-8").splitlines())

    def phase(self, card="CARD-001"):
        from collab.phase import card_phase
        return card_phase(self.project, card)

    def plan(self, **changes):
        os.environ["FAKE_PLAN"] = json.dumps(dict(GOOD_PLAN, profile="# 项目说明\n正文", **changes), ensure_ascii=False)

    def last_flow(self, kind, card="CARD-001"):
        from collab.flow import read_flow
        found = [e for e in read_flow(self.collab / "cards" / card) if e.get("kind") == kind]
        return found[-1] if found else None

    def mark(self, kind, card, pid):
        folder = self.collab / "后台"
        folder.mkdir(exist_ok=True)
        (folder / f"{kind}.json").write_text(json.dumps({"card": card, "pid": pid, "time": 1}), encoding="utf-8")


class Said:
    def __init__(self):
        self.lines = []

    def notify(self, project, conversation, text):
        self.lines.append(text)
        return True


class PlanAnswer(Base):
    def setUp(self):
        super().setUp()
        codex = self.bin / "codex"
        codex.write_text(STRICT_CODEX, encoding="utf-8")
        codex.chmod(0o755)
        self.new_card()

    def test_real_service_accepts_the_output_format(self):
        self.plan()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.phase()["code"], "plan_ready")

    def test_answer_with_wrong_types_is_a_failure_not_a_crash(self):
        from collab.planner import plan_card
        write_script(self.bin / "codex", 'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                                         'printf \'%s\' "$FAKE_PLAN" > "$out"\n')
        self.plan(summary=123)
        self.assertEqual(plan_card(self.project, "CARD-001"), 2)
        self.assertEqual(self.phase()["code"], "plan_failed")

    def test_test_command_must_be_runnable(self):
        write_script(self.bin / "codex", 'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                                         'printf \'%s\' "$FAKE_PLAN" > "$out"\n')
        self.plan(test_command="python -c 'x")
        code, _ = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 2)
        self.assertEqual(self.phase()["code"], "plan_failed")

    def test_failure_reason_is_plain_words(self):
        write_script(self.bin / "codex", 'echo "Reading additional input from stdin..."\n'
                                         'echo \'ERROR: {"error":{"message":"bad"}}\'\nexit 1\n')
        self.plan()
        code, _ = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 2)
        summary = self.last_flow("方案失败")["summary"]
        self.assertTrue(summary)
        for machine_words in ("Reading", "ERROR", "{"):
            self.assertNotIn(machine_words, summary)


class PlanNeverLoops(Base):
    """写方案花真实额度：没写成就停，绝不自动再跑。"""

    def setUp(self):
        super().setUp()
        from desktop.clerk.tasks import TaskWatcher
        self.said = Said()
        self.watcher = TaskWatcher(self.said)

    def rounds(self, count=8):
        for _ in range(count):
            self.watcher.tick(self.project)
            time.sleep(0.25)

    def test_killed_planner_is_not_restarted(self):
        # 写方案程序被直接杀掉：来不及留下任何失败记录
        script = self.bin / "codex"
        script.write_text('#!/usr/bin/env python3\nimport json, os, signal, sys\n'
                          'open(os.environ["FAKE_CODEX_LOG"], "a").write(json.dumps(sys.argv) + "\\n")\n'
                          'os.kill(os.getppid(), signal.SIGKILL)\n', encoding="utf-8")
        script.chmod(0o755)
        self.new_card()
        self.rounds()
        self.assertEqual(self.plan_calls(), 1)
        self.assertEqual(self.phase()["code"], "plan_failed")
        self.assertEqual(self.phase()["code"], "plan_failed", "第二次看不能变样")

    def test_stale_record_stays_failed(self):
        self.make_card(allowed=False, source=CONV, reported="planning")
        self.mark("plan", "CARD-001", dead_pid())
        for _ in range(3):
            self.assertEqual(self.phase()["code"], "plan_failed")
        self.rounds(3)
        self.assertEqual(self.plan_calls(), 0)
        self.assertEqual(self.phase()["code"], "plan_failed")

    def test_plan_finished_while_closed_is_not_called_a_failure(self):
        self.make_card(plan=True, source=CONV, reported="planning", title="甲")
        self.mark("plan", "CARD-001", dead_pid())
        self.rounds(2)
        self.assertEqual(self.said.lines, ["「甲」的方案写好了，点左边的卡片看方案，批准后才开工。"])

    def test_waiting_card_still_starts_once(self):
        self.plan()
        self.make_card(allowed=False, source=CONV, reported="new")
        self.rounds(12)
        self.assertEqual(self.plan_calls(), 1)
        self.assertEqual(self.phase()["code"], "plan_ready")


class Actions(ServerCase, Base):
    def act(self, action, card="CARD-001"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": action})

    def ready_card(self, command="true"):
        cdir = self.make_card(plan=True, source=CONV, reported="plan_ready", title="甲")
        (cdir / "开工.json").write_text(json.dumps({"test_command": command}), encoding="utf-8")
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
        return cdir

    def block_start(self):
        blocker = Path(self.project + "-worktrees")
        blocker.write_text("这里本该是文件夹", encoding="utf-8")
        return blocker

    def test_failed_start_has_a_way_out(self):
        self.ready_card()
        blocker = self.block_start()
        self.assertEqual(self.act("approve")[0], 200)
        phase = wait_until(lambda: self.phase() if self.phase()["code"] == "start_failed" else None, 10,
                           "开工失败后没有显示出来")
        self.assertEqual((phase["label"], phase["tone"], phase["needs_you"], phase["actions"]),
                         ("开工失败", "bad", True, ["resume", "discard"]))
        blocker.unlink()
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() != "待派发", 10, "接着跑没有开工")

    def test_failed_start_is_reported_once(self):
        from desktop.clerk.tasks import TaskWatcher
        self.ready_card()
        self.block_start()
        self.act("approve")
        wait_until(lambda: self.phase()["code"] == "start_failed", 10)
        said = Said()
        watcher = TaskWatcher(said)
        watcher.tick(self.project)
        watcher.tick(self.project)
        self.assertEqual(said.lines, ["「甲」开工失败，可以点卡片接着跑，或者不做了。"])

    def test_user_stop_through_the_button_is_not_reported(self):
        from desktop.clerk.tasks import TaskWatcher
        write_script(self.bin / "codex", "sleep 60\n")
        self.ready_card()
        self.act("approve")
        wait_until(lambda: "开跑" in self.flow_kinds(), 8)
        time.sleep(0.5)
        self.assertEqual(self.act("stop")[0], 200)
        wait_until(lambda: self.phase()["code"] == "interrupted", 8)
        (self.collab / "cards" / "CARD-001" / "已汇报.json").write_text('{"code": "running"}', encoding="utf-8")
        said = Said()
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines, [])

    def test_discard_is_written_to_the_record(self):
        self.make_card(plan=True)
        self.assertEqual(self.act("discard")[0], 200)
        self.assertEqual(self.state(), "已放弃")
        self.assertIsNotNone(self.last_flow("放弃"))

    def test_replan_while_busy_says_so(self):
        cdir = self.make_card(allowed=False, source=CONV, reported="plan_failed")
        from collab.flow import record
        record(cdir, "方案失败", src="程序", dst="你", summary="没写成")
        self.mark("plan", "CARD-999", os.getpid())
        status, answer = self.act("replan")
        self.assertEqual(status, 409)
        self.assertIn("方案", answer["error"])

    def test_rejected_review_goes_back_to_the_developer(self):
        dev_log = Path(self.tmp) / "dev.log"
        count = Path(self.tmp) / "agy.count"
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{dev_log}"\necho 写好了\n')
        write_script(self.bin / "agy", f'echo x >> "{count}"\n'
                                       f'if [ "$(wc -l < "{count}")" -gt 1 ]; then echo 结论：通过; exit 0; fi\n'
                                       'echo 结论：不通过\necho 按钮颜色还是红的\n')
        self.ready_card()
        self.act("approve")
        wait_until(lambda: self.state() == "需Claude介入", 25, "复查没有打回")
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "接着跑后没有完成")
        rounds = dev_log.read_text(encoding="utf-8").split("\n===\n")
        self.assertEqual(len([r for r in rounds if r.strip()]), 2, "开发员要重新改一次")
        self.assertIn("按钮颜色还是红的", rounds[1])

    def test_review_without_verdict_only_reviews_again(self):
        dev_log = Path(self.tmp) / "dev.log"
        count = Path(self.tmp) / "agy.count"
        write_script(self.bin / "agent", f'echo x >> "{dev_log}"\necho 写好了\n')
        write_script(self.bin / "agy", f'echo x >> "{count}"\n'
                                       f'if [ "$(wc -l < "{count}")" -gt 1 ]; then echo 结论：通过; exit 0; fi\n'
                                       'echo 网络断了\n')
        self.ready_card()
        self.act("approve")
        wait_until(lambda: self.state() == "需Claude介入", 25)
        self.act("resume")
        wait_until(lambda: self.state() == "待验收", 25)
        self.assertEqual(len(dev_log.read_text(encoding="utf-8").splitlines()), 1)


class SmallThings(Base):
    def test_watch_stops_on_a_dropped_card(self):
        self.make_card(state="已放弃")
        code, _ = self.cli("watch", self.project, "CARD-001", timeout=8)
        self.assertEqual(code, 0)

    def test_finished_plan_does_not_look_busy(self):
        from desktop.view import card_view
        self.make_card(plan=True)
        view = card_view(self.project, "CARD-001")
        step = next(s for s in view["stations"] if s["key"] == "disp")
        self.assertEqual(step["state"], "wait")
        self.assertIn("等你批准", step["detail"])

    def test_one_liveness_check(self):
        source = (ROOT / "desktop" / "clerk" / "tasks.py").read_text(encoding="utf-8")
        self.assertNotIn("def _pid_alive", source)

    def test_no_workaround_for_starting_the_reader(self):
        source = (ROOT / "desktop" / "clerk" / "codex_history.py").read_text(encoding="utf-8")
        self.assertNotIn('getattr(reader', source)


class ToolLeftovers(Base):
    def test_helpers_left_by_a_finished_tool_are_cleaned_up(self):
        # 真实 Cursor 干完后会留下语言服务进程，一直占内存
        from collab.agents import watched_runner
        from collab.phase import process_is_alive
        pid_file = Path(self.tmp) / "helper.pid"
        tool = self.bin / "tool"
        write_script(tool, f'sleep 60 >/dev/null 2>&1 &\necho $! > "{pid_file}"\necho 干完了\n')
        code, out = watched_runner([str(tool)], self.project, 20, poll=0.1)
        self.assertEqual(code, 0)
        self.assertIn("干完了", out)
        helper = int(pid_file.read_text(encoding="utf-8"))
        wait_until(lambda: not process_is_alive(helper), 7, "工具留下的进程还活着")


class NoFalsePass(ServerCase, Base):
    """程序判定不能被骗：测试不能被删，测试命令要真跑，批准的方案要交到干活的人手里。"""

    def act(self, action):
        return self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": action})

    def start(self, command):
        cdir = self.make_card(plan=True, source=CONV, reported="plan_ready")
        (cdir / "开工.json").write_text(json.dumps({"test_command": command}), encoding="utf-8")
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
        self.assertEqual(self.act("approve")[0], 200)

    def finished(self):
        wait_until(lambda: self.state() in ("待验收", "需Claude介入"), 25, "施工没有结束")
        return self.state()

    def test_tests_written_by_the_tester_are_kept(self):
        # 允许范围只有 src/app.py，测试员新写的测试不能被当成越界删掉
        write_script(self.bin / "codex", 'mkdir -p tests\necho "新测试" > tests/test_new.py\necho 写好了\n')
        self.start("test -f tests/test_new.py")
        self.assertEqual(self.finished(), "待验收")
        worktree = Path(self.project + "-worktrees") / "CARD-001"
        self.assertTrue((worktree / "tests" / "test_new.py").exists())

    def test_developer_still_cannot_leave_the_scope(self):
        write_script(self.bin / "agent", 'echo 越界 > other.txt\necho 写好了\n')
        self.start("true")
        self.finished()
        self.assertFalse((Path(self.project + "-worktrees") / "CARD-001" / "other.txt").exists())

    def test_failing_second_half_of_a_command_is_a_failure(self):
        self.start("true && false")
        self.assertEqual(self.finished(), "需Claude介入")

    def test_command_with_cd_works(self):
        self.start("cd src && test -f app.py")
        self.assertEqual(self.finished(), "待验收")

    def test_approved_plan_reaches_everyone(self):
        said = Path(self.tmp) / "said.log"
        for name in ("codex", "agent", "agy"):
            write_script(self.bin / name, f'printf "{name}:%s\\n" "$*" >> "{said}"\n'
                                          + ("echo 结论：通过\n" if name == "agy" else "echo 写好了\n"))
        self.start("true")
        self.finished()
        lines = said.read_text(encoding="utf-8").splitlines()
        for name in ("codex", "agent", "agy"):
            self.assertTrue(any(l.startswith(name + ":") and "方案.md" in l for l in lines), name)


class ScopeRules(Base):
    def test_folders_work(self):
        from collab.scope import out_of_scope
        self.assertEqual(out_of_scope(["src/a.py", "src/b/c.py"], ["src/"]), [])
        self.assertEqual(out_of_scope(["src/a.py"], ["src"]), [])
        self.assertEqual(out_of_scope(["srcx/a.py"], ["src"]), ["srcx/a.py"])

    def test_secrets_are_never_in_scope(self):
        from collab.scope import out_of_scope
        for path in (".env", ".env.local", ".git/config", "web/.env"):
            self.assertEqual(out_of_scope([path], ["*", "**"]), [path])

    def test_plan_cannot_ask_for_everything(self):
        from collab.planner import _validate_answer
        for bad in ("*", "**", "*.env", ".git*", ".env.local", "web/.env"):
            ok, _ = _validate_answer(dict(GOOD_PLAN, allowed=[bad]), False)
            self.assertFalse(ok, bad)
        self.assertTrue(_validate_answer(dict(GOOD_PLAN, allowed=["src/*.py", "src/"]), False)[0])


class UserContentIsKept(ServerCase, Base):
    def fake_plan_writer(self):
        write_script(self.bin / "codex", 'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                                         'printf \'%s\' "$FAKE_PLAN" > "$out"\n')

    def profile(self, text):
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        path = docs / "项目说明.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_half_written_profile_is_not_replaced(self):
        self.fake_plan_writer()
        self.plan()
        path = self.profile("- 禁区：千万别动 payment/\n- 怎么启动：（待填）\n")
        self.new_card()
        self.assertEqual(self.cli("plan", self.project, "CARD-001")[0], 0)
        self.assertIn("千万别动 payment/", path.read_text(encoding="utf-8"))

    def test_blank_template_is_filled(self):
        from collab.workspace import PROFILE_TEMPLATE
        self.fake_plan_writer()
        self.plan()
        path = self.profile(PROFILE_TEMPLATE)
        self.new_card()
        self.assertEqual(self.cli("plan", self.project, "CARD-001")[0], 0)
        self.assertIn("正文", path.read_text(encoding="utf-8"))

    def test_broken_settings_are_not_wiped_by_a_team_change(self):
        from desktop.clerk import tasks as clerk_tasks
        self.make_card()
        config = self.collab / "配置.json"
        folder = self.collab / "行政提议"
        folder.mkdir(exist_ok=True)
        (folder / ("b" * 32 + ".json")).write_text(json.dumps({
            "id": "b" * 32, "conversation": CONV, "kind": "team", "role": "developer", "tool": "codex",
            "before": "cursor", "state": "open", "card": "", "time": 0}), encoding="utf-8")
        config.write_text("坏的设置，但用户还能手工修", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            clerk_tasks.decide(self.project, CONV, "b" * 32, "confirm")
        self.assertEqual(config.read_text(encoding="utf-8"), "坏的设置，但用户还能手工修")

    def test_card_numbers_are_checked_at_the_door(self):
        status, _ = self.get("/api/plan", project=self.project, card="../../外面")
        self.assertEqual(status, 400)
        status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "../外面", "action": "discard"})
        self.assertEqual(status, 400)


class StopAndAlive(ServerCase, Base):
    def act(self, action, card="CARD-001"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": action})

    def spawn(self):
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def start(self):
        self.make_card(plan=True, source=CONV, reported="plan_ready")
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
        self.assertEqual(self.act("approve")[0], 200)

    def test_pressing_stop_twice_leaves_nothing_running(self):
        beat = Path(self.tmp) / "beat"
        write_script(self.bin / "codex", f"trap '' TERM\nwhile :; do date +%s%N >> \"{beat}\"; sleep 0.2; done\n")
        self.start()
        wait_until(beat.exists, 8, "工具没有开始")
        threads = [threading.Thread(target=self.act, args=("stop",)) for _ in range(2)]
        threads[0].start()
        time.sleep(1)
        threads[1].start()
        for thread in threads:
            thread.join(15)
        time.sleep(1)
        size = beat.stat().st_size
        time.sleep(1)
        self.assertEqual(beat.stat().st_size, size, "工具还在跑")

    def test_stop_never_touches_another_card(self):
        from collab.flow import record
        other = self.spawn()
        mine = self.spawn()
        cdir = self.make_card(state="开发中")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=mine.pid)
        self.mark("run", "CARD-002", other.pid)
        status, answer = self.act("stop")
        self.assertEqual(status, 409)
        self.assertIn("终端", answer["error"])
        self.assertIsNone(other.poll())

    def test_reused_process_number_is_not_our_task(self):
        from collab.flow import record
        from desktop.clerk.tasks import running
        cdir = self.make_card(state="开发中")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=os.getpid(), started="很久以前")
        self.assertEqual(self.phase()["code"], "interrupted")
        folder = self.collab / "后台"
        folder.mkdir(exist_ok=True)
        (folder / "plan.json").write_text(json.dumps({"card": "CARD-001", "pid": os.getpid(), "started": "很久以前"}),
                                          encoding="utf-8")
        self.assertIsNone(running(self.project, "plan"))

    def test_launch_writes_down_who_it_started(self):
        from collab.phase import process_start
        write_script(self.bin / "codex", "sleep 30\n")
        self.start()
        data = wait_until(lambda: (self.collab / "后台" / "run.json").exists()
                          and json.loads((self.collab / "后台" / "run.json").read_text(encoding="utf-8")), 5)
        self.assertEqual(data["started"], process_start(data["pid"]))
        self.act("stop")

    def test_background_tools_do_not_get_the_window_password(self):
        seen = Path(self.tmp) / "seen"
        write_script(self.bin / "agent", f'echo "[$SUPERAGENT_CHAT_TOKEN]" > "{seen}"\necho 写好了\n')
        os.environ["SUPERAGENT_CHAT_TOKEN"] = "窗口口令"
        self.start()
        wait_until(seen.exists, 15, "开发员没有开始")
        self.assertEqual(seen.read_text(encoding="utf-8").strip(), "[]")


class Tough(Base):
    def test_state_file_is_never_half_written(self):
        source = (ROOT / "collab" / "state.py").read_text(encoding="utf-8")
        self.assertIn(".replace(", source)

    def test_one_broken_card_does_not_hide_the_others(self):
        from desktop.clerk.tasks import TaskWatcher
        from desktop.view import list_cards
        self.make_card("CARD-001")
        (self.collab / "cards" / "CARD-001" / "状态.json").write_text("{坏", encoding="utf-8")
        self.make_card("CARD-002", state="待验收", source=CONV, reported="running", title="好卡")
        said = Said()
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines, ["「好卡」做完了，等你验收。"])
        self.assertEqual([c["id"] for c in list_cards(self.project)], ["CARD-002"])

    def test_empty_folder_does_not_block_new_cards(self):
        self.make_card("CARD-001")
        (self.collab / "cards" / "CARD-002").mkdir()
        self.assertEqual(self.new_card(), "CARD-003")


class ClerkAnswers(Base):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient()
        self.desk = ClerkDesk(lambda: self.client)

    def test_progress_uses_the_same_words_as_the_page(self):
        from collab.flow import record
        from desktop.clerk.tools import run_tool
        cdir = self.make_card(state="开发中")
        proc = subprocess.Popen(["true"])
        proc.wait()
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=proc.pid)
        self.assertIn("中断了", run_tool(self.project, "list_cards", {}))
        self.assertIn("中断了", run_tool(self.project, "read_card", {"card_id": "CARD-001"}))

    def test_installed_tools_do_not_reveal_folders(self):
        from desktop.clerk.tools import run_tool
        self.assertNotIn(str(self.bin), run_tool(self.project, "list_installed_tools", {}))

    def test_words_from_every_round_are_kept(self):
        self.client.steps = [{"text": "我先查一下。", "tool_calls": [tool_call("list_cards")]}, {"text": "现在没有卡片。"}]
        cid = self.desk.send(self.project, "", "进度？")
        wait_idle(self.desk, self.project, cid)
        fresh = ClerkDesk(lambda: self.client)
        words = " ".join(e.get("text", "") for e in fresh.events(self.project, cid, 0, wait=0)["events"])
        self.assertIn("我先查一下", words)
        self.assertIn("现在没有卡片", words)

    def test_empty_answer_tells_the_user(self):
        self.client.steps = [{"text": ""}]
        cid = self.desk.send(self.project, "", "你好")
        events = wait_idle(self.desk, self.project, cid)
        self.assertTrue(any("没有得到回复" in (e.get("text") or e.get("message") or "") for e in events))


class PageWords(Base):
    def test_top_line_matches_the_phase(self):
        from collab.flow import record
        from desktop.view import card_view
        self.make_card("CARD-001", state="已放弃")
        self.assertEqual(card_view(self.project, "CARD-001")["now"]["text"], "不做了")
        cdir = self.make_card("CARD-002", allowed=False)
        record(cdir, "方案失败", src="程序", dst="你", summary="没写成")
        self.assertIn("方案没写成", card_view(self.project, "CARD-002")["now"]["text"])

    def test_going_back_to_the_main_chat_leaves_the_read_only_page(self):
        swift = SWIFT.read_text(encoding="utf-8")
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("closeCodexHistory", swift)
        self.assertIn("closeCodexHistory", chat)

    def test_panel_is_told_whether_the_clerk_is_on(self):
        swift = SWIFT.read_text(encoding="utf-8")
        line = next(l for l in swift.splitlines() if "/?\\(base)&card=" in l)
        self.assertIn("clerk=", line)


class Window(unittest.TestCase):
    def test_confirm_and_alert_work_in_the_real_window(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("WKUIDelegate", source)
        self.assertIn("runJavaScriptConfirmPanelWithMessage", source)
        self.assertIn("runJavaScriptAlertPanelWithMessage", source)
        self.assertIn("web.uiDelegate = self", source)
        self.assertNotIn("runModal", source)


@needs_node()
class Pages(unittest.TestCase):
    def check(self, body):
        script = ("const assert = require('assert');"
                  f"const N = require({json.dumps(str(STATIC / 'nav.js'))});"
                  f"const P = require({json.dumps(str(STATIC / 'panel.js'))});" + body)
        done = run_node(script)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_buttons_only_come_from_the_phase(self):
        self.check("""
          const keys = (a) => P.actionButtons({ actions: a }, true).map((b) => b.key);
          assert.deepStrictEqual(keys(['approve', 'discard']), ['approve', 'discard']);
          assert.deepStrictEqual(keys(['replan', 'discard']), ['replan', 'discard']);
          assert.deepStrictEqual(keys([]), []);
          assert.deepStrictEqual(keys(['看不懂的动作']), []);
          const labels = P.actionButtons({ actions: ['approve', 'discard', 'replan', 'resume', 'stop'] }, true)
            .map((b) => b.label);
          assert.deepStrictEqual(labels, ['批准并开工', '不做了', '重写方案', '接着跑', '停下']);
          assert.deepStrictEqual(P.actionButtons({ actions: ['approve'] }, false), []);
        """)

    def test_card_list_does_not_say_nothing_found_when_all_are_on_top(self):
        self.check("""
          const c = [{ id: 'A' }];
          assert.strictEqual(N.emptyText([], [], []), '还没有卡片');
          assert.strictEqual(N.emptyText(c, [], []), '没有找到');
          assert.strictEqual(N.emptyText(c, c, []), '');
        """)

    def test_card_mark_comes_only_from_the_phase(self):
        self.check("""
          assert.deepStrictEqual(N.dotFor({ state: '待验收', phase: { tone: 'ok', label: '等你验收' } }),
                                 { tone: 'ok', label: '等你验收' });
          assert.deepStrictEqual(N.dotFor({ state: '待验收' }), { tone: 'done', label: '待验收' });
        """)
