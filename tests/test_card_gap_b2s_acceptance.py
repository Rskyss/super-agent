"""跑卡卡点修理 卡 B2′ 验收：已知没过名单（11-27 改为“拿掉文件再跑一遍”，方案“第三次修订”末节、任务清单 B2′ 行）。

① 等卡文件没过、别的全过 → 放行
② 名单外坏一项 → 照老规矩（改之前过 → 退回写代码的）
③ 带 && 的命令、Python 项目（逐文件跑）也照样能用
④ 拿掉后还有没过、拿掉后 0 项、别的测试引用了等卡文件 → 不放行
⑤ 名单读不懂 → 停下
⑥ 放进项目撤掉等这张卡的条目
⑦ 人工“接受剩下的问题”同样
⑧ 没登记名单时和改之前一模一样
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab.judge import FAIL, PASS, Verdict

NODE = shutil.which("node")
NODE_OK = 'import { test } from "node:test";\n'


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _node_test(*cases):
    return NODE_OK + "".join(
        f'test({json.dumps(n)}, () => {{ {"" if ok else "throw new Error(" + json.dumps(n) + ");"} }});\n'
        for n, ok in cases)


def _py_test(**cases):
    body = "import unittest\n\n\nclass T(unittest.TestCase):\n"
    for name, ok in cases.items():
        body += f"    def {name}(self):\n        self.assertTrue({ok})\n\n"
    return body


class KnownList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_remove_with_ledger(self):
        from collab import known_fail
        known_fail.add(self.project, "tests/a.test.ts", [], "等卡", card="CARD-031", why="验收先放")
        self.assertEqual([i["file"] for i in known_fail.load(self.project)], ["tests/a.test.ts"])
        self.assertEqual(known_fail.remove(self.project, "tests/a.test.ts"), 1)
        ledger = (self.project / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertEqual(ledger.count("已知没过"), 2)

    def test_refused_inputs(self):
        from collab import known_fail
        for kwargs in ({"file": "tests/a.test.ts", "tests": ["某项"], "card": "C"},   # 只能整个文件
                       {"file": "tests/a.test.ts", "tests": [], "card": None},       # 等卡要卡号
                       {"file": "../x.test.ts", "tests": [], "card": "C"},           # 项目外
                       {"file": "/abs/x.test.ts", "tests": [], "card": "C"},
                       {"file": ".collab/x", "tests": [], "card": "C"},
                       {"file": "tests/legacy", "tests": [], "card": "C"},                # 目录
                       {"file": "tests/legacy/__init__.py", "tests": [], "card": "C"},    # 包入口
                       {"file": "tests/conftest.py", "tests": [], "card": "C"},
                       {"file": "app.py", "tests": [], "card": "C"}):                      # 不是测试文件
            with self.subTest(kwargs), self.assertRaises(ValueError):
                known_fail.add(self.project, kwargs["file"], kwargs["tests"], "等卡", card=kwargs["card"], why="w")
        with self.assertRaises(ValueError):
            known_fail.add(self.project, "tests/a.test.ts", [], "偶发", why="不再支持")
        self.assertEqual(known_fail.load(self.project), [])

    def test_broken_and_old_per_item_entries_are_broken(self):
        from collab import known_fail
        path = self.project / ".collab" / "已知没过.json"
        for text in ("{", json.dumps({"items": [{"file": "tests/a.test.ts", "tests": ["某项"], "kind": "等卡",
                                                 "card": "C", "by": "x", "at": "t", "why": "w"}]})):
            with self.subTest(text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(known_fail.Broken):
                    known_fail.load(self.project)

    def test_ledger_first_then_list(self):
        from collab import known_fail
        with mock.patch.object(known_fail, "ledger_add", side_effect=PermissionError("只读")):
            with self.assertRaises(PermissionError):
                known_fail.add(self.project, "tests/a.test.ts", [], "等卡", card="CARD-1", why="w")
        self.assertEqual(known_fail.load(self.project), [])

    def test_cli(self):
        from collab.cli import main
        from collab import known_fail
        self.assertEqual(main(["known-fail", "add", str(self.project), "--file", "tests/a.test.ts",
                               "--card", "CARD-031", "--why", "验收先放"]), 0)
        self.assertEqual(known_fail.load(self.project)[0]["kind"], "等卡")
        self.assertEqual(main(["known-fail", "remove", str(self.project), "--file", "tests/a.test.ts"]), 0)


class _Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "proj"
        self.cdir = self.project / ".collab" / "cards" / "CARD-050"
        self.cdir.mkdir(parents=True)
        self.wt = root / "wt"
        (self.wt / "tests").mkdir(parents=True)
        self.base()
        _git(self.wt, "init", "-q")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for rel, text in files.items():
            (self.wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / rel).write_text(text, encoding="utf-8")

    def register(self, file, card="CARD-051"):
        from collab import known_fail
        known_fail.add(self.project, file, [], "等卡", card=card, why="测试")

    def slot_finish(self, runner):
        from collab.dispatch import _FullSlot
        slot = _FullSlot(runner, self.wt, self.cdir)
        slot.start()
        return slot.finish(1)

    def flow_summaries(self):
        from collab.flow import read_flow
        return [e.get("summary") or "" for e in read_flow(self.cdir) if e.get("kind") == "全量"]


@unittest.skipUnless(NODE, "需要 node")
class GateCommand(_Gate):
    COMMAND = "node --test --test-concurrency=1 tests/*.test.mjs"

    def base(self):
        self.write({"tests/old.test.mjs": _node_test(("老一", True), ("老二", True)), "check.mjs": "process.exit(0);\n"})

    def finish(self, command=None):
        from collab.fullrun import CommandRunner
        return self.slot_finish(CommandRunner(command or self.COMMAND))

    def test_1_waiting_file_passes(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        self.assertEqual(self.finish(), (Verdict(PASS, 0, ""), ""))
        self.assertTrue(any("全部测试都过" in s and "拿掉名单里" in s for s in self.flow_summaries()))

    def test_1_unregistered_not_waved(self):
        self.register("tests/other.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish()
        self.assertEqual((verdict.result, stop), (FAIL, ""), "名单外的照老规矩：改之前过 → 退回")

    def test_2_outside_new_failure_goes_back(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False)),
                    "tests/old.test.mjs": _node_test(("老一", True), ("老二", False))})
        verdict, stop = self.finish()
        self.assertEqual((verdict.result, stop), (FAIL, ""))

    def test_3_chained_command_also_works(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        self.assertEqual(self.finish(self.COMMAND + " && node check.mjs"), (Verdict(PASS, 0, ""), ""))

    def test_3_chained_command_later_step_broken_not_waved(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False)), "check.mjs": "process.exit(2);\n"})
        verdict, stop = self.finish(self.COMMAND + " && node check.mjs")
        self.assertEqual((verdict.result, stop), (FAIL, ""), "类型检查这类步骤新坏了：拿掉等卡文件也不过，退回")

    def test_4_only_waiting_files_zero_tests_not_passed(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish("node --test tests/accept.test.mjs")
        self.assertFalse(verdict.result == PASS and stop == "", "拿掉后一项都没跑，不能当过")

    def test_4_shared_helper_removed_not_passed(self):
        """老测试引用了等卡文件里的东西：拿掉后老测试跑不起来，不放行。"""
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": NODE_OK + "export const nine = 9;\n" + 'test("验收", () => { throw Error("x"); });\n',
                    "tests/old.test.mjs": 'import { nine } from "./accept.test.mjs";\n' + _node_test(("老一", True))})
        verdict, stop = self.finish()
        self.assertFalse(verdict.result == PASS and stop == "")

    def test_5_broken_list_stops(self):
        from collab import known_fail
        self.register("tests/accept.test.mjs")
        known_fail._path(self.project).write_text("{", encoding="utf-8")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish()
        self.assertIn("名单读不懂", stop)

    def test_8_no_list_old_behaviour(self):
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish()
        self.assertEqual((verdict.result, stop), (FAIL, ""))

    def test_all_pass_no_rerun(self):
        self.register("tests/accept.test.mjs")
        from collab.fullrun import CommandRunner
        runner = CommandRunner(self.COMMAND)
        with mock.patch.object(CommandRunner, "run", wraps=runner.run) as run:
            self.assertEqual(self.slot_finish(runner), (Verdict(PASS, 0, ""), ""))
        self.assertEqual(run.call_count, 1, "全过就不多跑")


class GatePython(_Gate):
    """③ Python 项目（逐文件跑）同样拿掉再跑。"""

    def base(self):
        self.write({"tests/test_old.py": _py_test(test_a=True, test_b=True)})

    def finish(self):
        from collab.fullrun import FullRunner
        return self.slot_finish(FullRunner())

    def test_waiting_file_passes(self):
        self.register("tests/test_accept.py")
        self.write({"tests/test_accept.py": _py_test(test_x=False)})
        self.assertEqual(self.finish(), (Verdict(PASS, 0, ""), ""))

    def test_waiting_import_error_passes(self):
        self.register("tests/test_accept.py")
        self.write({"tests/test_accept.py": "from 还没写的模块 import x\n"})
        self.assertEqual(self.finish(), (Verdict(PASS, 0, ""), ""))

    def test_outside_failure_blocks(self):
        self.register("tests/test_accept.py")
        self.write({"tests/test_accept.py": _py_test(test_x=False), "tests/test_old.py": _py_test(test_a=True, test_b=False)})
        verdict, stop = self.finish()
        self.assertFalse(verdict.result == PASS and stop == "")


class GatePythonOnlyWaiting(_Gate):
    """④ 拿掉后一项测试都不剩（Python 逐文件跑会报“跑了 0 项、通过”）：不能当过。"""

    def base(self):
        self.write({"app.py": "X = 1\n"})

    def test_zero_left_not_passed(self):
        from collab.fullrun import FullRunner
        self.register("tests/test_accept.py")
        self.write({"tests/test_accept.py": _py_test(test_x=False)})
        verdict, stop = self.slot_finish(FullRunner())
        self.assertFalse(verdict.result == PASS and stop == "")


@unittest.skipUnless(NODE, "需要 node")
class Round2Command(GateCommand):
    """卡 B2′ 复查第 2 轮。"""

    def test_gitignored_registered_file_not_removed_not_passed(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False)), ".gitignore": "tests/accept.test.mjs\n"})
        _git(self.wt, "add", ".gitignore")
        _git(self.wt, "commit", "-qm", "ignore")
        self.write({"tests/old.test.mjs": _node_test(("老一", True), ("老二", False))})   # 老测试真坏了
        verdict, stop = self.finish()
        self.assertFalse(verdict.result == PASS and stop == "", "副本里根本没有这个文件，不能说拿掉了")

    def test_count_unknown_not_passed(self):
        """报告格式给不出项数（dot）：认不出实际跑了几项，不放行。"""
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish(self.COMMAND.replace("node --test", "node --test --test-reporter=dot"))
        self.assertFalse(verdict.result == PASS and stop == "")

    def test_code_changed_between_runs_not_passed(self):
        from collab.fullrun import CommandRunner
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        runner = CommandRunner(self.COMMAND)
        real = runner.run

        def run(worktree, stop=None, without=()):
            if without:   # 第二遍开跑前改了老测试要用的代码
                self.write({"tests/old.test.mjs": _node_test(("老一", True), ("老二", False))})
                self.write({"tests/old.test.mjs": _node_test(("老一", True), ("老二", True))})
                (self.wt / "extra.txt").write_text("改过\n", encoding="utf-8")
            return real(worktree, stop, without=without)
        runner.run = run
        verdict, stop = self.slot_finish(runner)
        self.assertFalse(verdict.result == PASS and stop == "", "两遍之间代码变了，不能拼起来放行")

    def test_list_changed_between_runs_not_passed(self):
        from collab import known_fail
        from collab.fullrun import CommandRunner
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        runner = CommandRunner(self.COMMAND)
        real = runner.run

        def run(worktree, stop=None, without=()):
            out = real(worktree, stop, without=without)
            if without:
                known_fail.remove(self.project, "tests/accept.test.mjs")   # 第二遍跑着时撤单
            return out
        runner.run = run
        verdict, stop = self.slot_finish(runner)
        self.assertFalse(verdict.result == PASS and stop == "")

    def test_broken_list_stops_even_when_all_pass(self):
        from collab import known_fail
        self.register("tests/accept.test.mjs")
        known_fail._path(self.project).write_text("{", encoding="utf-8")
        verdict, stop = self.finish()
        self.assertIn("名单读不懂", stop)

    def test_second_run_gets_fresh_cache(self):
        """拿掉文件再跑的那一遍用干净的缓存目录，不吃第一遍留下的缓存。"""
        self.register("tests/accept.test.mjs")
        cache = Path(self.tmp.name) / "shared-cache"
        cache.mkdir()
        self.write({"tests/accept.test.mjs": _node_test(("验收", False)),
                    "tests/old.test.mjs": NODE_OK + 'import fs from "node:fs";\n'
                    'const d = process.env.XDG_CACHE_HOME, f = d + "/warm";\n'
                    'test("老一", () => { const warm = fs.existsSync(f); fs.writeFileSync(f, "1"); if (!warm) throw Error("首次启动坏了"); });\n'})
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}):
            verdict, stop = self.finish()
        self.assertFalse(verdict.result == PASS and stop == "", "第二遍读到第一遍写的缓存才变绿，不能放行")


class Round2Python(_Gate):
    def base(self):
        self.write({"tests/test_old.py": _py_test(test_a=True)})

    def test_all_skipped_after_removal_not_passed(self):
        from collab.fullrun import FullRunner
        self.register("tests/test_wait.py")
        self.write({"tests/test_wait.py": _py_test(test_x=False),
                    "tests/test_old.py": "import unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
                                         "    @unittest.skipUnless(Path('tests/test_wait.py').is_file(), 'x')\n"
                                         "    def test_price(self):\n        self.fail()\n"})
        verdict, stop = self.slot_finish(FullRunner())
        self.assertFalse(verdict.result == PASS and stop == "", "拿掉后只剩跳过的，实际 0 项")


class Round2List(unittest.TestCase):
    def test_no_list_project_untouched(self):
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            os.chmod(project / ".collab", 0o555)
            try:
                self.assertEqual(known_fail.load(project), [])
                self.assertFalse((project / ".collab" / "已知没过.lock").exists())
            finally:
                os.chmod(project / ".collab", 0o755)

    def test_remove_interrupted_before_ledger_leaves_no_exemption(self):
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="C", why="w")
            with mock.patch.object(known_fail, "ledger_add", side_effect=OSError("断了")):
                with self.assertRaises(OSError):
                    known_fail.remove(project, "tests/a.test.ts")
            self.assertEqual(known_fail.load(project), [], "撤单写台账前断了：豁免已经没了（只会多挡）")

    def test_concurrent_adds_both_kept(self):
        import threading
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            ts = [threading.Thread(target=known_fail.add, args=(project, f"tests/t{i}.test.ts", [], "等卡"),
                                   kwargs={"card": "C", "why": "w"}) for i in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(len(known_fail.load(project)), 4)


class MergeDropsWaiting(unittest.TestCase):
    def test_merge_drops_card_entries(self):
        from collab import known_fail, merge
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="CARD-031", why="w")
            known_fail.add(project, "tests/b.test.ts", [], "等卡", card="CARD-032", why="w")
            merge._drop_waiting(project, "CARD-031")
            self.assertEqual([i["file"] for i in known_fail.load(project)], ["tests/b.test.ts"])

    def test_merge_card_calls_drop(self):
        src = (Path(__file__).resolve().parents[1] / "collab" / "merge.py").read_text(encoding="utf-8")
        body = src.split("def merge_card(", 1)[1]
        self.assertIn("_drop_waiting(", body)


@unittest.skipUnless(NODE, "需要 node")
class AcceptWithWaiting(_Gate):
    """⑦ 人工接受：用真实的全部测试（项目配的命令）跑。"""

    COMMAND = "node --test --test-concurrency=1 tests/*.test.mjs"

    def base(self):
        self.write({"tests/old.test.mjs": _node_test(("老一", True))})

    def accept(self):
        from collab import accept
        from collab.flow import record
        from collab.fullrun import CommandRunner
        from collab.state import Card
        wt = self.project.parent / "proj-worktrees" / "CARD-050"
        wt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.wt, wt)
        Card("CARD-050", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", dst="你", summary="复查退满", cause="复查")
        with mock.patch.object(accept, "is_review_stop", return_value=True):
            return accept.accept_remaining(self.project, "CARD-050", runner=CommandRunner(self.COMMAND))

    def test_only_waiting_accepted(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        self.assertTrue(self.accept().get("ok"))

    def test_outside_blocks(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False)), "tests/old.test.mjs": _node_test(("老一", False))})
        self.assertFalse(self.accept().get("ok"))

    def test_broken_list_blocks_with_reason(self):
        from collab import known_fail
        self.register("tests/accept.test.mjs")
        known_fail._path(self.project).write_text("{", encoding="utf-8")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        got = self.accept()
        self.assertFalse(got.get("ok"))
        self.assertIn("名单读不懂", str(got))


class Round3Counts(unittest.TestCase):
    def test_executed_count(self):
        from collab.fullrun import executed_count as e
        self.assertEqual(e("Ran 1 test in 0s\n\nOK (skipped=3)"), 0, "子测试全跳过：不会算成负数")
        self.assertEqual(e("== 1 xfailed in 0.1s =="), 0, "只标了预期失败、正文没跑：不算")
        self.assertEqual(e("1 passed in 0.00s"), 1, "pytest 简短汇总")
        self.assertEqual(e("===== tests/a.py =====\nRan 1 test in 0s\n\nOK\nRan 1 tests in 0s\n\nOK", merged=True), 1,
                         "程序逐文件跑：逐文件 + 总汇总不重复加")
        self.assertEqual(e("ℹ tests 1\nℹ pass 1\nx\nℹ tests 0\nℹ pass 0"), 1, "组合命令几段相加")
        self.assertIsNone(e("没有汇总"))


@unittest.skipUnless(NODE, "需要 node")
class Round3Command(GateCommand):
    def test_auto_discovery_zero_not_passed(self):
        self.register("tests/accept.test.mjs")
        (self.wt / "tests" / "old.test.mjs").unlink()
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "只剩等卡")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        verdict, stop = self.finish("node --test")
        self.assertFalse(verdict.result == PASS and stop == "")

    def test_flow_states_real_count(self):
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        self.assertEqual(self.finish(), (Verdict(PASS, 0, ""), ""))
        self.assertTrue(any("实际跑了 2 项" in s for s in self.flow_summaries()), self.flow_summaries())

    def test_symlinked_dir_in_path_not_removed(self):
        self.register("queue/wait.test.mjs")
        (self.wt / "next").mkdir()
        (self.wt / "queue").symlink_to("tests")
        self.write({"tests/wait.test.mjs": _node_test(("等卡", True)),
                    "tests/old.test.mjs": _node_test(("老一", True), ("老二", False))})
        verdict, stop = self.finish()
        self.assertFalse(verdict.result == PASS and stop == "", "路径经过目录快捷方式：不认，免得拿掉别处的老测试")


class Round3Python(_Gate):
    def base(self):
        self.write({"tests/test_old.py": _py_test(test_a=True), "tests/test_wait.py": _py_test(test_x=False)})

    def test_code_changed_between_runs_voids_result(self):
        from collab.dispatch import _FullSlot
        from collab.fullrun import FullRunner
        self.register("tests/test_wait.py")
        runner = FullRunner()
        real = runner.run

        def run(worktree, stop=None, without=()):
            if without:
                self.write({"tests/test_old.py": _py_test(test_a=False)})   # 两遍之间把老功能改坏
            return real(worktree, stop, without=without)
        runner.run = run
        slot = _FullSlot(runner, self.wt, self.cdir)
        slot.start()
        verdict, stop = slot.finish(1)
        self.assertIn("作废", stop)


class Round3List(unittest.TestCase):
    def test_merge_without_list_creates_no_lock(self):
        from collab import merge
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            merge._drop_waiting(project, "CARD-001")
            self.assertFalse((project / ".collab" / "已知没过.lock").exists())

    def test_readonly_lock_still_readable(self):
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="C", why="w")
            lock = project / ".collab" / "已知没过.lock"
            os.chmod(lock, 0o444)
            try:
                self.assertEqual(len(known_fail.load(project)), 1)
            finally:
                os.chmod(lock, 0o644)

    def test_reader_waits_for_writer(self):
        import threading
        import time
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="C", why="w")
            held = threading.Event()

            def writer():
                with known_fail._locked(project):
                    held.set()
                    time.sleep(0.6)
            t = threading.Thread(target=writer)
            t.start()
            held.wait(5)
            t0 = time.time()
            known_fail.load(project)
            self.assertGreaterEqual(time.time() - t0, 0.4, "读名单要等写的人放手")
            t.join()

    def test_merge_card_really_drops(self):
        from collab import known_fail, merge
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="CARD-031", why="w")
            known_fail.add(project, "tests/b.test.ts", [], "等卡", card="CARD-032", why="w")
            with mock.patch.object(merge, "_merge", return_value=None), \
                    mock.patch.object(merge, "running_merge_ok", create=True, return_value=True):
                try:
                    merge.merge_card(project, "CARD-031")
                except Exception:  # noqa: BLE001 — 只关心撤单
                    pass
            data = json.loads((project / ".collab" / "已知没过.json").read_text(encoding="utf-8"))
            self.assertEqual([i["file"] for i in data["items"]], ["tests/b.test.ts"])


@unittest.skipUnless(NODE, "需要 node")
class Round3Accept(AcceptWithWaiting):
    def test_code_changed_before_state_write_blocks(self):
        from collab import dispatch as d
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        real = d.rerun_without_waiting
        wt = self.project.parent / "proj-worktrees" / "CARD-050"

        def rerun(*a, **kw):
            out = real(*a, **kw)
            (wt / "tests" / "old.test.mjs").write_text(_node_test(("老一", False)), encoding="utf-8")   # 放行判完、写状态前改坏
            return out
        with mock.patch.object(d, "rerun_without_waiting", side_effect=rerun):
            got = self.accept()
        self.assertFalse(got.get("ok"))

    def test_list_removed_before_state_write_blocks(self):
        from collab import dispatch as d, known_fail
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        real = d.rerun_without_waiting

        def rerun(*a, **kw):
            out = real(*a, **kw)
            known_fail.remove(self.project, "tests/accept.test.mjs")
            return out
        with mock.patch.object(d, "rerun_without_waiting", side_effect=rerun):
            got = self.accept()
        self.assertFalse(got.get("ok"))


class Round4(unittest.TestCase):
    def test_counts(self):
        from collab.fullrun import executed_count as e
        self.assertEqual(e("TAP version 13\n1..0\n# tests 1\n# pass 1"), 0, "TAP 写了一项都没有：这一段算 0")
        self.assertEqual(e("Ran 1 test in 0s\n\nOK\nRan 1 test in 0s\n\nOK (skipped=1)"), 1, "项目命令几段各算各的")
        self.assertEqual(e("===== tests/a.py =====\nRan 1 test in 0s\n\nOK\nRan 1 tests in 0s\n\nOK", merged=True), 1)

    def test_register_through_link_refused(self):
        from collab import known_fail
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            (project / "next").mkdir()
            (project / "queue").symlink_to("next")
            with self.assertRaises(ValueError):
                known_fail.add(project, "queue/wait.test.mjs", [], "等卡", card="C", why="w")

    def test_remove_messages(self):
        from collab import known_fail
        from collab.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".collab").mkdir()
            known_fail.add(project, "tests/a.test.ts", [], "等卡", card="C", why="w")
            err = []
            with mock.patch.object(known_fail, "ledger_add", side_effect=OSError("只读")), \
                    mock.patch("sys.stderr") as stderr:
                self.assertEqual(main(["known-fail", "remove", str(project), "--file", "tests/a.test.ts"]), 1)
                err = "".join(c.args[0] for c in stderr.write.call_args_list)
            self.assertIn("已经撤掉", err)
            self.assertEqual(known_fail.load(project), [])


class Round4Python(Round3Python):
    def test_first_registration_mid_run_voids(self):
        from collab.dispatch import _FullSlot
        from collab.fullrun import FullRunner
        slot = _FullSlot(FullRunner(), self.wt, self.cdir)
        slot.start()
        slot.thread.join()
        self.register("tests/test_wait.py")
        self.write({"tests/test_old.py": _py_test(test_a=False)})
        verdict, stop = slot.finish(1)
        self.assertIn("作废", stop)


@unittest.skipUnless(NODE, "需要 node")
class Round4Accept(AcceptWithWaiting):
    def test_remove_waits_until_state_written(self):
        """人工接受写“待验收”时拿着名单的共享锁：撤单要等它写完。"""
        import threading
        import time
        from collab import known_fail
        from collab.state import Card
        self.register("tests/accept.test.mjs")
        self.write({"tests/accept.test.mjs": _node_test(("验收", False))})
        real_save = Card.save
        seen = {}

        def save(card, path):
            if card.state == "待验收":
                t = threading.Thread(target=known_fail.remove, args=(self.project, "tests/accept.test.mjs"))
                t.start()
                time.sleep(0.4)
                seen["blocked"] = t.is_alive()
                real_save(card, path)
                seen["thread"] = t
                return
            return real_save(card, path)
        with mock.patch.object(Card, "save", save):
            got = self.accept()
        seen.get("thread") and seen["thread"].join(5)
        self.assertTrue(got.get("ok"))
        self.assertTrue(seen.get("blocked"), "撤单要等状态写完")


class Round5(unittest.TestCase):
    def test_counts(self):
        from collab.fullrun import executed_count as e
        self.assertEqual(e("✔ /dev/null (1ms)\nℹ tests 1\nℹ suites 0\nℹ pass 1\nℹ skipped 0\nℹ todo 0"), 0,
                         "只是文件加载成功：不算测试正文")
        self.assertEqual(e("TAP version 13\nok 1 - old\n1..1\n# tests 1\n# pass 1\nTAP version 13\n1..0\n# tests 0"), 1,
                         "前段真跑过、后段零项：保留前段")
        self.assertEqual(e("===== unit =====\nRan 1 test in 0s\n\nOK\nRan 1 test in 0s\n\nOK (skipped=1)"), 1,
                         "项目命令打印了像分段标题的日志：照样按段相加")


class Round5Python(Round3Python):
    def test_list_appears_after_check_not_used(self):
        """开跑时没有名单：哪怕交结果途中冒出名单，也不进“拿掉文件再跑”（固定走老规矩）。"""
        from collab.dispatch import _FullSlot
        from collab.fullrun import FullRunner
        runner = FullRunner()
        calls = []
        real = runner.run

        def run(worktree, stop=None, without=()):
            calls.append(tuple(without))
            return real(worktree, stop, without=without)
        runner.run = run
        slot = _FullSlot(runner, self.wt, self.cdir)
        slot.start()
        slot.thread.join()
        real_appeared = slot._list_appeared

        def late(now):
            out = real_appeared(now)
            self.register("tests/test_wait.py")   # 刚查完“没有名单”就登记
            return out
        slot._list_appeared = late
        slot.finish(1)
        self.assertTrue(all(not w for w in calls), "没有拿掉文件再跑")

    def test_list_appears_after_green_first_run_voids(self):
        from collab.dispatch import _FullSlot
        from collab.fullrun import FullRunner
        self.write({"tests/test_wait.py": _py_test(test_x=True)})
        slot = _FullSlot(FullRunner(), self.wt, self.cdir)
        slot.start()
        slot.thread.join()
        self.register("tests/test_wait.py")
        verdict, stop = slot.finish(1)
        self.assertIn("作废", stop)


class Round5Merge(unittest.TestCase):
    def test_merged_state_written_under_list_lock(self):
        src = (Path(__file__).resolve().parents[1] / "collab" / "merge.py").read_text(encoding="utf-8")
        i = src.index('card.transition("已合并")')
        self.assertIn("_list_lock(project)", src[i - 400:i])


@unittest.skipUnless(NODE, "需要 node")
class AcceptFromConfig(AcceptWithWaiting):
    """人工接受从项目配置取全部测试命令（不手动传 runner）。"""

    def accept(self):
        from collab import accept
        from collab.config import save_full_test
        from collab.flow import record
        from collab.state import Card
        save_full_test(self.project, self.COMMAND)
        wt = self.project.parent / "proj-worktrees" / "CARD-050"
        wt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.wt, wt)
        Card("CARD-050", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", dst="你", summary="复查退满", cause="复查")
        with mock.patch.object(accept, "is_review_stop", return_value=True):
            return accept.accept_remaining(self.project, "CARD-050")


class RulesPackage(unittest.TestCase):
    def test_manual_mentions_registering(self):
        text = (Path(__file__).resolve().parents[1] / "agents" / "手册" / "施工单怎么写.md").read_text(encoding="utf-8")
        self.assertIn("collab known-fail add", text)
        self.assertIn("拿掉", text)


if __name__ == "__main__":
    unittest.main()
