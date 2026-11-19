"""全量卡补测，覆盖已有验收未检查的边界。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import ProjectCase, wait_until
from collab import fullrun
from collab.flow import read_flow, record, text_of
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict
from collab.phase import card_phase, process_start
from desktop.clerk.tasks import TaskBusy
from desktop.view import card_view
from test_acceptance_11_fixes import Case
from test_fullcard_acceptance import OK_TEST, REVIEW_STOP


SUCCESS = Verdict(PASS, 0, "Ran 1 test in 0.001s\n\nOK\n")
FAILURE = Verdict(FAIL, 1, "Ran 1 test in 0.001s\n\nFAILED (failures=1)\n")
TIMED_OUT = Verdict(TIMEOUT, None, "")


class FullRunBoundaries(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["COLLAB_PYTHON"] = sys.executable
        self.worktree = self.make_copy()
        self.files = [f"tests/test_{name}.py" for name in ("a", "b", "c")]
        (self.worktree / "tests").mkdir()
        for rel in self.files:
            (self.worktree / rel).write_text(OK_TEST, encoding="utf-8")

    def test_timeout_lists_the_outcomes_of_files_that_run_after_it(self):
        with mock.patch.object(fullrun, "run_command", side_effect=[TIMED_OUT, SUCCESS, FAILURE]):
            result = fullrun.FullRunner(workers=1).run(self.worktree)

        self.assertEqual(result.result, TIMEOUT)
        self.assertIsNone(result.returncode)
        # 结果表在最前；没过的文件自己打印的输出跟在后面（CARD-010 Claude 接手：没有 FAIL:/ERROR: 开头的报错也要留）
        self.assertCountEqual(result.output.splitlines()[:3], [
            "test_a.py：超时", "test_b.py：过", "test_c.py：没过",
        ])

    def test_whole_run_deadline_limits_next_file_and_marks_queued_file(self):
        result = self.run_with_clock("run")
        self.assertEqual(result.result, TIMEOUT)
        self.assertCountEqual(result.output.splitlines(), [
            "test_a.py：过", "test_b.py：超时", "test_c.py：超时",
        ])

    def test_single_file_and_baseline_runs_share_the_whole_run_deadline(self):
        subprocess.run(["git", "add", "tests"], cwd=self.worktree, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@t",
                        "commit", "-qm", "基线测试"], cwd=self.worktree, check=True)
        for method in ("alone", "base"):
            with self.subTest(method=method):
                result = self.run_with_clock(method)
                self.assertEqual(result[self.files[0]].result, PASS)
                self.assertEqual(result[self.files[1]].result, TIMEOUT)

    def run_with_clock(self, method):
        elapsed = 0
        started = []

        def run_file(cmd, cwd, env, timeout, stop=None, **kwargs):
            nonlocal elapsed
            started.append((cmd[-1], timeout))
            if cmd[-1] == "test_a.py":
                elapsed += 295
                return SUCCESS
            elapsed += 15
            return TIMED_OUT

        with mock.patch.object(fullrun, "time", wraps=fullrun.time) as clock, \
                mock.patch.object(fullrun, "run_command", side_effect=run_file):
            clock.time.side_effect = clock.monotonic.side_effect = lambda: 1000 + elapsed
            runner = fullrun.FullRunner(timeout=310, workers=1)
            args = () if method == "run" else (self.files,)
            result = getattr(runner, method)(self.worktree, *args)

        self.assertEqual(started, [("test_a.py", 300), ("test_b.py", 15)])
        return result

    def test_path_size_and_nanosecond_changes_void_even_a_failed_run(self):
        source = self.worktree / "src" / "app.py"
        original = source.read_bytes()
        for rel in self.files[1:]:
            (self.worktree / rel).unlink()
        for change in ("路径", "大小", "纳秒修改时间"):
            with self.subTest(change=change):
                # 每次从同一个文件和固定时间开始，分别只改变一个指纹字段。
                source.write_bytes(original)
                stamp = 1_700_000_000_000_000_100
                os.utime(source, ns=(stamp, stamp))
                initial_mtime = source.stat().st_mtime_ns

                def run_file(*args, **kwargs):
                    if change == "路径":
                        source.rename(source.with_name("renamed.py"))
                    elif change == "大小":
                        source.write_bytes(original + b"\n")
                        os.utime(source, ns=(stamp, stamp))
                    else:
                        os.utime(source, ns=(stamp, stamp + 100))
                    return FAILURE

                with mock.patch.object(fullrun, "run_command", side_effect=run_file):
                    result = fullrun.FullRunner(workers=1).run(self.worktree)
                if change == "路径":
                    source.with_name("renamed.py").rename(source)
                if change == "纳秒修改时间":
                    self.assertNotEqual(source.stat().st_mtime_ns, initial_mtime)
                self.assertEqual(result.result, "作废")
                self.assertIsNone(result.returncode)
                self.assertIn("代码变了", result.output)

    def test_change_while_copying_also_voids_the_run(self):
        copy_tree = fullrun.copy_tree

        def copy_and_edit(src, dst, stop=None):
            copy_tree(src, dst, stop)
            (Path(src) / "src" / "app.py").write_text("x = 123\n", encoding="utf-8")

        with mock.patch.object(fullrun, "copy_tree", side_effect=copy_and_edit), \
                mock.patch.object(fullrun, "run_command", return_value=TIMED_OUT):
            result = fullrun.FullRunner(workers=1).run(self.worktree)
        self.assertEqual(result.result, "作废")

    def test_changes_in_excluded_directories_and_bytecode_leave_result_valid(self):
        # 施工副本的 .git 是文件，直接检查普通仓库里的 .git 目录。
        root = Path(self.project)
        (root / "tests").mkdir()
        (root / self.files[0]).write_text(OK_TEST, encoding="utf-8")

        def write_caches(*args, **kwargs):
            (root / ".git" / "cache").touch()
            for dirname in ("node_modules", ".collab", "__pycache__", "Example.app", ".venv", "venv"):
                folder = root / "src" / dirname
                folder.mkdir()
                (folder / "cache").touch()
            (root / "src" / "app.pyc").touch()
            return SUCCESS

        with mock.patch.object(fullrun, "run_command", side_effect=write_caches):
            result = fullrun.FullRunner(workers=1).run(root)
        self.assertEqual(result.result, PASS, result.output)

    def test_external_stop_wins_over_a_timeout_and_a_source_change(self):
        stop = threading.Event()

        def stop_after_timeout(*args, **kwargs):
            (self.worktree / "src" / "app.py").write_text("x = 123\n", encoding="utf-8")
            stop.set()
            return TIMED_OUT

        with mock.patch.object(fullrun, "run_command", side_effect=stop_after_timeout) as run_file:
            result = fullrun.FullRunner(workers=1).run(self.worktree, stop)
        self.assertEqual(result.result, "停了")
        self.assertEqual(run_file.call_count, 1, "叫停后不能再启动后面的测试文件")

    def test_merge_prioritizes_stop_then_stale_then_timeout(self):
        stale = Verdict("作废", None, "跑的时候代码变了")
        stopped = Verdict("停了", None, "")
        for verdicts, expected in (
            ([TIMED_OUT, stale], "作废"), ([stale, stopped, TIMED_OUT], "停了"),
        ):
            with self.subTest(expected=expected):
                result = fullrun._merge(list(zip(self.files, verdicts)), 0)
                self.assertEqual(result.result, expected)


class PythonFallback(unittest.TestCase):
    def fresh_fullrun(self):
        # 单独加载一份，避免别的用例已经找过 Python，或留下本用例的缓存。
        spec = importlib.util.spec_from_file_location("fullrun_for_python_choice", fullrun.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_invalid_override_uses_login_python_once_and_links_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            missing_execute_bit = root / "不可执行"
            missing_execute_bit.touch()
            login_python = root / "登录环境的 Python"
            login_python.symlink_to(sys.executable)
            module = self.fresh_fullrun()
            lookup = ["/bin/zsh", "-lc", "command -v python3"]
            completed = subprocess.CompletedProcess(lookup, 0, f"{login_python}\n", "")
            with mock.patch.dict(os.environ, {"COLLAB_PYTHON": str(missing_execute_bit)}), \
                    mock.patch.object(module.subprocess, "run", return_value=completed) as shell:
                self.assertEqual(module.app_python(), str(login_python))
                module.safe_env(root / "bin")
                self.assertEqual(module.app_python(), str(login_python))

            self.assertEqual(os.readlink(root / "bin" / "python3"), str(login_python))
            self.assertEqual(shell.call_count, 1)
            self.assertEqual(shell.call_args.args[0], lookup)
            self.assertEqual(shell.call_args.kwargs.get("timeout"), 10)

    def test_lookup_timeout_or_launch_failure_uses_current_interpreter(self):
        for error in (subprocess.TimeoutExpired("/bin/zsh", 10), OSError("shell 启动失败")):
            with self.subTest(error=error):
                module = self.fresh_fullrun()
                with mock.patch.dict(os.environ, {"COLLAB_PYTHON": ""}), \
                        mock.patch.object(module.subprocess, "run", side_effect=error):
                    self.assertEqual(module.app_python(), sys.executable)


class AcceptanceBoundaries(ProjectCase):
    def blocked(self, card_id="CARD-001", **fields):
        cdir = self.make_card(card_id, state="需Claude介入", plan=True)
        stop = {"summary": REVIEW_STOP, "cause": "复查不通过", **fields}
        record(cdir, "转人工", src="程序", dst="调度员", **stop)
        return cdir

    def write_background_record(self, card_id, started):
        backend = self.collab / "后台" / "accept.json"
        backend.parent.mkdir(exist_ok=True)
        backend.write_text(json.dumps({
            "card": card_id, "pid": os.getpid(), "started": started,
        }), encoding="utf-8")

    def test_latest_manual_stop_and_current_state_are_checked_again(self):
        from collab.accept import accept_remaining

        manual = self.blocked()
        record(manual, "转人工", summary="复查不通过，需要调度员看原因", cause="测试不通过")
        other_state = self.make_card("CARD-002", state="待验收", plan=True)
        record(other_state, "转人工", summary=REVIEW_STOP, cause="复查不通过")
        for card_id, cdir in (("CARD-001", manual), ("CARD-002", other_state)):
            with self.subTest(card=card_id):
                state_before = (cdir / "状态.json").read_bytes()
                events_before = read_flow(cdir)
                runner = mock.Mock()
                self.assertEqual(accept_remaining(self.project, card_id, runner=runner),
                                 {"ok": False, "reason": "这张卡现在不能这样做。"})
                self.assertEqual((cdir / "状态.json").read_bytes(), state_before)
                self.assertEqual(read_flow(cdir), events_before)
                runner.run.assert_not_called()

    def test_cli_missing_worktree_records_failure_and_exits_one(self):
        cdir = self.blocked()
        code, output = self.cli("accept-issues", self.project, "CARD-001")
        self.assertEqual(code, 1, output)
        reason = "找不到改好的版本，没法跑全部测试"
        self.assertIn(reason, output)
        self.assertEqual(self.state(), "需Claude介入")
        self.assertEqual(read_flow(cdir)[-1]["kind"], "接受失败")
        self.assertEqual(read_flow(cdir)[-1]["summary"], reason)

    def test_legacy_card_without_python_tests_keeps_the_reviewers_words(self):
        original = "1. 复查原话第一条\n2. 复查原话第二条"
        cdir = self.blocked(summary="第 2 次复查不通过，需要调度员看原因", cause=None, text=original)
        self.make_copy()
        self.write_background_record("CARD-001", process_start(os.getpid()))

        code, output = self.cli("accept-issues", self.project, "CARD-001")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.state(), "待验收")
        accepted, ended = read_flow(cdir)[-2:]
        self.assertEqual((accepted["kind"], accepted["src"], accepted["dst"]), ("接受", "你", "程序"))
        self.assertEqual(text_of(cdir, accepted), original)
        self.assertEqual((ended["kind"], ended["state"]), ("结束", "待验收"))
        self.assertIn("全部测试也过了", ended["summary"])
        self.assertIn("| CARD-001 | 待验收 |", (self.collab / "任务板.md").read_text(encoding="utf-8"))
        ledger = Path(self.project) / "docs" / "开发进度跟踪.md"
        self.assertIn("CARD-001：接受复查剩下的问题，全部测试通过，进入待确认",
                      ledger.read_text(encoding="utf-8"))

    def test_failure_reasons_keep_card_blocked_and_save_only_last_8000_characters(self):
        from collab.accept import accept_remaining

        cdir = self.blocked()
        worktree = self.make_copy()
        (worktree / "tests").mkdir()
        (worktree / "tests" / "test_card.py").write_text(OK_TEST, encoding="utf-8")
        state_before = (cdir / "状态.json").read_bytes()
        output = "头上这段不能留下\n" + "测" * 8010 + "\n最后一条输出\n"
        cases = [
            (FAIL, "没过"), (TIMEOUT, "超时"), ("作废", "作废"),
            (ENV_ERROR, "没跑起来"), ("停了", "没跑起来"),
        ]
        for result, wording in cases:
            with self.subTest(result=result):
                runner = mock.Mock()
                runner.run.return_value = Verdict(result, None, output)
                answer = accept_remaining(self.project, "CARD-001", runner=runner)
                self.assertFalse(answer["ok"])
                self.assertIn("全部测试", answer["reason"])
                self.assertIn(wording, answer["reason"])
                failure = read_flow(cdir)[-1]
                self.assertEqual((failure["kind"], failure["src"], failure["dst"]), ("接受失败", "程序", "你"))
                self.assertEqual(failure["summary"], answer["reason"])
                self.assertEqual(text_of(cdir, failure), output[-8000:])
                self.assertEqual((cdir / "状态.json").read_bytes(), state_before)
        self.assertNotIn("接受", self.flow_kinds())
        self.assertNotIn("结束", self.flow_kinds())

    def test_stale_background_records_and_failures_from_previous_stop_stay_hidden(self):
        old_reason = "之前那次全部测试没过"
        cdir = self.blocked()
        record(cdir, "接受失败", summary=old_reason)
        current_reason = "这次复查停下的原因"
        record(cdir, "转人工", summary="第 2 次复查不通过，需要调度员看原因", say=current_reason)
        for card_id, started in (("CARD-002", process_start(os.getpid())), ("CARD-001", "过期启动时间")):
            with self.subTest(card=card_id):
                self.write_background_record(card_id, started)
                phase = card_phase(self.project, "CARD-001")
                self.assertEqual(phase["code"], "blocked")
                self.assertIn("accept_issues", phase["actions"])
                self.assertIn(current_reason, phase["detail"])
                self.assertNotIn(old_reason, phase["detail"])

    def test_review_summary_is_used_when_original_text_was_never_saved(self):
        from collab.accept import accept_remaining

        current_reason = "这次复查停下的原因"
        cdir = self.blocked(say=current_reason)
        self.make_copy()
        self.assertTrue(accept_remaining(self.project, "CARD-001")["ok"])
        accepted = read_flow(cdir)[-2]
        self.assertEqual(accepted["kind"], "接受")
        self.assertEqual(text_of(cdir, accepted), current_reason)


class BackgroundAcceptance(Case):
    def blocked(self, card_id="CARD-001"):
        cdir = self.make_card(card_id, state="需Claude介入", plan=True)
        record(cdir, "转人工", summary=REVIEW_STOP, cause="复查不通过", text=REVIEW_STOP)
        return cdir

    def test_busy_or_unavailable_background_returns_conflict_without_accepting(self):
        for card_id, error in (("CARD-001", TaskBusy("已有任务")), ("CARD-002", OSError("后台起不来"))):
            with self.subTest(error=error):
                cdir = self.blocked(card_id)
                with mock.patch("desktop.server.launch", side_effect=error):
                    status, answer = self.act("accept_issues", card_id)
                self.assertEqual(status, 409, answer)
                self.assertIn("正在跑全部测试", answer["error"])
                self.assertEqual(self.state(card_id), "需Claude介入")
                self.assertEqual(read_flow(cdir)[-1]["kind"], "转人工")

    def test_failed_background_restores_actions_and_can_be_retried(self):
        cdir = self.blocked()
        initial_actions = card_phase(self.project, "CARD-001")["actions"]
        worktree = self.make_copy()
        (worktree / "tests").mkdir()
        gate = Path(self.tmp) / "让测试继续"
        test_file = worktree / "tests" / "test_card.py"
        test_file.write_text(
            "import unittest\n"
            "from pathlib import Path\n"
            "from time import sleep, monotonic\n"
            "class CardTest(unittest.TestCase):\n"
            "    def test_wait_then_fail(self):\n"
            f"        gate = Path({str(gate)!r})\n"
            "        end = monotonic() + 15\n"
            "        while not gate.exists() and monotonic() < end:\n"
            "            sleep(0.05)\n"
            "        self.fail('这条全量测试故意没过')\n",
            encoding="utf-8",
        )
        os.environ["COLLAB_PYTHON"] = sys.executable
        backend = self.collab / "后台" / "accept.json"
        try:
            status, answer = self.act("accept_issues")
            self.assertEqual(status, 200, answer)
            self.assertEqual(self.state(), "需Claude介入")
            phase = card_phase(self.project, "CARD-001")
            self.assertEqual(phase["code"], "accepting")
            self.assertFalse(phase["needs_you"])
            self.assertEqual(phase["actions"], [])
            self.assertEqual(card_view(self.project, "CARD-001")["now"]["text"], "正在跑全部测试")
            start = read_flow(cdir)[-1]
            self.assertEqual((start["kind"], start["src"], start["dst"]), ("接受开始", "程序", "程序"))
            self.assertGreater(start["pid"], 0)
            self.assertTrue(start["started"])
            self.assertEqual(start["started"], process_start(start["pid"]))
        finally:
            gate.touch()
            wait_until(lambda: not backend.exists(), 25, "后台测试没结束")

        self.assertEqual(self.state(), "需Claude介入")
        failure = read_flow(cdir)[-1]
        self.assertEqual(failure["kind"], "接受失败")
        self.assertIn("这条全量测试故意没过", text_of(cdir, failure))
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "blocked")
        self.assertIn(REVIEW_STOP, phase["detail"])
        self.assertIn(failure["summary"], phase["detail"])
        self.assertEqual(phase["actions"], initial_actions)

        test_file.write_text(OK_TEST, encoding="utf-8")
        status, answer = self.act("accept_issues")
        self.assertEqual(status, 200, answer)
        wait_until(lambda: not backend.exists(), 25, "重跑没有结束")
        self.assertEqual(self.state(), "待验收")
        accepted = [event for event in read_flow(cdir) if event["kind"] == "接受"]
        self.assertEqual([text_of(cdir, event) for event in accepted], [REVIEW_STOP])
