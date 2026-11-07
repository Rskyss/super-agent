"""返工验收发现的问题：判定还有口子，以及窗口、停下的收尾。"""
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from acceptance_helpers import ROOT, GOOD_PLAN, ScriptedClient, ServerCase, tool_call, wait_idle, wait_until, write_script
from collab.config import ConfigError, load_team
from collab.flow import read_flow, record
from desktop.clerk.client import ModelError
from desktop.clerk.desk import ClerkDesk
from desktop.clerk.tasks import TaskBusy
from desktop.view import card_view
from test_acceptance_10_fixes import CONV, Base


class Case(ServerCase, Base):
    def act(self, action, card="CARD-001"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": action})

    def ready(self, command="true"):
        cdir = self.make_card(plan=True, source=CONV, reported="plan_ready")
        (cdir / "开工.json").write_text(json.dumps({"test_command": command}), encoding="utf-8")
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
        return cdir

    def start(self, command="true"):
        self.ready(command)
        self.assertEqual(self.act("approve")[0], 200)

    def finished(self):
        wait_until(lambda: self.state() in ("待验收", "需Claude介入"), 25, "施工没有结束")
        return self.state()

    @property
    def worktree(self):
        return Path(self.project + "-worktrees") / "CARD-001"

    def counted(self, name, *answers):
        """第 N 次调用走第 N 段脚本，用完后重复最后一段。"""
        count = Path(self.tmp) / f"{name}.count"
        body = f'echo x >> "{count}"\nn=$(wc -l < "{count}")\n'
        for index, answer in enumerate(answers[:-1], 1):
            body += f'if [ "$n" -eq {index} ]; then\n{answer}\nexit $?\nfi\n'
        write_script(self.bin / name, body + answers[-1] + "\n")


class PipesCannotHideFailures(Case):
    def test_failed_test_before_a_pipe_is_a_failure(self):
        self.start("false | cat")
        self.assertEqual(self.finished(), "需Claude介入")

    def test_missing_command_before_a_pipe_is_an_environment_problem(self):
        self.start("不存在的命令 2>/dev/null | cat")
        self.assertEqual(self.finished(), "需Claude介入")
        results = [e.get("result") for e in read_flow(self.collab / "cards" / "CARD-001") if e.get("kind") == "判定"]
        self.assertEqual(results[-1], "环境错误")


class TestsCannotBeRemoved(Case):
    def tester_writes(self):
        write_script(self.bin / "codex", 'mkdir -p tests\necho 真测试 > tests/test_new.py\necho 写好了\n')

    def test_developer_cannot_delete_tests(self):
        self.tester_writes()
        write_script(self.bin / "agent", 'rm -f tests/test_new.py\necho 写好了\n')
        self.start("test -f tests/test_new.py")
        self.assertEqual(self.finished(), "待验收")
        self.assertEqual((self.worktree / "tests" / "test_new.py").read_text(encoding="utf-8").strip(), "真测试")

    def test_developer_cannot_empty_tests_and_it_is_written_down(self):
        self.tester_writes()
        write_script(self.bin / "agent", ': > tests/test_new.py\necho 写好了\n')
        self.start("grep -q 真测试 tests/test_new.py")
        self.assertEqual(self.finished(), "待验收")
        notes = [e.get("summary") or "" for e in read_flow(self.collab / "cards" / "CARD-001")
                 if e.get("kind") == "范围"]
        self.assertTrue(any("恢复" in note and "tests/test_new.py" in note for note in notes), notes)

    def test_new_test_folder_is_not_a_free_pass(self):
        write_script(self.bin / "codex", 'mkdir e2e\necho 真测试 > e2e/a.txt\necho 写好了\n')
        write_script(self.bin / "agent", 'echo 越界 > e2e/b.txt\necho 写好了\n')
        self.start()
        self.finished()
        self.assertTrue((self.worktree / "e2e" / "a.txt").exists())
        self.assertFalse((self.worktree / "e2e" / "b.txt").exists())

    def test_tests_survive_when_the_tester_fails_halfway(self):
        # 工具失败会自动重试一次，所以要连续失败才算测试员没交活
        broken = Path(self.tmp) / "broken"
        broken.write_text("测试员这次会失败", encoding="utf-8")
        write_script(self.bin / "codex", f'mkdir -p tests\necho 真测试 > tests/test_new.py\n'
                                         f'[ -f "{broken}" ] && exit 1\necho 写好了\n')
        self.start("test -f tests/test_new.py")
        self.assertEqual(self.finished(), "需Claude介入")
        broken.unlink()
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "接着跑后没有完成")
        self.assertTrue((self.worktree / "tests" / "test_new.py").exists())

    def test_file_names_with_brackets(self):
        from collab.scope import out_of_scope
        self.assertEqual(out_of_scope(["tests/[id].test.ts"], ["tests/[id].test.ts"]), [])


class TesterMustDeliver(Case):
    def test_tester_writing_nothing_stops_the_card(self):
        # 真实 Codex 拒绝动手时只回一句话，退出状态却是成功
        dev_log = Path(self.tmp) / "dev.log"
        write_script(self.bin / "agent", f'echo x >> "{dev_log}"\necho 写好了\n')
        write_script(self.bin / "codex", 'echo 请确认是否解除限制\n')
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        self.assertIn("没有写出任何测试", self.phase()["detail"])
        self.assertFalse(dev_log.exists(), "开发员不该开工")

    def test_carrying_on_asks_the_tester_again(self):
        write_script(self.bin / "codex", 'echo 请确认是否解除限制\n')
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        write_script(self.bin / "codex", 'echo 真测试 > test_new.py\necho 写好了\n')
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "接着跑后没有完成")
        self.assertTrue((self.worktree / "test_new.py").exists())

    def test_read_only_rule_of_planning_does_not_reach_the_workers(self):
        from collab.planner import _build_prompt
        self.assertIn("写给后面干活的人", _build_prompt(Path(self.tmp), "需求", False))
        said = Path(self.tmp) / "said.log"
        write_script(self.bin / "codex", f'printf "%s" "$*" >> "{said}"\necho 真测试 > test_new.py\necho 写好了\n')
        self.start()
        self.finished()
        self.assertIn("写方案这一步", said.read_text(encoding="utf-8"))


class FailedToolsAreRetried(Case):
    def test_a_failed_tool_gets_one_more_try(self):
        from collab.agents import ask
        calls = []

        def flaky(command, cwd, timeout):
            calls.append(command)
            return (1, "网络断了") if len(calls) == 1 else (0, "好了")

        self.assertTrue(ask("codex", "做事", self.project, runner=flaky).ok)
        self.assertEqual(len(calls), 2)


class ChangedFilesAreReadCorrectly(Case):
    def notes(self):
        return [e.get("summary") or "" for e in read_flow(self.collab / "cards" / "CARD-001") if e.get("kind") == "范围"]

    def test_new_test_folder_is_not_blamed_on_the_developer(self):
        write_script(self.bin / "codex", 'mkdir e2e\necho 真测试 > e2e/a.txt\necho 写好了\n')
        self.start()
        self.assertEqual(self.finished(), "待验收")
        for note in self.notes():
            self.assertNotIn("撤销", note)
            self.assertNotIn("恢复", note)

    def test_script_in_a_new_folder_stays_runnable(self):
        write_script(self.bin / "codex", 'mkdir e2e\nprintf "#!/bin/sh\\nexit 0\\n" > e2e/run.sh\nchmod +x e2e/run.sh\necho 写好了\n')
        self.start("./e2e/run.sh")
        self.assertEqual(self.finished(), "待验收")

    def test_developer_file_in_an_allowed_new_folder_is_kept(self):
        cdir = self.ready("test -f src/feature/new.py")
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + "- src/feature/new.py\n", encoding="utf-8")
        write_script(self.bin / "agent", 'mkdir -p src/feature\necho 代码 > src/feature/new.py\necho 写好了\n')
        self.assertEqual(self.act("approve")[0], 200)
        self.assertEqual(self.finished(), "待验收")

    def test_test_file_with_a_space_is_protected(self):
        write_script(self.bin / "codex", 'mkdir -p tests\necho "exit 1" > "tests/my test.sh"\necho 写好了\n')
        write_script(self.bin / "agent", 'echo "exit 0" > "tests/my test.sh"\necho 写好了\n')
        self.start('sh "tests/my test.sh"')
        self.assertEqual(self.finished(), "需Claude介入")

    def test_rewritten_tests_leave_no_old_copy(self):
        broken = Path(self.tmp) / "broken"
        broken.write_text("第一次失败", encoding="utf-8")
        write_script(self.bin / "codex", f'mkdir -p tests\nif [ -f "{broken}" ]; then echo 旧 > tests/old.txt; exit 1; fi\n'
                                         'rm -f tests/old.txt\necho 新 > tests/new.txt\necho 写好了\n')
        self.start("test -f tests/new.txt")
        self.assertEqual(self.finished(), "需Claude介入")
        broken.unlink()
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "接着跑后没有完成")
        self.assertFalse((self.worktree / "tests" / "old.txt").exists())
        self.assertFalse((self.collab / "cards" / "CARD-001" / "测试快照" / "tests" / "old.txt").exists())

    def test_test_replaced_by_a_folder_is_restored(self):
        write_script(self.bin / "codex", 'mkdir -p tests\necho 真测试 > tests/test_new.py\necho 写好了\n')
        write_script(self.bin / "agent", 'rm tests/test_new.py\nmkdir tests/test_new.py\necho 写好了\n')
        self.start("test -f tests/test_new.py")
        self.assertEqual(self.finished(), "待验收")

    def test_test_replaced_by_a_link_does_not_write_through_it(self):
        write_script(self.bin / "codex", 'mkdir -p tests\necho 真测试 > tests/test_new.py\necho 写好了\n')
        write_script(self.bin / "agent", 'rm tests/test_new.py\nln -s ../src/app.py tests/test_new.py\necho 写好了\n')
        self.start()
        self.finished()
        self.assertEqual((self.worktree / "src" / "app.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertFalse((self.worktree / "tests" / "test_new.py").is_symlink())


class ApproveChecksFirst(Case):
    def test_broken_start_file_does_not_half_approve(self):
        cdir = self.ready()
        (cdir / "开工.json").write_text("坏的", encoding="utf-8")
        self.assertEqual(self.act("approve")[0], 409)
        self.assertEqual(self.state(), "待对齐")


class LastReviewFixes(Case):
    def test_carrying_on_does_not_freeze_the_developer_code(self):
        # 测试员没交活，副本里已有别的改动，接着跑时这些改动不能算成测试文件
        write_script(self.bin / "codex", 'echo 请确认\n')
        self.start("grep -q 第二版 src/app.py")
        self.assertEqual(self.finished(), "需Claude介入")
        (self.worktree / "src" / "app.py").write_text("第一版\n", encoding="utf-8")
        write_script(self.bin / "codex", 'echo 真测试 > test_new.py\necho 写好了\n')
        write_script(self.bin / "agent", 'echo 第二版 > src/app.py\necho 写好了\n')
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "接着跑后没有完成")
        files = json.loads((self.collab / "cards" / "CARD-001" / "测试文件.json").read_text(encoding="utf-8"))
        self.assertEqual(files, ["test_new.py"])

    def test_installed_dependencies_are_left_alone(self):
        from collab.scope import out_of_scope
        self.assertEqual(out_of_scope(["node_modules/a/index.js", "web/node_modules/b.js"], ["src/app.py"]), [])

    def test_moving_a_file_out_of_its_place_is_caught(self):
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "guide.md").write_text("说明\n", encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "guide")
        cdir = self.ready()
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + "- src/guide.md\n", encoding="utf-8")
        write_script(self.bin / "agent", 'git mv docs/guide.md src/guide.md\necho 写好了\n')
        self.assertEqual(self.act("approve")[0], 200)
        self.assertEqual(self.finished(), "需Claude介入")

    def test_cache_files_do_not_count_as_tests(self):
        write_script(self.bin / "codex", 'mkdir __pycache__\necho x > __pycache__/a.pyc\necho 请确认\n')
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        self.assertIn("没有写出任何测试", self.phase()["detail"])


class ReviewRework(Case):
    def test_opinion_survives_a_failed_developer(self):
        dev_log = Path(self.tmp) / "dev.log"
        broken = Path(self.tmp) / "broken"
        write_script(self.bin / "agent", f'[ -f "{broken}" ] && exit 1\n'
                                         f'printf "%s\\n===\\n" "$*" >> "{dev_log}"\necho 写好了\n')
        self.counted("agy", 'echo 结论：不通过\necho 按钮颜色还是红的', 'echo 结论：通过')
        self.start()
        wait_until(lambda: self.state() == "需Claude介入", 25, "复查没有打回")
        broken.write_text("开发员这次会失败", encoding="utf-8")
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: "开发员" in self.phase()["detail"], 25, "开发员没有失败")
        broken.unlink()
        self.assertEqual(self.act("resume")[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "最后没有完成")
        rounds = [r for r in dev_log.read_text(encoding="utf-8").split("\n===\n") if r.strip()]
        self.assertEqual(len(rounds), 2, "第三次接着跑开发员必须真的重改")
        self.assertIn("按钮颜色还是红的", rounds[1])


class StartAndStop(Case):
    def test_failed_launch_after_approval_has_a_way_out(self):
        for problem in (OSError("起不来"), TaskBusy("已有")):
            with self.subTest(problem=type(problem).__name__):
                card = "CARD-001" if isinstance(problem, OSError) else "CARD-002"
                cdir = self.make_card(card, plan=True, source=CONV, reported="plan_ready")
                docs = Path(self.project) / "docs"
                docs.mkdir(exist_ok=True)
                (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
                with mock.patch("desktop.server.launch", side_effect=problem):
                    status, answer = self.act("approve", card)
                self.assertEqual(status, 409)
                self.assertTrue(answer["error"])
                self.assertEqual(self.phase(card)["code"], "start_failed")
                self.assertTrue(cdir.exists())

    def test_card_started_in_a_terminal_cannot_be_stopped_here(self):
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        cdir = self.make_card(state="开发中")
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=proc.pid)
        status, answer = self.act("stop")
        self.assertEqual(status, 409)
        self.assertIn("终端", answer["error"])
        self.assertNotIn("停下", self.flow_kinds())
        self.assertIsNone(proc.poll())

    def test_process_time_does_not_depend_on_language_or_time_zone(self):
        script = ("import sys; sys.path.insert(0, %r);"
                  "from collab.phase import process_start; print(process_start(%d))" % (str(ROOT), os.getpid()))
        answers = set()
        for extra in ({"TZ": "Asia/Shanghai", "LANG": "zh_CN.UTF-8"}, {"TZ": "America/New_York", "LANG": "C"}):
            env = dict(os.environ, **extra)
            answers.add(subprocess.run(["python3", "-B", "-c", script], env=env, capture_output=True,
                                       text=True).stdout.strip())
        self.assertEqual(len(answers), 1, answers)
        self.assertTrue(answers.pop())


class TopLine(Base):
    def dead(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        return proc.pid

    def working(self, cdir, **run):
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", **run)
        record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写代码")

    def test_dropped_card_does_not_look_busy(self):
        self.working(self.make_card(state="已放弃"), pid=self.dead())
        self.assertEqual(card_view(self.project, "CARD-001")["now"]["text"], "不做了")

    def test_interrupted_card_says_so(self):
        self.working(self.make_card(state="开发中"), pid=self.dead())
        self.assertIn("中断了", card_view(self.project, "CARD-001")["now"]["text"])

    def test_reused_process_number_says_interrupted(self):
        self.working(self.make_card(state="开发中"), pid=os.getpid(), started="很久以前")
        self.assertIn("中断了", card_view(self.project, "CARD-001")["now"]["text"])

    def test_one_liveness_check(self):
        self.assertNotIn("def _pid_alive", (ROOT / "desktop" / "view.py").read_text(encoding="utf-8"))


class Rules(Base):
    def test_plan_cannot_use_wide_patterns(self):
        from collab.planner import _validate_answer
        for bad in ("**/*", "*/*", "?*", ".*", ".e*", "[.]env", "*.py"):
            self.assertFalse(_validate_answer(dict(GOOD_PLAN, allowed=[bad]), False)[0], bad)
        for good in ("src/*.py", "src/", "tests/[id].test.ts", "src/app.py"):
            self.assertTrue(_validate_answer(dict(GOOD_PLAN, allowed=[good]), False)[0], good)

    def test_unreadable_settings_are_a_plain_error(self):
        self.make_card()
        (self.collab / "配置.json").write_bytes(b"\xff\xfe")
        with self.assertRaises(ConfigError):
            load_team(self.project)

    def test_words_are_kept_when_the_model_fails_later(self):
        def broken():
            raise ModelError("断了")
        client = ScriptedClient([{"text": "我先查一下。", "tool_calls": [tool_call("list_cards")]}, broken])
        desk = ClerkDesk(lambda: client)
        cid = desk.send(self.project, "", "进度？")
        wait_idle(desk, self.project, cid)
        fresh = ClerkDesk(lambda: client)
        words = " ".join(e.get("text", "") for e in fresh.events(self.project, cid, 0, wait=0)["events"])
        self.assertIn("我先查一下", words)
