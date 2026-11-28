"""v0.96 B1、B2：全部测试那关不再因为“比不出”“测试不稳”停下（docs/跑卡不停半路/方案.md 第 2、3 条）。

B1：项目自配的全部测试命令（train 用 tsx --test，node 的测试工具）改之前也不过时，认出两边没过的是哪几项：
    同一批 → 放行并写明、记以后再修；多出来的 → 只把多出来的退给写代码的；认不出 → 照旧停下。
    11-23 以后 train 停的 15 次里有 5 次是这一类（卡 8、12、13、14、16）。
B2：程序自己逐个文件跑（Python 项目）时，没过的那几项单独跑能过 → 整套再跑一次，过了就放行并记“测试不稳”
    （开发助手卡 26、28 停过 3 次）。
材料：node-*.txt 是真用 node 24 跑出来的输出（两个分组里各有一个同名测试，A 组坏 / B 组坏两种），
train卡16-node.txt 是 11-27 卡 16 真实存下的输出（流转里只存了末尾 6000 字，正文前半截没了）。
复查第 1 轮（Codex）的反例都在这里：同名子测试、TODO、空名单、名单和汇总对不上、命令后面连着别的检查、
第二遍冒出新失败、预期中的失败。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab import fullrun
from collab.dispatch import _chain_rest, _FullSlot
from collab.flow import read_flow
from collab.judge import FAIL, PASS, Verdict

FIX = Path(__file__).parent / "fixtures" / "全部测试输出"
SPEC_A = (FIX / "node-spec-A组坏.txt").read_text(encoding="utf-8")
SPEC_B = (FIX / "node-spec-B组坏.txt").read_text(encoding="utf-8")
TAP_A = (FIX / "node-tap-A组坏.txt").read_text(encoding="utf-8")
TAP_B = (FIX / "node-tap-B组坏.txt").read_text(encoding="utf-8")
CARD16 = (FIX / "train卡16-node.txt").read_text(encoding="utf-8")


class FailingNames(unittest.TestCase):
    def test_spec_names_carry_file_and_group(self):
        names = {fullrun.id_words(n) for n in fullrun.failing_ids(SPEC_A)}
        self.assertEqual(names, {"tests/a.test.mjs::A > same name", "tests/a.test.mjs::A > always",
                                 "tests/a.test.mjs::B > always", "tests/has space/b.test.mjs::same name"})

    def test_same_name_in_another_group_is_a_different_item(self):
        self.assertNotEqual(fullrun.failing_ids(SPEC_A), fullrun.failing_ids(SPEC_B),
                            "A 组的同名测试好了、B 组的坏了：不是同一批")

    def test_tap_gives_the_same_names_and_skips_todo(self):
        self.assertEqual(fullrun.failing_ids(TAP_A), fullrun.failing_ids(SPEC_A))
        self.assertNotIn("todo one", "".join(fullrun.failing_ids(TAP_A)), "TODO 不算没过")
        self.assertNotEqual(fullrun.failing_ids(TAP_A), fullrun.failing_ids(TAP_B))

    def test_todo_turning_real_is_a_new_failure(self):
        real = TAP_A.replace("not ok 5 - todo one # TODO", "not ok 5 - todo one").replace("# fail 4", "# fail 5")
        self.assertEqual({fullrun.id_words(n) for n in fullrun.failing_ids(real) - fullrun.failing_ids(TAP_A)},
                         {"tests/has space/b.test.mjs::todo one"})

    def test_group_named_with_arrow_differs_from_nested_groups(self):
        """复查第 2 轮严重 2：一个叫“A > B”的分组和 A 里套 B 是两回事。"""
        self.assertNotEqual(fullrun._test_id("t.ts", ["A > B", "same"]), fullrun._test_id("t.ts", ["A", "B", "same"]))

    def test_cancelled_tests_are_unreadable(self):
        """复查第 2 轮严重 3：分组的 before() 坏了，下面的测试被取消、根本没跑，不能当成旧失败。"""
        self.assertIsNone(fullrun.failing_ids(TAP_A.replace("# cancelled 0", "# cancelled 1")))
        self.assertIsNone(fullrun.failing_ids(SPEC_A.replace("ℹ cancelled 0", "ℹ cancelled 1")))

    def test_pytest_ids_with_dash_kept_whole(self):
        out = ("FAILED tests/t.py::test_case[old - one] - assert 0\n"
               "===== 1 failed, 1 passed in 0.10s =====\n")
        self.assertEqual(fullrun.failing_ids(out), {"tests/t.py::test_case[old - one]"})

    def test_truncated_card16_output_is_unreadable(self):
        self.assertIsNone(fullrun.failing_ids(CARD16), "正文被截掉、对不上分组：认不出，照旧停")

    def test_count_mismatch_is_unreadable(self):
        self.assertIsNone(fullrun.failing_ids(SPEC_A.replace("ℹ fail 4", "ℹ fail 9")))

    def test_unittest_output(self):
        out = ("FAIL: test_a (tests.test_x.T.test_a)\n----\nERROR: test_b (tests.test_y.U.test_b)\n----\n"
               "Ran 5 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n")
        self.assertEqual(fullrun.failing_ids(out), {"tests.test_x.T.test_a", "tests.test_y.U.test_b"})

    def test_unittest_expected_failures_not_counted(self):
        out = ("FAIL: test_a (tests.test_x.T.test_a)\n----\n"
               "Ran 5 tests in 0.1s\n\nFAILED (failures=1, expected failures=1)\n")
        self.assertEqual(fullrun.failing_ids(out), {"tests.test_x.T.test_a"})

    def test_unittest_unexpected_success_is_unreadable(self):
        out = "Ran 5 tests in 0.1s\n\nFAILED (unexpected successes=1)\n"
        self.assertIsNone(fullrun.failing_ids(out), "意外通过没有名字：认不全")

    def test_pytest_output_including_ids_with_spaces(self):
        out = ("FAILED tests/test_x.py::test_a[id with space] - assert 1 == 2\nERROR tests/test_y.py::test_b\n"
               "===== 1 failed, 3 passed, 1 error in 0.50s =====\n")
        self.assertEqual(fullrun.failing_ids(out),
                         {"tests/test_x.py::test_a[id with space]", "tests/test_y.py::test_b"})

    def test_nothing_recognisable(self):
        self.assertIsNone(fullrun.failing_ids("Error: something exploded\n"))

    def test_chain_rest(self):
        self.assertEqual(_chain_rest("./node_modules/.bin/tsx --test tests/*.test.ts"), [])
        self.assertEqual(_chain_rest("./node_modules/.bin/tsx --test --test-concurrency=1 tests/*.test.ts && pnpm typecheck"),
                         ["pnpm typecheck"])
        self.assertIsNone(_chain_rest("npm test; ./check-build"))
        self.assertIsNone(_chain_rest("npm test && bash -c 'x'"))
        self.assertIsNone(_chain_rest("cd app && pnpm test && pnpm typecheck"), "切了目录：后半段单独补跑会跑在别处")


class _CmdRunner:
    """假的“项目自配命令”：现在跑一次给 now，改之前跑一次给 before。"""

    def __init__(self, now, before, command="./node_modules/.bin/tsx --test tests/*.test.ts"):
        self.command = command
        self.now, self.before = now, before

    def applicable(self, _wt):
        return True

    def run(self, _wt, _stop=None, without=()):
        return self.now

    def base_all(self, _wt, _stop=None):
        return self.before


class _PyRunner:
    """假的“程序逐个文件跑”：run 依次给结果；base、alone 按文件给。"""

    def __init__(self, runs, base, alone):
        self.runs, self.base_map, self.alone_map = list(runs), base, alone
        self.run_calls = 0

    def run(self, _wt, _stop=None, without=()):
        self.run_calls += 1
        return self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]

    def base(self, _wt, files, _stop=None):
        return {f: self.base_map[f] for f in files if f in self.base_map}

    def alone(self, _wt, files, _stop=None):
        return {f: self.alone_map[f] for f in files if f in self.alone_map}


def _ut(*items, extra=""):
    lines = "".join(f"FAIL: {m} ({mod}.T.{m})\n----\n" for mod, m in items)
    n = len(items)
    return f"{lines}Ran 9 tests in 0.1s\n\nFAILED (failures={n}{extra})\n"


class Slot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        (self.project / "tests").mkdir(parents=True)
        self.cdir = self.project / ".collab" / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def finish(self, runner):
        slot = _FullSlot(runner, self.project, self.cdir)
        slot.start()
        out = slot.finish(judge_round=1)
        self.slot = slot
        return out

    def full_events(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "全量"]

    # —— B1 ——
    def test_same_batch_passes_and_is_noted(self):
        verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, SPEC_A), Verdict(FAIL, 1, SPEC_A)))
        self.assertEqual(stop, "", "改之前和改之后没过的是同一批：不停下")
        self.assertEqual(verdict.result, PASS)
        self.assertIn("改之前就不过", self.full_events()[-1].get("summary", ""))
        later = (self.cdir / "以后再修.md").read_text(encoding="utf-8")
        self.assertIn("改之前就不过", later)
        self.assertIn("A > always", later)

    def test_same_name_other_group_goes_back_to_developer(self):
        verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, SPEC_B), Verdict(FAIL, 1, SPEC_A)))
        self.assertEqual(stop, "")
        self.assertEqual(verdict.result, FAIL, "A 组那项修好、B 组同名那项改坏了：退回写代码的")
        self.assertIn("B > same name", verdict.output)
        self.assertNotIn("A > always", verdict.output, "只列多出来的那项，老毛病不算它的")

    def test_extra_list_survives_dev_prompt_trimming(self):
        from collab.dispatch import _short_test_output
        many = SPEC_B
        verdict, _ = self.finish(_CmdRunner(Verdict(FAIL, 1, many), Verdict(FAIL, 1, SPEC_A)))
        self.assertIn("B > same name", _short_test_output(verdict.output), "派活前截短后名单还在（复查第 1 轮中等 4）")

    def test_unreadable_still_stops(self):
        verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, "boom\n"), Verdict(FAIL, 1, "boom\n")))
        self.assertIn("认不出", stop)

    def test_tests_pass_but_other_check_fails_is_not_a_subset(self):
        """复查第 1 轮严重 3：改后测试全过、后面的构建检查没过，名单是空的，不能当成旧失败的子集放行。"""
        ok_tests = SPEC_A.split("✖ failing tests:")[0].replace("ℹ fail 4", "ℹ fail 0").replace("✖", "✔")
        verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, ok_tests), Verdict(FAIL, 1, SPEC_A)))
        self.assertTrue(stop, "认不出是什么没过：停下")

    def test_chained_check_is_run_on_its_own(self):
        cmd = "./node_modules/.bin/tsx --test tests/*.test.ts && pnpm typecheck"
        ran = []

        class Rest:
            def __init__(self, command):
                ran.append(command)

            def run(self, *_a, **_k):
                return Verdict(FAIL, 2, "error TS2322")

        with mock.patch.object(fullrun, "CommandRunner", Rest):
            verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, SPEC_A), Verdict(FAIL, 1, SPEC_A), command=cmd))
        self.assertEqual(ran, ["pnpm typecheck"], "前面测试同一批没过、后面的类型检查两边都没跑到：单独补跑")
        self.assertIn("pnpm typecheck", stop, "补跑没过就停下说明")

    def test_chained_check_passing_lets_it_through(self):
        cmd = "./node_modules/.bin/tsx --test tests/*.test.ts && pnpm typecheck"

        class Rest:
            def __init__(self, command):
                pass

            def run(self, *_a, **_k):
                return Verdict(PASS, 0, "")

        with mock.patch.object(fullrun, "CommandRunner", Rest):
            verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, SPEC_A), Verdict(FAIL, 1, SPEC_A), command=cmd))
        self.assertEqual((verdict.result, stop), (PASS, ""))

    def test_before_passing_still_returns_to_developer(self):
        verdict, stop = self.finish(_CmdRunner(Verdict(FAIL, 1, SPEC_A), Verdict(PASS, 0, "ℹ tests 7\nℹ pass 7\n")))
        self.assertEqual(stop, "")
        self.assertEqual(verdict.result, FAIL)

    # —— B2 ——
    def _files(self, *names):
        for n in names:
            (self.project / "tests" / f"{n}.py").write_text("# t\n", encoding="utf-8")

    def test_shaky_test_reruns_whole_suite_once_and_passes(self):
        self._files("test_flaky")
        runner = _PyRunner([Verdict(FAIL, 1, _ut(("test_flaky", "test_a"))), Verdict(PASS, 0, "Ran 9 tests in 0.1s\n\nOK\n")],
                           base={"tests/test_flaky.py": Verdict(PASS, 0, "OK")},
                           alone={"tests/test_flaky.py": Verdict(PASS, 0, "OK")})
        verdict, stop = self.finish(runner)
        self.assertEqual((verdict.result, stop), (PASS, ""), "单独跑能过、整套再跑一遍也过了：放行")
        self.assertEqual(runner.run_calls, 2, "整套只多跑一遍")
        self.assertIn("测试不稳", (self.cdir / "以后再修.md").read_text(encoding="utf-8"))
        self.assertEqual(self.slot.last_actual.result, PASS, "复查退回到顶那条路看到的是第二遍的结果")

    def test_shaky_test_still_failing_on_rerun_stops_as_before(self):
        self._files("test_flaky")
        bad = Verdict(FAIL, 1, _ut(("test_flaky", "test_a")))
        runner = _PyRunner([bad, bad], base={"tests/test_flaky.py": Verdict(PASS, 0, "OK")},
                           alone={"tests/test_flaky.py": Verdict(PASS, 0, "OK")})
        verdict, stop = self.finish(runner)
        self.assertTrue(stop)
        self.assertEqual(runner.run_calls, 2, "只多跑一遍，不无限跑")

    def test_rerun_with_unnamed_new_failure_stops(self):
        """复查第 1 轮严重 4：第二遍多了一个“意外通过”（没有名字），不能只拿认出的名字比。"""
        self._files("test_flaky", "test_old")
        first = Verdict(FAIL, 1, _ut(("test_flaky", "test_a"), ("test_old", "test_b")))
        second = Verdict(FAIL, 1, "FAIL: test_b (test_old.T.test_b)\n----\nRan 9 tests in 0.1s\n\n"
                                  "FAILED (failures=1, unexpected successes=1)\n")
        runner = _PyRunner([first, second],
                           base={"tests/test_flaky.py": Verdict(PASS, 0, "OK"),
                                 "tests/test_old.py": Verdict(FAIL, 1, _ut(("test_old", "test_b")))},
                           alone={"tests/test_flaky.py": Verdict(PASS, 0, "OK")})
        verdict, stop = self.finish(runner)
        self.assertTrue(stop, "第二遍认不全：照旧停下")

    def test_rerun_leaving_only_an_old_failure_in_a_new_file_passes(self):
        """复查第 1 轮中等 1：第二遍只剩一个改之前就不过的（第一遍没出现过的文件），要去取它的改前结果，不能就此停下。"""
        self._files("test_flaky", "test_other")
        runner = _PyRunner([Verdict(FAIL, 1, _ut(("test_flaky", "test_a"))),
                            Verdict(FAIL, 1, _ut(("test_other", "test_c")))],
                           base={"tests/test_flaky.py": Verdict(PASS, 0, "OK"),
                                 "tests/test_other.py": Verdict(FAIL, 1, _ut(("test_other", "test_c")))},
                           alone={"tests/test_flaky.py": Verdict(PASS, 0, "OK")})
        verdict, stop = self.finish(runner)
        self.assertEqual((verdict.result, stop), (PASS, ""))


if __name__ == "__main__":
    unittest.main()
