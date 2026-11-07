"""第二批验收和独立复查发现的问题。合并动的是用户的真实项目，这里最严。"""
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import HERE, STATIC, needs_node, run_node, tool_call, wait_until, write_script
from collab.dispatch import review_verdict
from collab.flow import record
from desktop.clerk.tasks import TaskWatcher
from test_acceptance_10_fixes import CONV, Said
from test_acceptance_11_fixes import Case
from test_acceptance_20_merge import MergeCase
from test_acceptance_22_card_chat import ChatCase


class Verdict(unittest.TestCase):
    def test_words_quoted_from_the_request_do_not_count(self):
        # 真实 Codex 的输出带着提示词
        text = "user\n只读复查。第一行写“结论：通过”或“结论：不通过”，之后列出问题。\ncodex\n结论：通过\n\n- 未发现问题"
        self.assertEqual(review_verdict(text), "pass")

    def test_last_real_verdict_wins(self):
        self.assertEqual(review_verdict("第一行写“结论：通过”\n结论：不通过\n少了测试"), "reject")

    def test_decorated_verdicts(self):
        self.assertEqual(review_verdict("**结论：不通过**"), "reject")
        self.assertEqual(review_verdict("# 结论：通过"), "pass")

    def test_only_the_request_is_not_a_verdict(self):
        self.assertEqual(review_verdict("第一行写“结论：通过”或“结论：不通过”"), "unclear")


class VerdictIsStrict(unittest.TestCase):
    def test_any_real_rejection_wins(self):
        self.assertEqual(review_verdict("结论：不通过\n修好后应该是：\n结论：通过"), "reject")
        self.assertEqual(review_verdict("结论：不通过\n少了测试\n> 结论：通过"), "reject")

    def test_code_blocks_and_quotes_do_not_count(self):
        self.assertEqual(review_verdict("结论：不通过\n```\n结论：通过\n```"), "reject")
        self.assertEqual(review_verdict("我看不出来。\n```\n结论：通过\n```"), "unclear")
        self.assertEqual(review_verdict("> 结论：通过"), "unclear")

    def test_words_that_only_start_with_pass_are_not_a_pass(self):
        self.assertEqual(review_verdict("结论：通过不了，测试是假的"), "unclear")
        self.assertEqual(review_verdict("结论：通过率只有 40%，不能合并"), "unclear")

    def test_normal_forms(self):
        for text in ("结论：通过", "结论: 通过", "结论：通过。", "**结论：通过**", "**结论：** 通过", "结论：通过，没有问题"):
            self.assertEqual(review_verdict(text), "pass", text)
        for text in ("结论：不通过", "**结论：不通过**", "**结论：** 不通过", "结论：不通过。少测试"):
            self.assertEqual(review_verdict(text), "reject", text)


class CreditIsNotGuessed(unittest.TestCase):
    def test_only_real_credit_errors(self):
        from collab.dispatch import _credit_problem
        for text in ("Your AI credits balance is too low to continue.", "RESOURCE_EXHAUSTED (code 429)",
                     "Error: usage limit reached", "You exceeded your current quota"):
            self.assertIn("额度用完了", _credit_problem("开发员", "agent", text), text)
        for text in ("FAILED tests/test_quota.py::test_over_quota - AssertionError",
                     "Error: insufficient permissions to write /tmp/x",
                     "pytest: rate limit middleware test failed",
                     "用户说要加一个额度提示\n工具崩溃了"):
            self.assertEqual(_credit_problem("开发员", "agent", text), "", text)

    def test_words_far_above_the_end_do_not_count(self):
        from collab.dispatch import _credit_problem
        self.assertEqual(_credit_problem("开发员", "agent", "RESOURCE_EXHAUSTED\n" + "别的输出\n" * 800), "")


class SyncIsSafe(MergeCase):
    def sync(self):
        from collab.merge import sync_copy
        return sync_copy(self.project, "CARD-001")

    def card_dir(self):
        return self.collab / "cards" / "CARD-001"

    def conflict_on(self, name, card_text, main_text):
        path = Path(self.project) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("原来\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加文件")
        cdir = self.prepared()
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + f"- {name}\n", encoding="utf-8")
        (self.worktree / name).write_text(card_text, encoding="utf-8")
        path.write_text(main_text, encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "主项目也改了")
        return cdir

    def test_conflict_in_a_chinese_file_name_with_spaces_is_caught(self):
        name = "说明 文档.md"
        cdir = self.conflict_on(name, "卡片版\n", "项目版\n")
        self.assertEqual(self.sync(), [name])
        before = self.head()
        self.assert_stopped(self.merge(), "冲突", before)
        self.assertEqual(json.loads((cdir / "同步冲突.json").read_text(encoding="utf-8")), [name])

    def test_second_sync_keeps_unresolved_conflicts_on_the_list(self):
        cdir = self.conflict_on("a.md", "卡片版\n", "项目版\n")
        self.assertEqual(self.sync(), ["a.md"])
        self.commit("other.txt", "又有新内容\n")
        self.assertEqual(self.sync(), ["a.md"])
        self.assertEqual(json.loads((cdir / "同步冲突.json").read_text(encoding="utf-8")), ["a.md"])
        before = self.head()
        self.assert_stopped(self.merge(), "冲突", before)
        self.assertEqual(self.main_file("a.md"), "项目版\n")

    def test_markers_are_never_merged_even_when_nobody_listed_them(self):
        cdir = self.prepared()
        (self.worktree / "src" / "app.py").write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> z\n", encoding="utf-8")
        before = self.head()
        self.assert_stopped(self.merge(), "冲突", before)
        self.assertFalse((cdir / "同步冲突.json").exists())

    def test_project_setting_cannot_wipe_the_work(self):
        self.prepared()
        self.commit("other.txt", "新内容\n")
        self.git("config", "merge.ff", "only")
        self.assertEqual(self.sync(), [])
        self.assertEqual((self.worktree / "src" / "app.py").read_text(encoding="utf-8"), "x = 2\n")
        self.git("config", "--unset", "merge.ff")

    def test_failure_puts_the_work_back(self):
        from collab.merge import _git as real_git
        self.prepared()
        self.commit("other.txt", "新内容\n")

        def broken(cwd, *args, **kwargs):
            if "--squash" in args:
                return subprocess.run(["false"], capture_output=True, text=True)
            return real_git(cwd, *args, **kwargs)

        with mock.patch("collab.merge._git", side_effect=broken):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertIn("x = 2", self.out("show", "HEAD:src/app.py", cwd=self.worktree))

    def test_conflicts_without_markers_stop_before_anything_changes(self):
        # 项目删了文件，卡片改了它。没有冲突标记，开发员也看不出来
        name = "gone.md"
        path = Path(self.project) / name
        path.write_text("原来\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加文件")
        cdir = self.prepared()
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + f"- {name}\n", encoding="utf-8")
        (self.worktree / name).write_text("卡片改了\n", encoding="utf-8")
        self.git("rm", "-q", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "项目删了")
        with self.assertRaises(RuntimeError) as caught:
            self.sync()
        self.assertIn(name, str(caught.exception))
        self.assertIn("x = 2", self.out("show", "HEAD:src/app.py", cwd=self.worktree))
        self.assertEqual((self.worktree / name).read_text(encoding="utf-8"), "卡片改了\n")

    def test_refused_sync_changes_nothing_on_the_card(self):
        name = "gone.md"
        path = Path(self.project) / name
        path.write_text("原来\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加文件")
        cdir = self.prepared()
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + f"- {name}\n", encoding="utf-8")
        (self.worktree / name).write_text("卡片改了\n", encoding="utf-8")
        self.git("rm", "-q", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "项目删了")
        status, answer = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001",
                                                       "action": "revise", "text": "改"})
        self.assertEqual(status, 409)
        self.assertIn(name, answer["error"])
        self.assertEqual(self.state(), "待验收")
        self.assertNotIn("## 追加要求", need.read_text(encoding="utf-8"))

    def test_backup_is_not_overwritten_by_an_empty_sync(self):
        self.prepared()
        self.commit("other.txt", "一\n")
        self.sync()
        tip = self.out("rev-parse", "card-backup/CARD-001")
        # 工作已经提交并合并回卡片之外的情况：副本没有改动时再同步
        self.git_in_copy("checkout", "-q", "--", ".")
        self.commit("more.txt", "二\n")
        self.sync()
        self.assertEqual(self.out("rev-parse", "card-backup/CARD-001"), tip)

    def git_in_copy(self, *args):
        subprocess.run(["git", *args], cwd=self.worktree, check=True, capture_output=True)


class UnverifiedCodeDoesNotSpread(MergeCase):
    def merging_a(self):
        """CARD-001 已经快进进项目，测试还没跑完。"""
        self.prepared()
        with mock.patch("collab.merge._run_tests", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.merge()

    def second_card(self, state):
        cdir = self.make_card("CARD-002", state=state, plan=True, title="乙")
        (cdir / "开工.json").write_text('{"test_command": "true"}', encoding="utf-8")
        return cdir

    def body(self, action, **more):
        return dict({"project": self.project, "card": "CARD-002", "action": action}, **more)

    def test_revise_waits(self):
        self.merging_a()
        self.second_card("待验收")
        status, answer = self.post("/api/clerk/card", self.body("revise", text="改"))
        self.assertEqual(status, 409)
        self.assertIn("合并", answer["error"])

    def test_approve_waits(self):
        self.merging_a()
        self.second_card("待对齐")
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "项目说明.md").write_text("# 项目说明\n正文\n", encoding="utf-8")
        status, answer = self.post("/api/clerk/card", self.body("approve"))
        self.assertEqual(status, 409)
        self.assertIn("合并", answer["error"])
        self.assertEqual(self.state("CARD-002"), "待对齐")

    def test_leftover_mark_of_a_finished_card_does_not_block(self):
        cdir = self.prepared()
        self.assertTrue(self.merge()["ok"])
        (cdir / "合并进行中.json").write_text(json.dumps({"base": "a", "branch": "b", "merged": "c"}), encoding="utf-8")
        from collab.merge import merge_unfinished
        self.assertEqual(merge_unfinished(self.project), "")


class TestFileConflict(MergeCase):
    def test_developer_can_resolve_a_conflict_in_a_test_file(self):
        name = "tests/test_shared.py"
        path = Path(self.project) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("原来\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加测试")
        write_script(self.bin / "codex", f'echo 卡片的测试 > {name}\necho 写好了\n')
        write_script(self.bin / "agent", 'echo "x = 2" > src/app.py\necho 写好了\n')
        self.start("true")
        self.assertEqual(self.finished(), "待验收")
        path.write_text("项目的测试\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "主项目也改了测试")
        self.assertFalse(self.merge()["ok"])
        # 测试员这轮什么都没动；开发员把冲突解决掉
        write_script(self.bin / "codex", 'echo 不用改\n')
        write_script(self.bin / "agent", f'echo "两边都留" > {name}\necho "x = 3" > src/app.py\necho 写好了\n')
        status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001",
                                                  "action": "revise", "text": "处理冲突"})
        self.assertEqual(status, 200)
        wait_until(lambda: self.state() in ("待验收", "需Claude介入") and "追加要求" in self.flow_kinds()
                   and self.flow_kinds()[-1] in ("结束", "转人工"), 30, "修改没有结束")
        self.assertEqual(self.state(), "待验收", self.phase())
        self.assertEqual((self.worktree / name).read_text(encoding="utf-8").strip(), "两边都留")


class LastRound(MergeCase):
    def sync(self):
        from collab.merge import sync_copy
        return sync_copy(self.project, "CARD-001")

    def revise(self):
        return self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "revise", "text": "改"})

    def test_tester_touching_a_conflict_file_does_not_freeze_it(self):
        self.prepared()
        self.commit("src/app.py", "主项目也改了\n")
        self.assertFalse(self.merge()["ok"])
        said = Path(self.tmp) / "tester.log"
        write_script(self.bin / "codex", f'printf "%s" "$*" >> "{said}"\necho 测试员解决的 > src/app.py\n'
                                         'echo 测试 > test_new.py\necho 写好了\n')
        write_script(self.bin / "agent", 'echo 开发员的新改动 > src/app.py\necho 写好了\n')
        self.assertEqual(self.revise()[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "修改没有做完")
        self.assertEqual((self.worktree / "src" / "app.py").read_text(encoding="utf-8").strip(), "开发员的新改动")
        files = json.loads((self.collab / "cards" / "CARD-001" / "测试文件.json").read_text(encoding="utf-8"))
        self.assertNotIn("src/app.py", files)
        self.assertIn("不要改", said.read_text(encoding="utf-8"))

    def test_failed_hook_then_retry_with_a_deleted_file(self):
        name = "old.txt"
        (Path(self.project) / name).write_text("要删的\n", encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加文件")
        cdir = self.prepared()
        need = cdir / "需求卡.md"
        need.write_text(need.read_text(encoding="utf-8") + f"- {name}\n", encoding="utf-8")
        (self.worktree / name).unlink()
        hooks = Path(self.out("rev-parse", "--git-path", "hooks", cwd=self.worktree))
        hooks = hooks if hooks.is_absolute() else self.worktree / hooks
        hooks.mkdir(parents=True, exist_ok=True)
        write_script(hooks / "pre-commit", "exit 1\n")
        self.assertFalse(self.merge()["ok"])
        (hooks / "pre-commit").unlink()
        answer = self.merge()
        self.assertTrue(answer["ok"], answer)
        self.assertFalse((Path(self.project) / name).exists())

    def test_half_cleared_marks_on_a_conflict_file_are_refused(self):
        self.prepared()
        self.commit("src/app.py", "主项目也改了\n")
        self.assertEqual(self.sync(), ["src/app.py"])
        before = self.head()
        for leftover in ("结果\n>>>>>>> z\n", "<<<<<<< HEAD\n结果\n", "结果\n=======\n结果\n"):
            (self.worktree / "src" / "app.py").write_text(leftover, encoding="utf-8")
            self.assertFalse(self.merge()["ok"], leftover)
            self.assertEqual(self.head(), before)

    def test_sync_from_another_branch_is_not_merged_quietly(self):
        self.prepared()
        main = self.out("rev-parse", "--abbrev-ref", "HEAD")
        self.git("checkout", "-q", "-b", "experiment")
        self.commit("experiment.txt", "实验\n")
        self.sync()
        self.git("checkout", "-q", main)
        before = self.head()
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("experiment", answer["reason"])
        self.assertEqual(self.head(), before)
        self.assertFalse((Path(self.project) / "experiment.txt").exists())

    def test_tool_committing_by_itself_cannot_sneak_files_in(self):
        self.prepared()
        (self.worktree / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
        (self.worktree / "stray.txt").write_text("范围外\n", encoding="utf-8")
        subprocess.run(["git", "add", ".env.local", "stray.txt"], cwd=self.worktree, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "工具自己提交"],
                       cwd=self.worktree, check=True)
        before = self.head()
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("stray.txt", answer["reason"])
        self.assertEqual(self.head(), before)


class ReviewerFocus(Case):
    def test_reviewer_is_told_records_are_not_its_job(self):
        # 真实 Codex 曾因台账还没写施工记录而打回。那是程序在结束后才写的
        said = Path(self.tmp) / "said.log"
        write_script(self.bin / "agy", f'printf "%s" "$*" >> "{said}"\necho 结论：通过\n')
        self.start()
        self.assertEqual(self.finished(), "待验收")
        self.assertIn("流程记录由程序在施工结束后补写", said.read_text(encoding="utf-8"))


class ToolProblems(Case):
    AGY = ('error: Your AI credits balance is too low to continue.\n'
           'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429)"}\n'
           '{"status":"ERROR","response":"","error":"Your AI credits balance is too low to continue."}\nexit 1\n')

    def test_reviewer_out_of_credit_says_so(self):
        write_script(self.bin / "agy", "cat <<'END'\n" + self.AGY.replace("\nexit 1\n", "\nEND\nexit 1\n"))
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        detail = self.phase()["detail"]
        self.assertIn("额度用完了", detail)
        self.assertIn("agy", detail)
        self.assertIn("换", detail)

    def test_developer_out_of_credit_says_so(self):
        write_script(self.bin / "agent", 'echo "Error: usage limit reached, quota exceeded"\nexit 1\n')
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        self.assertIn("额度用完了", self.phase()["detail"])

    def test_other_failures_keep_their_reason(self):
        write_script(self.bin / "agent", 'echo "网络断了"\nexit 1\n')
        self.start()
        self.assertEqual(self.finished(), "需Claude介入")
        self.assertIn("网络断了", self.phase()["detail"])


class ProjectMustBeReady(MergeCase):
    def test_detached_head_stops(self):
        self.prepared()
        self.git("checkout", "-q", "--detach")
        before = self.head()
        self.assert_stopped(self.merge(), "分支", before)

    def test_unfinished_rebase_stops(self):
        self.prepared()
        self.commit("other.txt", "一\n")
        self.git("checkout", "-q", "-b", "topic", "HEAD~1")
        self.commit("other.txt", "二\n")
        subprocess.run(["git", "rebase", "main"], cwd=self.project, capture_output=True)
        self.commit_nothing_check()

    def commit_nothing_check(self):
        rebase = self.out("rev-parse", "--git-path", "rebase-merge")
        if not (Path(self.project) / rebase).exists():
            self.skipTest("这台电脑的默认分支不是 main，没有进入变基")
        before = self.head()
        self.assert_stopped(self.merge(), "没做完", before)
        subprocess.run(["git", "rebase", "--abort"], cwd=self.project, capture_output=True)

    def test_button_without_a_branch_still_reads_well(self):
        self.prepared()
        self.git("checkout", "-q", "--detach")
        self.assertEqual(self.phase()["branch"], "")


class RollbackIsCareful(MergeCase):
    def failing_test_that(self, action):
        """测试最后一步在主项目里做 action，然后失败。副本里直接通过。"""
        return f'[ "$(basename "$PWD")" != 项目 ] && exit 0; {action}; exit 1'

    def test_commit_made_during_the_test_is_not_lost(self):
        self.prepared(self.failing_test_that(
            'echo 用户的 > mine.txt; git add mine.txt; git -c user.name=t -c user.email=t@t commit -qm 用户的提交'))
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("没有自动退回", answer["reason"])
        self.assertEqual(self.main_file("mine.txt"), "用户的\n")
        self.assertIn("用户的提交", self.out("log", "--format=%s"))
        self.assertEqual(self.state(), "待验收")

    def test_branch_switched_during_the_test_is_not_moved(self):
        self.prepared(self.failing_test_that('git checkout -q -b 另一个分支'))
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("没有自动退回", answer["reason"])
        self.assertEqual(self.out("rev-parse", "--abbrev-ref", "HEAD"), "另一个分支")
        self.assertIn("CARD-001", self.out("log", "-1", "--format=%s", "另一个分支"))

    def test_rollback_that_cannot_finish_says_so(self):
        self.prepared(self.failing_test_that('echo 测试改了 > src/app.py'))
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("没能退回", answer["reason"])
        self.assertNotIn("已退回", answer["reason"])


class Interrupted(MergeCase):
    def killed_after_merging(self, command="true"):
        """合并已经进了主项目，测试还没跑完进程就没了。"""
        cdir = self.prepared(command)
        base = self.head()
        with mock.patch("collab.merge._run_tests", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.merge()
        self.assertNotEqual(self.head(), base)
        return cdir, base

    def test_stage_says_what_happened_and_offers_the_way_out(self):
        self.killed_after_merging()
        phase = self.phase()
        self.assertEqual(phase["code"], "merge_failed")
        self.assertEqual(phase["actions"], ["accept", "discard"])
        self.assertIn("没验证", phase["detail"])

    def test_retry_finishes_the_test(self):
        self.killed_after_merging()
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.state(), "已合并")

    def test_retry_with_failing_test_rolls_back(self):
        cdir, base = self.killed_after_merging("true")
        (cdir / "开工.json").write_text(json.dumps({"test_command": "false"}), encoding="utf-8")
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("已退回", answer["reason"])
        self.assertEqual(self.head(), base)
        self.assertEqual(self.main_file(), "x = 1\n")

    def test_discard_takes_the_unverified_code_back(self):
        _, base = self.killed_after_merging()
        self.assertEqual(self.act("discard")[0], 200)
        self.assertEqual(self.head(), base)
        self.assertEqual(self.state(), "已放弃")

    def test_discard_does_not_touch_new_work(self):
        _, base = self.killed_after_merging()
        self.commit("mine.txt", "用户的\n")
        now = self.head()
        self.assertEqual(self.act("discard")[0], 200)
        self.assertEqual(self.head(), now)
        self.assertNotEqual(self.head(), base)
        self.assertEqual(self.state(), "已放弃")

    def test_retry_after_new_work_does_not_touch_it(self):
        self.killed_after_merging()
        self.commit("mine.txt", "用户的\n")
        now = self.head()
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertEqual(self.head(), now)
        self.assertEqual(self.main_file("mine.txt"), "用户的\n")


class HonestReasons(MergeCase):
    def test_rebase_works_without_git_identity(self):
        import os
        self.prepared()
        self.commit("other.txt", "主项目新文件\n")
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.assertTrue(self.merge()["ok"], self.last_flow("合并失败"))

    def test_leftover_change_in_the_copy_does_not_pretend_to_be_a_conflict(self):
        self.prepared()
        (self.worktree / ".gitignore").write_text("被测试改了\n", encoding="utf-8")
        self.commit("other.txt", "主项目新文件\n")
        self.assertTrue(self.merge()["ok"], self.last_flow("合并失败"))
        self.assertNotIn("被测试改了", self.main_file(".gitignore"))

    def test_conflict_reason_names_the_file_and_the_way_out(self):
        self.prepared()
        self.commit("src/app.py", "主项目也改了\n")
        reason = self.merge()["reason"]
        self.assertIn("src/app.py", reason)
        self.assertIn("还要修改", reason)

    def test_commit_hook_failure_says_so(self):
        self.prepared()
        hooks = Path(self.out("rev-parse", "--git-path", "hooks", cwd=self.worktree))
        hooks = hooks if hooks.is_absolute() else self.worktree / hooks
        hooks.mkdir(parents=True, exist_ok=True)
        write_script(hooks / "pre-commit", "echo 钩子不让提交\nexit 1\n")
        before = self.head()
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertIn("提交", answer["reason"])
        self.assertEqual(self.head(), before)
        self.assertIn("钩子不让提交", (self.collab / "cards" / "CARD-001" / "流转.jsonl").read_text(encoding="utf-8")
                      + self.detail_text())

    def detail_text(self):
        from collab.flow import text_of
        return text_of(self.collab / "cards" / "CARD-001", self.last_flow("合并失败"))

    def test_top_line_shows_merging(self):
        from desktop.view import card_view
        self.prepared()
        self.mark("merge", "CARD-001", __import__("os").getpid())
        self.assertEqual(card_view(self.project, "CARD-001")["now"]["text"], "合并中")


class ConflictIsNotADeadEnd(MergeCase):
    def conflicted(self):
        self.prepared()
        self.commit("src/app.py", "主项目也改了\n")
        self.assertFalse(self.merge()["ok"])

    def dev_script(self, body):
        self.dev_log = Path(self.tmp) / "dev.log"
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{self.dev_log}"\n{body}\necho 写好了\n')
        write_script(self.bin / "codex", 'mkdir -p tests\n[ -f tests/test_new.py ] || echo 测试 > tests/test_new.py\necho 写好了\n')

    def test_developer_gets_the_new_content_and_can_merge(self):
        self.conflicted()
        self.dev_script('echo "解决后的内容" > src/app.py')
        self.assertEqual(self.revise_card()[0], 200)
        wait_until(lambda: self.state() == "待验收", 25, "修改没有做完")
        said = self.dev_log.read_text(encoding="utf-8")
        self.assertIn("src/app.py", said)
        self.assertIn("冲突", said)
        self.assertTrue(self.merge()["ok"], self.last_flow("合并失败"))
        self.assertEqual(self.main_file(), "解决后的内容\n")
        self.assertEqual(self.out("log", "--format=%s").count("主项目的改动"), 1)
        self.assertNotIn("<<<<<<<", self.out("log", "-p", "-3", "--format="))

    def revise_card(self):
        return self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "revise",
                                             "text": "处理冲突"})

    def test_markers_left_behind_are_a_failure(self):
        self.conflicted()
        self.dev_script(': # 开发员什么都没改')
        self.revise_card()
        wait_until(lambda: self.state() == "需Claude介入", 30, "应该转人工")
        self.assertIn("<<<<<<<", (self.worktree / "src" / "app.py").read_text(encoding="utf-8"))
        self.assertEqual(self.main_file(), "主项目也改了\n")

    def test_merge_refuses_markers(self):
        self.conflicted()
        cdir = self.collab / "cards" / "CARD-001"
        self.dev_script(': # 没改')
        from collab.merge import sync_copy
        sync_copy(self.project, "CARD-001")
        self.assertTrue((cdir / "同步冲突.json").exists())
        before = self.head()
        self.assert_stopped(self.merge(), "冲突", before)

    def test_sync_without_conflict_keeps_work_and_new_content(self):
        self.prepared()
        self.commit("other.txt", "主项目新文件\n")
        from collab.merge import sync_copy
        self.assertEqual(sync_copy(self.project, "CARD-001"), [])
        self.assertEqual((self.worktree / "src" / "app.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertEqual((self.worktree / "other.txt").read_text(encoding="utf-8"), "主项目新文件\n")
        self.assertEqual(self.out("rev-parse", "HEAD", cwd=self.worktree), self.head())

    def test_up_to_date_copy_is_untouched(self):
        self.prepared()
        from collab.merge import sync_copy
        tip = self.out("rev-parse", "HEAD", cwd=self.worktree)
        self.assertEqual(sync_copy(self.project, "CARD-001"), [])
        self.assertEqual(self.out("rev-parse", "HEAD", cwd=self.worktree), tip)
        self.assertEqual((self.worktree / "src" / "app.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_work_can_be_found_again_after_sync(self):
        self.conflicted()
        from collab.merge import sync_copy
        sync_copy(self.project, "CARD-001")
        self.assertIn("card-backup/CARD-001", self.out("branch"))


class ReviseOrder(Case):
    def test_old_card_without_a_test_command_is_not_half_changed(self):
        cdir = self.make_card(state="需Claude介入", plan=True)
        (cdir / "开工.json").unlink()
        status, answer = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001",
                                                       "action": "revise", "text": "改"})
        self.assertEqual(status, 409)
        self.assertIn("测试命令", answer["error"])
        self.assertEqual(self.state(), "需Claude介入")
        self.assertNotIn("## 追加要求", (cdir / "需求卡.md").read_text(encoding="utf-8"))

    def test_failed_start_has_a_way_out(self):
        cdir = self.make_card(state="待验收", plan=True)
        with mock.patch("desktop.clerk.tasks.launch", side_effect=OSError("起不来")):
            status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001",
                                                      "action": "revise", "text": "改"})
        self.assertEqual(status, 409)
        self.assertEqual(self.state(), "开发中")
        self.assertIn("resume", self.phase()["actions"])
        self.assertTrue(cdir.exists())

    def test_summary_after_revise_does_not_say_interrupted(self):
        self.start()
        wait_until(lambda: self.state() == "待验收", 25)
        self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "revise", "text": "改"})
        wait_until(lambda: self.flow_kinds().count("开跑") == 2, 10)
        runs = [e for e in self.collab_flow() if e.get("kind") == "开跑"]
        self.assertNotIn("中断", runs[-1]["summary"])

    def collab_flow(self):
        from collab.flow import read_flow
        return read_flow(self.collab / "cards" / "CARD-001")


class PendingWords(ChatCase):
    def building_card_chat(self):
        cdir = self.building()
        cid = self.open()
        return cdir, cid

    def to_state(self, cdir, state):
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": state, "rework": 0}), encoding="utf-8")

    def test_finished_card_does_not_promise(self):
        cdir = self.card(state="已合并")
        self.propose_revise()
        cid = self.open()
        self.say(cid, "再改改")
        answer = [m for m in self.client.calls[1][0] if m["role"] == "tool"][0]["content"]
        self.assertIn("已经结束", answer)
        self.assertEqual(self.pending(), [])
        self.assertTrue(cdir.exists())

    def test_words_left_when_the_card_ends_are_told(self):
        cdir, cid = self.building_card_chat()
        self.propose_revise("没来得及转交的话")
        self.say(cid, "改")
        self.to_state(cdir, "已合并")
        TaskWatcher(self.desk).tick(self.project)
        self.assertEqual(self.pending(), [])
        self.assertTrue(any("没来得及转交的话" in (w or "") and "主对话" in w for w in self.words(cid)))

    def test_new_words_survive_confirming_the_old_box(self):
        cdir, cid = self.building_card_chat()
        self.propose_revise("第一条")
        self.say(cid, "改")
        self.to_state(cdir, "待验收")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        TaskWatcher(self.desk).tick(self.project)
        box = self.boxes(self.events(cid), "revise")[0]
        self.to_state(cdir, "开发中")
        record(cdir, "开跑", src="调度员", dst="程序", summary="接着跑", pid=__import__("os").getpid())
        self.propose_revise("第二条")
        self.say(cid, "再改")
        self.to_state(cdir, "待验收")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        self.assertEqual(self.decide(cid, box["id"], "confirm")[0], 200)
        self.assertEqual(self.pending(), ["第二条"])

    def test_saying_it_twice_keeps_one_open_box(self):
        self.card(state="待验收")
        cid = self.open()
        self.propose_revise("第一次")
        self.say(cid, "改")
        self.propose_revise("第二次")
        self.say(cid, "不对，这样改")
        states = {}
        for event in self.events(cid):
            if event["type"] == "proposal":
                states[event["id"]] = event["state"]
            elif event["type"] == "proposal_done":
                states[event["id"]] = event["state"]
        self.assertEqual(sorted(states.values()), ["open", "replaced"])


class ReportWords(Case):
    def test_no_double_full_stop(self):
        cdir = self.make_card(state="待验收", source=CONV, reported="review", title="甲")
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突。点按钮处理。")
        said = Said()
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines, ["「甲」合并没成功：有冲突。点按钮处理。"])


class BoxEdges(ChatCase):
    def test_replacing_a_box_keeps_the_noted_words(self):
        cdir = self.building()
        cid = self.open()
        self.propose_revise("第一条")
        self.say(cid, "改")
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 0}), encoding="utf-8")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        TaskWatcher(self.desk).tick(self.project)
        self.propose_revise("第二条")
        self.say(cid, "再改")
        boxes = self.boxes(self.events(cid), "revise")
        self.assertIn("第一条", boxes[-1]["text"])
        self.assertIn("第二条", boxes[-1]["text"])
        self.assertEqual(self.decide(cid, boxes[-1]["id"], "cancel")[0], 200)
        self.assertEqual(self.pending(), [])
        TaskWatcher(self.desk).tick(self.project)
        self.assertEqual(len(self.boxes(self.events(cid), "revise")), 2)

    def test_box_is_closed_when_the_start_fails(self):
        self.card(state="待验收")
        cid = self.open()
        self.propose_revise()
        _, events = self.say(cid, "改")
        box = self.boxes(events, "revise")[0]
        with mock.patch("desktop.clerk.tasks.launch", side_effect=OSError("起不来")):
            status, _ = self.decide(cid, box["id"], "confirm")
        self.assertEqual(status, 200)
        self.assertEqual(self.state(), "开发中")
        self.assertIn("resume", self.phase()["actions"])
        self.assertTrue(any("接着跑" in (w or "") for w in self.words(cid)))


class ReplyEdges(ChatCase):
    def test_no_false_alarm_after_a_box(self):
        self.client.steps = [{"text": "我整理好了。", "tool_calls": [tool_call("propose_task", {
            "title": "新", "do": "做", "not_do": "不", "done_when": "完"})]}, {"text": ""}]
        cid, _ = self.say("", "做个事")
        self.assertNotIn("这次没有得到回复，请再说一次。", self.words(cid))

    def test_answer_carries_the_card_number(self):
        self.card()
        _, answer = self.post("/api/clerk/card/open", {"project": self.project, "card": "CARD-001"})
        self.assertEqual(answer["id"], "CARD-001")


class Pages(Case):
    @needs_node()
    def test_switching_between_card_and_main_chat(self):
        done = subprocess.run(["node", str(HERE / "chat_card_check.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)

    def test_confirmed_revise_box_does_not_talk_about_a_new_card(self):
        script = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("已确认，开始修改。", script)

    @needs_node()
    def test_button_without_a_branch(self):
        done = run_node("const P = require(" + json.dumps(str(STATIC / "panel.js")) + ");" + """
          const b = P.actionButtons({ actions: ['accept'], branch: '' }, true);
          if (b[0].label !== '验收通过，合并') throw new Error(b[0].label);
        """)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_revise_box_is_reset_when_changing_cards(self):
        script = (STATIC / "panel.js").read_text(encoding="utf-8")
        start = script.index("window.SuperAgentPanel = {")
        self.assertIn("reviseBox", script[start:start + 600])
