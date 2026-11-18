"""施工单 B 补充：本卡命令计数、回退、副本隔离和错误处理。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab.judge import ENV_ERROR, TIMEOUT, Verdict
from collab.progress import ProgressJob, read


EXTRA = "import unittest\nclass Extra(unittest.TestCase):\n    def test_extra(self): pass\n"
ACCEPTANCE = """import unittest
from pathlib import Path
class Acceptance(unittest.TestCase):
    def test_pass(self): pass
    def test_fail(self): self.fail('仍有遗漏')
    @unittest.skip('条件不足')
    def test_skip(self): pass
    def test_copy_only(self): Path('copy-marker').write_text('ran')
"""


class ProgressCommandEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.wt = root / "wt"
        self.tests = self.wt / "tests"
        self.tests.mkdir(parents=True)
        (self.tests / "test_extra.py").write_text(EXTRA, encoding="utf-8")
        (self.tests / "test_acceptance.py").write_text(ACCEPTANCE, encoding="utf-8")
        self.cdir = root / "card"
        self.cdir.mkdir()
        self.command = 'python3 -m unittest discover -s tests -p "test_acceptance.py"'

    def job(self, command, files=None):
        return ProgressJob(self.cdir, self.wt, "开发", 17,
                           ["tests/test_extra.py"] if files is None else files,
                           test_command=command)

    def assert_count(self, got, passed, total):
        self.assertEqual(got, {"seq": 17, "step": "开发", "passed": passed, "total": total})

    def test_command_with_only_preexisting_acceptance_tests_is_counted(self):
        self.assert_count(self.job(self.command, files=[]).compute(), 2, 4)

    def test_tick_runs_without_newly_delivered_test_files(self):
        job = self.job(self.command, files=[])
        job.tick()
        self.assert_count({k: v for k, v in read(self.cdir).items() if k != "time"}, 2, 4)
        self.assertEqual(job.runs, 1)
        self.assertFalse((self.wt / "copy-marker").exists(), "测试只能写副本")

    def test_chain_continues_after_test_failure_and_deducts_skips(self):
        command = self.command + ' && python3 -m unittest discover -s tests -p "test_extra.py"'
        self.assert_count(self.job(command).compute(), 3, 5)
        self.assertFalse((self.wt / "copy-marker").exists())

    def test_python_alias_and_quoted_pattern_are_supported(self):
        # safe_env 保证 python3 可用；临时补 python 同义入口，不依赖机器是否装了 python。
        import collab.progress as progress
        real_env = progress.safe_env

        def env_with_alias(folder):
            env = real_env(folder)
            (Path(folder) / "python").symlink_to(Path(folder) / "python3")
            return env

        with mock.patch("collab.progress.safe_env", side_effect=env_with_alias):
            self.assert_count(self.job("  python -m unittest discover -s tests -p 'test_extra.py'  ").compute(), 1, 1)

    def test_mixed_command_falls_back_whole_and_runs_no_shell_tail(self):
        import shlex
        marker = Path(self.tmp.name) / "should-not-exist"
        for command in (
            'python3 -m unittest discover -s tests -p "test_extra.py" && touch ' + shlex.quote(str(marker)),
            "echo ignored && " + self.command,
            "python3 -m pytest",
            "npm test",
        ):
            with self.subTest(command=command):
                self.assert_count(self.job(command).compute(), 1, 1)
                self.assertFalse(marker.exists())
                self.assertFalse((self.wt / "copy-marker").exists())

    def test_empty_command_keeps_delivered_file_fallback(self):
        self.assert_count(self.job("").compute(), 1, 1)

    def test_default_command_still_ignores_missing_delivered_file(self):
        job = ProgressJob(self.cdir, self.wt, "开发", 17,
                          ["tests/missing_test.py", "tests/test_extra.py"])
        self.assert_count(job.compute(), 1, 1)

    def test_import_error_in_card_command_is_not_reported_as_normal_progress(self):
        (self.tests / "test_import_error.py").write_text("raise ImportError('还未实现')\n", encoding="utf-8")
        got = self.job('python3 -m unittest discover -s tests -p "test_import_error.py"').compute()
        self.assertIn("跑不起来", got.get("error", ""), got)
        self.assertNotIn("passed", got)
        self.assertNotIn("total", got)

    def test_timeout_and_unstartable_command_keep_existing_error_messages(self):
        # 不真等十分钟；只替换启动子进程的边界，其余准备副本和错误处理照常执行。
        for result, message in ((TIMEOUT, "测试跑超时了"), (ENV_ERROR, "测试跑不起来")):
            with self.subTest(result=result):
                job = self.job(self.command)
                with mock.patch("collab.progress.run_command", return_value=Verdict(result, None, "")):
                    got = job.compute()
                self.assertEqual(got, {"seq": 17, "step": "开发", "error": message})

    def test_stop_during_command_does_not_publish_partial_counts(self):
        job = self.job(self.command)

        def stopped(*args):
            job.event.set()
            return Verdict("停了", None, "")

        with mock.patch("collab.progress.run_command", side_effect=stopped):
            self.assertEqual(job.compute(), {})

    def test_dispatch_passes_string_and_wrapped_card_command_to_real_progress(self):
        from collab import dispatch
        from collab.agents import AgentResult

        def ask(*args, **kwargs):
            return AgentResult(True, "已完成", 1, final="汇报：已完成")

        def start_synchronously(job):
            # 省去后台线程等待，保留真实构造、命令处理、复制、计数和写进度。
            job.tick()
            return job

        for command in (self.command, ["sh", "-c", self.command]):
            with self.subTest(command=command):
                with mock.patch.object(dispatch, "_PROGRESS_ON", {str(self.cdir)}), \
                        mock.patch.object(ProgressJob, "start", start_synchronously):
                    dispatch._dispatch(ask, self.cdir, "开发员", "agy", "开发", "处理遗漏",
                                       self.wt, {}, 1, test_command=command)
                got = read(self.cdir)
                self.assertEqual((got.get("passed"), got.get("total")), (2, 4), got)
                self.assertEqual(got.get("step"), "开发")


class ReportInstructionEdges(unittest.TestCase):
    def test_report_avoids_technical_names_and_keeps_existing_delivery_rules(self):
        from collab.dispatch import _REPORT_ASK
        for required in ("大白话", "不写文件名", "函数名", "命令", "汇报：", "最后一行", "100", "不要另建汇报文件"):
            with self.subTest(required=required):
                self.assertIn(required, _REPORT_ASK)


if __name__ == "__main__":
    unittest.main()
