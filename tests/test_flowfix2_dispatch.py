"""流程修理第二轮：派活和交接（㉝ ㉞ ㊲ ㊻ ㊱）。

69 里看到的：复查退回后改完又写“第 1 轮：测试全部通过”（和第一次一模一样）；行政只说“自动退回（第 N 次）”不说挑出了什么；
测试员把积木块编号写错，写代码的只能迁就错测试，复查连着两轮挑同一条；停下时只说“已退回 2 次”，不说为什么、该怎么办。
"""
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import plain_issues, run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.state import Card

# 69 复查第 2 轮的真实写法
REVIEW_69 = """结论：不通过

1. **[P1] 把服务商名当成积木块编号，绕过了“积木块不存在”的校验。** [tasks.py:587](/Users/rs/x/desktop/clerk/tasks.py:587) 会按 `provider` 查找并改写编号。
2. **[P2] 关联能力较多时，必需的结尾提醒会被截掉。** [blocks_tools.py:489](/Users/rs/x/blocks_tools.py:489) 只限制模型名单长度。

指定测试命令已完整运行：466 项全部通过。"""


class PlainIssues(unittest.TestCase):
    def test_first_sentence_without_paths_or_marks(self):
        self.assertEqual(plain_issues(REVIEW_69), [
            "把服务商名当成积木块编号，绕过了“积木块不存在”的校验",
            "关联能力较多时，必需的结尾提醒会被截掉",
        ])

    def test_no_numbered_items(self):
        self.assertEqual(plain_issues("结论：不通过\n整体思路不对。"), [])

    def test_long_item_is_cut(self):
        items = plain_issues("结论：不通过\n1. " + "很长" * 80 + "。后面")
        self.assertLessEqual(len(items[0]), 60)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, reviews, tester_fix=None):
        calls = {"dev": [], "tester": [], "review": []}
        reviews = list(reviews)
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"].append(prompt)
                return AgentResult(ok=True, output="过程", attempts=1, final=reviews.pop(0))
            if "测试本身写错了" in prompt:
                calls["tester"].append(prompt)
                (wt / "tests" / "test_a.py").write_text("# 改好的测试\n", encoding="utf-8")
                if tester_fix:
                    tester_fix(wt)
                return AgentResult(ok=True, output="改了测试", attempts=1, final="改了测试")
            if "编写自动化测试" in prompt:
                calls["tester"].append(prompt)
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="写了测试", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            return AgentResult(ok=True, output="改了", attempts=1, final="汇报：改了")

        card = Card("CARD-001", state="待派发")
        card, _ = run_card(card, self.collab, self.worktree, "true", "agy", "codex", ask_fn=ask_fn,
                           judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                           changed_fn=lambda w: [], allowed=None)
        return card, calls

    def events(self, kind):
        """say：给人看的完整一句话（summary 只留 80 字）"""
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind]


class RoundWords(Base):
    """㉝：复查退回后改完，判定要写来历，不能和第一次一模一样。"""

    def test_after_review_return(self):
        self.run_with(["结论：不通过\n1. 按钮还是红的。", "结论：通过"])
        sums = [e["summary"] for e in self.events("判定")]
        self.assertEqual(sums[0], "第 1 轮：测试全部通过")
        self.assertEqual(sums[1], "复查退回后第 1 次改完：测试全部通过")


class ReviewSaysWhat(Base):
    """㉞ ㊲：退回、停下时用大白话说复查挑出了什么。"""

    def test_return_lists_issues(self):
        self.run_with([REVIEW_69, "结论：通过"])
        back = self.events("退回")[0]["say"]
        self.assertIn("复查挑出 2 件事", back)
        self.assertIn("① 把服务商名当成积木块编号", back)
        self.assertIn("② 关联能力较多时", back)
        self.assertNotIn("tasks.py", back)
        self.assertNotIn("**", back)

    def test_reviewer_is_asked_for_plain_first_sentence(self):
        _, calls = self.run_with(["结论：通过"])
        self.assertIn("第一句用大白话", calls["review"][0])
        self.assertIn("【测试的问题】", calls["review"][0])

    def test_stop_summary_is_plain(self):
        self.run_with([REVIEW_69] * 3)
        stop = self.events("转人工")[-1]
        self.assertIn("复查还剩 2 件事", stop["say"])
        self.assertNotIn("tasks.py", stop["say"])
        self.assertEqual(stop.get("issues"), ["把服务商名当成积木块编号，绕过了“积木块不存在”的校验",
                                             "关联能力较多时，必需的结尾提醒会被截掉"])


class StopShownPlain(Base):
    """㊲：卡住时右上角说明和“接受剩下的问题”确认框里是大白话，原话去“交接”里看。"""

    def test_phase_uses_plain_words(self):
        from collab.phase import card_phase
        self.run_with([REVIEW_69] * 3)
        phase = card_phase(str(self.collab.parent), "CARD-001")
        self.assertIn("复查还剩 2 件事", phase["detail"])
        self.assertIn("1. 把服务商名当成积木块编号", phase["issues"])
        self.assertNotIn("tasks.py", phase["issues"])
        self.assertIn("交接", phase["issues"])


class OldCardStopIsPlain(Base):
    """复查 A 第 5 条：老卡没存 issues、复查员没按格式写时，确认框也不塞原文。"""

    def test_old_stop_without_issues(self):
        from collab.flow import record as rec
        from collab.phase import card_phase
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")
        rec(self.cdir, "转人工", src="程序", dst="调度员", cause="复查不通过", summary="复查不通过",
            text="结论：不通过\n**desktop/server.py:123** 里 activeChecks() 没清掉。")
        phase = card_phase(str(self.collab.parent), "CARD-001")
        self.assertNotIn("server.py", phase["issues"])
        self.assertNotIn("activeChecks", phase["issues"])
        self.assertIn("交接", phase["issues"])


class TestIsWrong(Base):
    """㊻：复查说是测试写错了，先让写测试的改测试，再让写代码的照着改，不逼写代码的迁就错测试。"""

    def test_tester_fixes_tests_first(self):
        review = ("结论：不通过\n1. 【测试的问题】测试把联网搜索积木块编号写成 tavily，实际编号是 web-search。\n"
                  "2. 编号写错也能出草稿。")
        card, calls = self.run_with([review, "结论：通过"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["tester"]), 2, "写测试 1 次 + 改测试 1 次")
        self.assertIn("web-search", calls["tester"][1])
        self.assertIn("只改测试", calls["tester"][1])
        self.assertNotIn("编号写错也能出草稿", calls["tester"][1], "不是测试的问题不交给写测试的")
        self.assertIn("测试已由写测试的改好", calls["dev"][1])
        steps = [e.get("step") for e in self.events("派活")]
        self.assertIn("改测试", steps)
        self.assertLess(steps.index("改测试"), len(steps) - 1 - steps[::-1].index("开发"))

    def test_tester_cannot_touch_product_code_while_fixing(self):
        import subprocess
        (self.worktree / "app.py").write_text("原来的\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=self.worktree, check=True, capture_output=True)

        def sneaky(wt):
            (wt / "app.py").write_text("写测试的偷改\n", encoding="utf-8")

        review = "结论：不通过\n1. 【测试的问题】测试写错了。"
        calls = {"n": 0}
        reviews = [review, "结论：通过"]
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "测试本身写错了" in prompt:
                sneaky(wt)
                return AgentResult(ok=True, output="", attempts=1, final="改了")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            calls["n"] += 1
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "agy", "codex",
                 ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
        self.assertEqual((self.worktree / "app.py").read_text(encoding="utf-8"), "原来的\n")


class StopReason(Base):
    """㊱：自动退回用完停下时，说清是哪种情况、建议怎么办。"""

    def test_same_issue_again_and_again(self):
        self.run_with([REVIEW_69] * 3)
        stop = self.events("转人工")[-1]["say"]
        self.assertIn("同一个问题反复出现", stop)
        self.assertIn("换", stop)

    def test_new_issues_every_round(self):
        self.run_with(["结论：不通过\n1. 甲的按钮是红的。", "结论：不通过\n1. 乙页面打不开。",
                       "结论：不通过\n1. 丙的数字算错了。"])
        stop = self.events("转人工")[-1]["say"]
        self.assertIn("每轮都冒出新问题", stop)
        self.assertIn("继续改", stop)

    def test_return_counts_for_whole_card(self):
        self.run_with(["结论：不通过\n1. 甲。", "结论：通过"])
        self.assertIn("整张卡第 2 次写代码", self.events("退回")[0]["say"])


if __name__ == "__main__":
    unittest.main()


class Handover(Base):
    """㉚ ㊺：开工时调度员备交接资料（和写测试同时），写代码、复查派活时都附上；没备成不挡流程。"""

    def _go(self, handover_ok=True, tests_done=False):
        order = []
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "准备交接资料" in prompt:
                order.append(("handover", kw.get("models", {})))
                if not handover_ok:
                    return AgentResult(ok=False, output="超时", attempts=1, final="")
                return AgentResult(ok=True, output="", attempts=1,
                                   final="## 要改的文件\n- app.py 的 save()：现在不查编号")
            if "只读复查" in prompt:
                order.append(("review", prompt))
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                order.append(("tester", prompt))
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            order.append(("dev", prompt))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = Card("CARD-001", state="待派发")
        run_card(card, self.collab, self.worktree, "true", "agy", "codex", ask_fn=ask_fn,
                 judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                 handover=("codex", {"codex": "gpt-6.1-sol", "codex_effort": "low"}))
        return order

    def test_notes_written_and_given_to_dev_and_reviewer(self):
        order = self._go()
        notes = self.cdir / "交接资料.md"
        self.assertTrue(notes.is_file())
        self.assertIn("app.py 的 save()", notes.read_text(encoding="utf-8"))
        kinds = [k for k, _ in order]
        self.assertLess(kinds.index("handover"), kinds.index("dev"), "写代码前就备好")
        dev = [p for k, p in order if k == "dev"][0]
        review = [p for k, p in order if k == "review"][0]
        self.assertIn(str(notes), dev)
        self.assertIn(str(notes), review)
        models = [m for k, m in order if k == "handover"][0]
        self.assertEqual(models.get("codex_sandbox"), "read-only", "调度员备资料只读，不能改副本")
        self.assertTrue(self.events("交接资料"))

    def test_failed_notes_do_not_block(self):
        order = self._go(handover_ok=False)
        self.assertFalse((self.cdir / "交接资料.md").exists())
        dev = [p for k, p in order if k == "dev"][0]
        self.assertNotIn("交接资料", dev)
        self.assertEqual(Card.load(self.collab / "cards" / "CARD-001" / "状态.json").state, "待验收")

    def test_codex_read_only_switch(self):
        from collab.agents import codex_command, _build
        cmd = _build("codex", "备资料", {"codex": "gpt-6.1-sol", "codex_effort": "low", "codex_sandbox": "read-only"})
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertEqual(codex_command("x")[codex_command("x").index("-s") + 1], "workspace-write")


class ReviewRound1B(Base):
    """Codex 复查第 1 轮 B 块：改测试那一步不能动写代码的成果、删掉的错测试不能被旧快照复活、
    没改成不锁半成品、交给写测试的要有原因和位置；停下判断要有“只剩小问题”；大白话里不出文件名、函数名。"""

    def _git(self):
        import subprocess
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=self.worktree, check=True, capture_output=True)

    def _fix_run(self, tester_does, review_text, tester_ok=True):
        wt = self.worktree
        reviews = [review_text, "结论：通过"]
        seen = {"tester": [], "judged": []}

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "测试本身写错了" in prompt:
                seen["tester"].append(prompt)
                tester_does(wt)
                return AgentResult(ok=tester_ok, output="", attempts=1, final="改了" if tester_ok else "")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                (wt / "tests" / "test_bad.py").write_text("# 写错的测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            if not (wt / "app.py").read_text(encoding="utf-8").startswith("写代码的"):
                (wt / "app.py").write_text("写代码的成果\n", encoding="utf-8")
                if (wt / "old.py").exists():
                    (wt / "old.py").unlink()
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        def judge(*a, **k):
            seen["judged"].append(sorted(p.name for p in (wt / "tests").glob("*.py")))
            return Verdict(PASS, 0, "ok")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "agy", "codex",
                 ask_fn=ask_fn, judge_fn=judge, reviewer_adapter="codex")
        return seen

    def setUp(self):
        super().setUp()
        (self.worktree / "app.py").write_text("原版\n", encoding="utf-8")
        (self.worktree / "old.py").write_text("要删的\n", encoding="utf-8")
        self._git()

    def test_tester_cannot_undo_developer_work(self):
        def tester(wt):
            (wt / "app.py").write_text("原版\n", encoding="utf-8")       # 改回原版
            (wt / "old.py").write_text("要删的\n", encoding="utf-8")      # 把写代码的删掉的建回来
            (wt / "tests" / "test_a.py").write_text("# 改好的\n", encoding="utf-8")
        self._fix_run(tester, "结论：不通过\n1. 【测试的问题】测试写错了。")
        self.assertEqual((self.worktree / "app.py").read_text(encoding="utf-8"), "写代码的成果\n")
        self.assertFalse((self.worktree / "old.py").exists())

    def test_deleted_wrong_test_stays_deleted(self):
        def tester(wt):
            (wt / "tests" / "test_bad.py").unlink()
        seen = self._fix_run(tester, "结论：不通过\n1. 【测试的问题】test_bad 那个测试本身就错了，删掉。")
        self.assertNotIn("test_bad.py", seen["judged"][-1])

    def test_failed_fix_does_not_lock_half_done_tests(self):
        def tester(wt):
            (wt / "tests" / "test_a.py").write_text("def (:\n", encoding="utf-8")
        self._fix_run(tester, "结论：不通过\n1. 【测试的问题】测试写错了。", tester_ok=False)
        self.assertEqual((self.worktree / "tests" / "test_a.py").read_text(encoding="utf-8"), "# 测试\n")

    def test_tester_gets_reason_and_place(self):
        seen = self._fix_run(lambda wt: None, "结论：不通过\n1. 【测试的问题】联网搜索的草稿出不来。\n"
                                              "   原因：tests/test_a.py 里积木块编号写成 tavily，应改成 web-search。\n"
                                              "2. 别的问题。")
        self.assertIn("应改成 web-search", seen["tester"][0])
        self.assertNotIn("别的问题", seen["tester"][0])


class SmallAndPlain(Base):
    def test_only_small_issues_left(self):
        self.run_with(["结论：不通过\n1. [P3] 提示里少一个标点。",
                       "结论：不通过\n1. [P3] 说明里有个错别字。",
                       "结论：不通过\n1. [P3] 按钮文字多一个空格。"])
        self.assertIn("剩下的都是小问题", self.events("转人工")[-1]["say"])

    def test_item_with_code_names_is_not_plain(self):
        """第 2 轮 B4：第一句里带文件名、函数名的不算大白话（硬删会剩半句），只计数，原话去“交接”里看"""
        self.assertEqual(plain_issues("结论：不通过\n1. 保存时 `app.py` 里 validate_id() 没查编号，草稿能存进去。"), [])
        self.run_with(["结论：不通过\n1. 保存时 `app.py` 没查编号。\n2. 草稿没放进去也显示已放进能力层。", "结论：通过"])
        say = self.events("退回")[0]["say"]
        self.assertIn("复查挑出 2 件事", say)
        self.assertIn("另有 1 件没写成大白话", say)
        self.assertNotIn("app.py", say)


class ReviewRound2B(ReviewRound1B):
    """Codex 复查第 2 轮 B 块。"""

    def test_tester_deleting_untouched_file_is_undone(self):
        (self.worktree / "keep.py").write_text("别动\n", encoding="utf-8")
        self._git()
        def tester(wt):
            (wt / "keep.py").unlink()
        self._fix_run(tester, "结论：不通过\n1. 【测试的问题】测试写错了。")
        self.assertEqual((self.worktree / "keep.py").read_text(encoding="utf-8"), "别动\n")

    def test_failed_fix_removes_new_half_tests(self):
        def tester(wt):
            (wt / "tests" / "test_new_half.py").write_text("def (:\n", encoding="utf-8")
        self._fix_run(tester, "结论：不通过\n1. 【测试的问题】测试写错了。", tester_ok=False)
        self.assertFalse((self.worktree / "tests" / "test_new_half.py").exists())

    def test_severe_issue_is_not_small(self):
        from collab.dispatch import _stop_reason
        from collab.flow import record as rec
        for words in ("结论：不通过\n1. [P1] 未登录也能删除全部记录，建议增加权限校验。",) * 3:
            rec(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过", text=words)
        self.assertNotIn("小问题", _stop_reason(self.cdir))

    def test_code_names_never_reach_plain_words(self):
        items = plain_issues("结论：不通过\n1. 保存时 validate_id(user_id) 没查 activeChecks 和 is_running。\n"
                             "2. app.go:42 有问题。\n3. P1／高：停工仍可能漏掉正在启动的工具。")
        self.assertEqual(items, ["停工仍可能漏掉正在启动的工具"])


class ReviewRound3B(ReviewRound1B):
    def test_failed_fix_keeps_existing_tests(self):
        """第 3 轮 B1：改测试没成时，原有的（已提交的）测试恢复，不能被当成新建的删掉"""
        (self.worktree / "tests" / "test_legacy.py").write_text("# 老测试\n", encoding="utf-8")
        self._git()
        def tester(wt):
            (wt / "tests" / "test_legacy.py").write_text("def (:\n", encoding="utf-8")
        self._fix_run(tester, "结论：不通过\n1. 【测试的问题】测试写错了。", tester_ok=False)
        self.assertEqual((self.worktree / "tests" / "test_legacy.py").read_text(encoding="utf-8"), "# 老测试\n")

    def test_unmarked_issue_is_never_small(self):
        from collab.dispatch import _stop_reason
        from collab.flow import record as rec
        for _ in range(3):
            rec(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：不通过",
                text="结论：不通过\n1. 项目名带空格时会误删整个项目。")
        self.assertNotIn("小问题", _stop_reason(self.cdir))

    def test_code_name_glued_to_chinese(self):
        self.assertEqual(plain_issues("结论：不通过\n1. 保存时validateId()没有校验。\n2. 说明里activeChecks不对。\n"
                                      "3. app.py里也错。\n4. 草稿没放进去也显示已放进。"),
                         ["草稿没放进去也显示已放进"])

    def test_reviewer_marks_levels(self):
        _, calls = self.run_with(["结论：通过"])
        self.assertIn("【轻微】", calls["review"][0])

    def test_confirm_box_counts_hidden_and_record_keeps_original(self):
        from collab.phase import card_phase
        self.run_with(["结论：不通过\n1. 【中等】少一个标点（第四轮起退回后的轻微记进以后再修，这里改成中等才留在剩下的里）。\n2. 【严重】validate_id() 没查。"] * 3)
        phase = card_phase(str(self.collab.parent), "CARD-001")
        self.assertIn("另有 1 件没写成大白话", phase["issues"])
        self.assertIn("validate_id", phase["issues_raw"])

