"""跑卡卡点修理 卡 B1 验收：认 unittest“哪几项没过”的解析只留 collab/fullrun.py 一处，**老行为一字不改**。

原来散在四处：fullrun.failing_names / failing_files、dispatch._failing / _more_failures、progress.failing_names、
progress.tally 里自己那份。合成后各入口对同样输入的结果必须和合并前完全一样（旧口径里的毛病也原样保留）。
下面把合并前的四份旧实现原样抄在这里当“标准答案”，在真实输出（tests/fixtures/失败输出/，11-26 用 Python 3.9、3.15
真跑出来的）和复查实测过的刁钻写法上逐个对照。
11-26 第三次修订：不新加读 node / pytest 文字输出的解析（B2 改读结构化报告），见 docs/跑卡卡点修理/方案.md 第 ② 类。
"""
import re
import unittest
from collections import Counter
from pathlib import Path

from collab import fullrun

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SAMPLES = HERE / "fixtures" / "失败输出"


# ---- 合并前的旧实现（072f008 原样抄来，只当标准答案） ----
def old_dispatch_failing(output):
    names = set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", output or "", re.M))
    m = re.search(r"FAILED \(([^)]*)\)", output or "")
    total = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", m.group(1))) if m else None
    return (names or None), total


def old_more_failures(now_out, base_out):
    now_names, now_total = old_dispatch_failing(now_out)
    base_names, base_total = old_dispatch_failing(base_out)
    if now_names is not None and base_names is not None and now_names - base_names:
        return True
    return now_total is not None and base_total is not None and now_total > base_total


def old_progress_failing_names(output):
    names = []
    for name, where in re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)", output or "", re.M):
        where = where.strip()
        suffix = "." + name
        base = where[:-len(suffix)] if where.endswith(suffix) else where
        full = f"{base}.{name}" if base else name
        if full not in names:
            names.append(full)
    return names


def old_tally(output):
    m = re.search(r"^Ran (\d+) tests?", output or "", re.M)
    if not m:
        return None
    total = int(m.group(1))
    names = {f"{a} ({b})" for a, b in re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)", output or "", re.M)}
    bad = re.search(r"FAILED \(([^)]*)\)", output or "")
    unexpected = re.search(r"unexpected successes=(\d+)", bad.group(1)) if bad else None
    failed = len(names) + (int(unexpected.group(1)) if unexpected else 0)
    if bad and not names and not unexpected:
        failed = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", bad.group(1)))
    skipped = re.search(r"skipped=(\d+)", output or "")
    return max(0, total - failed - (int(skipped.group(1)) if skipped else 0)), total


_OLD_FAIL_LINE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)(.*)$", re.M)


def old_failing_files(output, root=None):
    files = {}
    for m in _OLD_FAIL_LINE.finditer(output or ""):
        name = f"{m.group(1)} ({m.group(2)}){m.group(3).rstrip()}"
        files.setdefault(fullrun._test_file(m.group(1), m.group(2), root), []).append(name)
    return files


# ---- 对照用的输入：真实输出 + 复查实测过的刁钻写法 ----
def corpus():
    outs = {p.name: p.read_text(encoding="utf-8") for p in sorted(SAMPLES.glob("unittest-*.txt"))}
    outs.update({
        "空": "",
        "全过": "..\n----\nRan 2 tests in 0.1s\n\nOK\n",
        "只有意外通过（3.9 不列名字）": "Ran 3 tests in 0.0s\n\nFAILED (unexpected successes=1)\n",
        "改后 test_new 真失败": "FAIL: test_new (test_old.T)\n----\nRan 3 tests in 0.0s\n\nFAILED (failures=1)\n",
        "多一项预期失败": "FAIL: test_a (m.T) (x=1)\n----\nRan 2 tests in 0.0s\n\nFAILED (failures=1, expected failures=1)\n",
        "同一方法两个真失败": "FAIL: test_a (m.T) (x=1)\nFAIL: test_a (m.T) (x=2)\n----\nRan 2 tests in 0.0s\n\nFAILED (failures=2)\n",
        "同名嵌套类（3.9）": ("FAIL: test_a (__main__.T)\nFAIL: test_a (__main__.T.test_a)\n----\n"
                         "Ran 2 tests in 0.0s\n\nFAILED (failures=2)\n"),
        "子测试换了一个": "FAIL: test_a (m.T) (x=2)\n----\nRan 2 tests in 0.0s\n\nFAILED (failures=1)\n",
        "导入报错占位": ("ERROR: test_missing (unittest.loader._FailedTest.test_missing)\n----\n"
                   "Ran 1 test in 0.0s\n\nFAILED (errors=1)\n"),
        "打印了历史日志": ("FAIL: test_old (previous.T)\n----\nRan 1 test in 0.0s\n\nFAILED (unexpected successes=1)\n"),
        "只有汇总没有名字": "Ran 4 tests in 0.0s\n\nFAILED (failures=2, errors=1, skipped=1)\n",
        # 复查第 4 轮：前面打印过一条旧汇总（旧版取第一条）；没收尾的 FAIL 标题连着下一条（旧版连读，旧毛病保留）
        "前面有旧汇总": ("FAILED (failures=1)\nFAIL: test_a (m.T) (x=1)\nFAIL: test_a (m.T) (x=2)\n----\n"
                     "Ran 2 tests in 0.0s\n\nFAILED (failures=2)\n"),
        # 复查第 5 轮：子测试用文字名称（标题尾部 [读] / [写]）
        "文字子测试-读": "FAIL: test_io (test_review.T.test_io) [读]\n----\nRan 1 test in 0.0s\n\nFAILED (failures=1)\n",
        "文字子测试-写": "FAIL: test_io (test_review.T.test_io) [写]\n----\nRan 1 test in 0.0s\n\nFAILED (failures=1)\n",
        "没收尾的标题": "FAIL: cached (\nERROR: test_new (m.T)\n----\nRan 1 test in 0.0s\n\nFAILED (errors=1)\n",
    })
    return outs


class SameAsBefore(unittest.TestCase):
    def test_progress_tally(self):
        from collab.progress import tally
        for name, out in corpus().items():
            with self.subTest(name):
                self.assertEqual(tally(out), old_tally(out))

    def test_red_list_methods(self):
        for name, out in corpus().items():
            with self.subTest(name):
                self.assertEqual(fullrun.failing_methods(out), old_progress_failing_names(out))

    def test_failing_files_with_and_without_root(self):
        for name, out in corpus().items():
            for root in (None, ROOT):
                with self.subTest(name, root=root):
                    self.assertEqual(fullrun.failing_files(out, root), old_failing_files(out, root))

    def test_more_failures_every_pair(self):
        outs = list(corpus().items())
        for (an, a) in outs:
            for (bn, b) in outs:
                with self.subTest(now=an, base=bn):
                    self.assertEqual(fullrun.more_failures(a, b), old_more_failures(a, b))

    def test_culprits_every_pair(self):
        from collab.judge import FAIL, Verdict
        outs = list(corpus().values())
        for a in outs:
            for b in outs:
                now = Verdict(FAIL, 1, a)
                base = {f: Verdict(FAIL, 1, b) for f in fullrun.failing_files(a, ROOT)}
                self.assertEqual(fullrun.culprits(now, base, ROOT), self._old_culprits(now, base, ROOT))

    @staticmethod
    def _old_culprits(now, base, root):
        from collab.judge import FAIL
        out = []
        for rel, names in sorted(old_failing_files(now.output, root).items()):
            before = base.get(rel)
            if before is None or before.result != FAIL:
                out.append(rel)
                continue
            was = Counter(n for names_b in old_failing_files(before.output, root).values() for n in names_b)
            if Counter(names) - was:
                out.append(rel)
        return out


class EntriesStillWork(unittest.TestCase):
    """错误做法要打得红：入口真的在用这份解析，而且判对了方向。"""

    def test_broken_by_change_both_directions(self):
        from unittest import mock
        from collab import dispatch
        from collab.judge import FAIL, PASS, Verdict

        def run(now_text, base_text):
            seq = []

            def judge(cmd, cwd=None, timeout=None):
                seq.append(cwd)
                text = now_text if len(seq) == 1 else base_text
                return Verdict(FAIL if text else PASS, 1 if text else 0, text)

            with mock.patch("subprocess.run") as sp:
                sp.return_value = mock.Mock(returncode=0)
                return dispatch._broken_by_change("/tmp", ["tests/test_t.py"], judge)

        more = "FAIL: test_a (t.T)\nFAIL: test_b (t.T)\n----\nFAILED (failures=2)\n"
        less = "FAIL: test_a (t.T)\n----\nFAILED (failures=1)\n"
        self.assertEqual(run(more, less), ["tests/test_t.py"], "改后多坏一项：算这次改坏")
        self.assertEqual(run(less, less), [], "改前改后一样：不算这次改坏")
        self.assertEqual(run(less, ""), ["tests/test_t.py"], "改前是过的：算这次改坏")
        self.assertEqual(run(less, more), [], "改前坏两项、现在只坏一项：不算这次改坏（复查第 3 轮）")
        for v in ("39", "315"):   # 一行没收尾的日志不能吞掉下一条真失败（复查第 3 轮，真跑出来的输出）
            new = (SAMPLES / f"unittest-hang{v}-new.txt").read_text(encoding="utf-8")
            old = (SAMPLES / f"unittest-hang{v}-old.txt").read_text(encoding="utf-8")
            with self.subTest(python=v):
                self.assertEqual(run(new, old), ["tests/test_t.py"], "原本过的 test_new 变红：算这次改坏")

    def test_progress_job_counts(self):
        """施工进度经真实入口算：不给先红名单按通过数；给了名单只数名单里的（复查第 3 轮：只测 tally 时恒报零也能过）。"""
        import json
        import tempfile
        from unittest import mock
        from collab import progress
        from collab.judge import FAIL, Verdict
        out = (SAMPLES / "unittest-hang315-new.txt").read_text(encoding="utf-8")
        cmd = 'python3 -m unittest discover -s tests -p "test_review.py"'
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(progress, "copy_tree"), \
                mock.patch.object(progress, "run_command", return_value=Verdict(FAIL, 1, out)):
            job = progress.ProgressJob(tmp, tmp, "写代码", 1, [], test_command=cmd)
            got = job.compute()
            self.assertEqual((got.get("passed"), got.get("total")), (1, 3), got)
            (Path(tmp) / "开工前没过的测试.json").write_text(json.dumps(
                {"tests": ["test_review.T.test_anchor", "test_review.T.test_new"]}), encoding="utf-8")
            got = job.compute()
            self.assertEqual((got.get("passed"), got.get("total"), got.get("own")), (0, 2, True), got)
            (Path(tmp) / "开工前没过的测试.json").write_text(json.dumps(
                {"tests": ["test_review.T.test_anchor", "test_review.T.test_new", "test_review.T.test_ok"]}),
                encoding="utf-8")
            got = job.compute()
            self.assertEqual((got.get("passed"), got.get("total")), (1, 3), got)

    def test_short_note_keeps_message_of_bare_title(self):
        """只有“FAIL: 名字”没有括号位置时，交接摘要照旧带上断言消息（复查第 3 轮）。"""
        from collab import dispatch
        note = dispatch._short_test_output("连接缓存 ok\nFAIL: incomplete\nAssertionError: bad\n")
        self.assertIn("FAIL: incomplete", note)
        self.assertIn("AssertionError: bad", note)
        self.assertNotIn("连接缓存", note, "认出是失败块就只交失败项，不是退回“留最后几行”")
        note = dispatch._short_test_output("连接缓存 ok\nERROR: test_io (m.T)\nOSError: disk\n----\nFAILED (errors=1)\n")
        self.assertIn("ERROR: test_io (m.T)", note)
        self.assertIn("OSError: disk", note)
        self.assertNotIn("连接缓存", note, "只有 ERROR 的也认得出")
        # 两条失败紧挨着、中间没分隔线：各归各的（复查第 5 轮）
        note = dispatch._short_test_output("FAIL: a (m.T)\nAssertionError: A\nERROR: b (m.T)\n" + "trace\n" * 8 + "OSError: B\n")
        for piece in ("FAIL: a (m.T)", "AssertionError: A", "ERROR: b (m.T)", "OSError: B"):
            self.assertIn(piece, note)
        self.assertLess(note.index("AssertionError: A"), note.index("ERROR: b (m.T)"))
        self.assertLess(note.index("ERROR: b (m.T)"), note.index("OSError: B"))
        self.assertTrue(fullrun.UT_FAIL_START.search("准备日志\nFAIL: x (m.T)\n"), "失败块开头按行认")

    def test_unreadable_total_lists_all_like_before(self):
        """总数读不出来时（数字超长）旧版报错、外层把本批没过的全列出；顺序不能变（复查第 4 轮）。"""
        import sys
        from unittest import mock
        from collab import dispatch
        from collab.judge import FAIL, Verdict
        if not hasattr(sys, "set_int_max_str_digits"):
            self.skipTest("这版 Python 没有数字位数上限")
        files = ["tests/test_a.py", "tests/test_b.py"]

        def judge(cmd, cwd=None, timeout=None):
            changed = "test_a.py" in cmd[-1] and cwd == "now"
            log = "FAILED (failures=" + "9" * 641 + ")\n" if changed else ""
            name = "test_new" if changed else "test_old"
            return Verdict(FAIL, 1, log + f"FAIL: {name} (m.T)\nRan 1 test\nFAILED (failures=1)")

        old = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(640)
        try:
            with mock.patch("tempfile.mkdtemp", return_value="base"), mock.patch("shutil.rmtree"), \
                    mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)):
                self.assertEqual(dispatch._broken_by_change("now", files, judge), files)
        finally:
            sys.set_int_max_str_digits(old)

    def test_red_check_writes_method_list(self):
        import json
        import tempfile
        from collab import dispatch
        from collab.judge import FAIL, Verdict
        out = (SAMPLES / "unittest-py315.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "card"
            cdir.mkdir()
            wt = Path(tmp) / "wt"
            wt.mkdir()
            dispatch._red_check_one(cdir, "写测试", "python3 -m unittest discover -s tests -p \"test_old.py\"",
                                    lambda *a, **k: Verdict(FAIL, 1, out), wt, reply_seq=1)
            saved = json.loads((cdir / "开工前没过的测试.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["tests"], ["test_old.T.test_b", "test_old.T.test_a"])


class KnownAnswers(unittest.TestCase):
    """有明确答案的例子：标准答案和现实现共用的部分（找测试文件）错了也要打得红（复查第 3 轮）。"""

    def test_nested_class_file(self):
        out = "FAIL: test_x (test_flowfix4_progress.Outer.Inner.test_x)\n"
        self.assertEqual(list(fullrun.failing_files(out, ROOT)), ["tests/test_flowfix4_progress.py"])
        self.assertEqual(list(fullrun.failing_files(out)), ["tests/test_flowfix4_progress/Outer.py"], "不给项目照旧口径")

    def test_named_subtest_changed_counts(self):
        from collab.judge import FAIL, Verdict

        def v(s):
            return Verdict(FAIL, 1, f"FAIL: test_io (test_review.T.test_io) [{s}]\n")
        self.assertEqual(fullrun.culprits(v("写"), {"tests/test_review.py": v("读")}), ["tests/test_review.py"],
                         "同一方法换了一个文字子测试没过：算新坏")

    def test_same_subtest_twice_counts(self):
        from collab.judge import FAIL, Verdict
        line = "FAIL: test_a (test_t.T) (x=1)\n"
        now = Verdict(FAIL, 1, line + line + "Ran 2 tests in 0.0s\n\nFAILED (failures=2)\n")
        base = {"tests/test_t.py": Verdict(FAIL, 1, line + "Ran 2 tests in 0.0s\n\nFAILED (failures=1)\n")}
        self.assertEqual(fullrun.culprits(now, base, None), ["tests/test_t.py"], "同一行多坏一次也算")


class OnePlaceOnly(unittest.TestCase):
    def test_old_parsers_gone(self):
        from collab import dispatch, progress
        self.assertFalse(hasattr(dispatch, "_failing"))
        self.assertFalse(hasattr(dispatch, "_more_failures"))
        self.assertFalse(hasattr(progress, "failing_names"))
        self.assertFalse(hasattr(fullrun, "failing_names"))

    def test_every_entry_reads_the_shared_parser(self):
        """各入口真的都从 fullrun 那一份取数：把它换成“一项也认不出”，各入口的结果都得跟着变（复查第 4 轮：只搜写法会漏）。"""
        from unittest import mock
        from collab import progress
        out = (SAMPLES / "unittest-py315.txt").read_text(encoding="utf-8")
        base = "FAIL: test_z (test_old.T.test_z)\n----\nRan 4 tests in 0.0s\n\nFAILED (failures=1, errors=1)\n"   # 总数一样、名字换了
        before = (progress.tally(out), fullrun.failing_methods(out), fullrun.failing_files(out),
                  fullrun.more_failures(out, base))
        with mock.patch.object(fullrun, "_ut_failed", return_value=[]):
            after = (progress.tally(out), fullrun.failing_methods(out), fullrun.failing_files(out),
                     fullrun.more_failures(out, base))
        for label, b, a in zip(("进度", "先红名单", "失败文件", "比较"), before, after):
            with self.subTest(label):
                self.assertNotEqual(b, a)
        # 先红入口也得从这一份取：认不出就不写名单（复查第 5 轮）
        import tempfile
        from collab import dispatch
        from collab.judge import FAIL, Verdict
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fullrun, "_ut_failed", return_value=[]):
            dispatch._red_check_one(Path(tmp), "写测试", 'python3 -m unittest discover -s tests -p "test_old.py"',
                                    lambda *a, **k: Verdict(FAIL, 1, out), tmp, reply_seq=1)
            try:
                written = (Path(tmp) / "开工前没过的测试.json").read_text(encoding="utf-8")
            except OSError:
                written = None
            self.assertIsNone(written, "公共解析认不出时先红入口不该自己认出名单")

    def test_unittest_line_rule_written_once(self):
        hits = [p.name for p in (ROOT / "collab").glob("*.py")
                if re.search(r"\(\?:FAIL\|ERROR|\(FAIL\|ERROR", p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, ["fullrun.py"], hits)

    def test_no_text_guessing_for_other_tools(self):
        self.assertFalse(hasattr(fullrun, "failing_tests"), "读 node / pytest 打印文字推断结果不做（B2 读结构化报告）")
        pytest_out = ("FAILED tests/test_x.py::test_a - AssertionError: 1 != 2\n"
                      "ERROR tests/test_y.py::test_b - OSError\n=== 1 failed, 1 error in 0.1s ===\n")
        self.assertEqual(fullrun._ut_failed(pytest_out), [], "公共解析只认 unittest")
        node_out = (SAMPLES / "node24-spec.txt").read_text(encoding="utf-8")   # node 24 真跑出来的（反例）
        self.assertEqual(fullrun._ut_failed(node_out), [], "公共解析不读 node 文字")
        self.assertEqual(fullrun.failing_files(pytest_out), {})


if __name__ == "__main__":
    unittest.main()
