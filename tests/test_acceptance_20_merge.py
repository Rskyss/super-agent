"""11 号单：验收通过，一键合并。合并动的是用户的真实项目，所以失败时必须原样退回。"""
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from acceptance_helpers import STATIC, needs_node, run_node, wait_until
from collab.flow import record
from collab.vcs import add_worktree
from desktop.clerk.tasks import TaskWatcher
from test_acceptance_10_fixes import CONV, Said, dead_pid
from test_acceptance_11_fixes import Case


class MergeCase(Case):
    def out(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.project, capture_output=True, text=True).stdout.strip()

    def head(self):
        return self.out("rev-parse", "HEAD")

    def commit(self, name, text):
        path = Path(self.project) / name
        path.write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "主项目的改动")

    def prepared(self, command="true", change="x = 2\n", state="待验收"):
        cdir = self.make_card(state=state, plan=True, source=CONV, reported="running", title="改按钮")
        (cdir / "开工.json").write_text(json.dumps({"test_command": command}), encoding="utf-8")
        record(cdir, "结束", src="程序", dst="你", state=state, summary="做完了")
        add_worktree(self.project, "CARD-001", self.worktree)
        if change:
            (self.worktree / "src" / "app.py").write_text(change, encoding="utf-8")
        return cdir

    def merge(self):
        from collab.merge import merge_card
        return merge_card(self.project, "CARD-001")

    def main_file(self, name="src/app.py"):
        return (Path(self.project) / name).read_text(encoding="utf-8")

    def assert_stopped(self, answer, words, before):
        self.assertFalse(answer["ok"])
        self.assertIn(words, answer["reason"])
        self.assertEqual(self.head(), before, "主项目必须原样")
        self.assertEqual(self.state(), "待验收")
        self.assertEqual(self.last_flow("合并失败")["summary"], answer["reason"])
        self.assertEqual(self.phase()["code"], "merge_failed")
        self.assertIn(words, self.phase()["detail"])


class HappyPath(MergeCase):
    def test_changes_reach_the_project(self):
        self.prepared()
        before = self.head()
        answer = self.merge()
        self.assertTrue(answer["ok"], answer)
        self.assertNotEqual(self.head(), before)
        self.assertEqual(self.main_file(), "x = 2\n")
        self.assertEqual(self.state(), "已合并")
        self.assertEqual(self.phase()["code"], "done")
        self.assertIn(self.out("rev-parse", "--abbrev-ref", "HEAD"), self.last_flow("合并")["summary"])
        self.assertIn("CARD-001：改按钮", self.out("log", "-1", "--format=%s"))
        self.assertTrue((Path(self.project) / "docs" / "改按钮").is_dir(), "要归档")
        self.assertIn("CARD-001", (Path(self.project) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8"))

    def test_copy_is_cleaned_and_branch_is_kept(self):
        self.prepared()
        self.merge()
        self.assertFalse(self.worktree.exists())
        self.assertIn("card/CARD-001", self.out("branch"))

    def test_only_allowed_files_are_committed(self):
        cdir = self.prepared()
        (self.worktree / "stray.txt").write_text("范围外", encoding="utf-8")
        (self.worktree / ".env.local").write_text("SECRET=1", encoding="utf-8")
        (self.worktree / "node_modules").symlink_to(self.project)
        (self.worktree / "tests").mkdir()
        (self.worktree / "tests" / "test_new.py").write_text("# 测试", encoding="utf-8")
        (cdir / "测试文件.json").write_text(json.dumps(["tests/test_new.py"]), encoding="utf-8")
        self.assertTrue(self.merge()["ok"])
        names = self.out("show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertEqual(sorted(names), ["src/app.py", "tests/test_new.py"])

    def test_project_without_git_identity_can_still_merge(self):
        self.prepared()
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.assertEqual(self.out("config", "user.name"), "")
        self.assertTrue(self.merge()["ok"])

    def test_project_identity_is_used_when_there_is_one(self):
        self.prepared()
        self.git("config", "user.name", "项目的人")
        self.git("config", "user.email", "a@b")
        self.merge()
        self.assertEqual(self.out("log", "-1", "--format=%an"), "项目的人")

    def test_other_local_changes_are_kept(self):
        self.prepared()
        (Path(self.project) / "note.txt").write_text("用户的笔记", encoding="utf-8")
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.main_file("note.txt"), "用户的笔记")

    def test_project_moved_on_is_rebased(self):
        self.prepared()
        self.commit("other.txt", "主项目新文件\n")
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.main_file(), "x = 2\n")
        self.assertEqual(self.main_file("other.txt"), "主项目新文件\n")

    def test_already_committed_work_can_retry(self):
        # 上次提交后中途断了，再点一次也能合并
        self.prepared()
        self.git_in_copy("add", "src/app.py")
        self.git_in_copy("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "上次的提交")
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.main_file(), "x = 2\n")

    def git_in_copy(self, *args):
        subprocess.run(["git", *args], cwd=self.worktree, check=True, capture_output=True)

    def test_archive_problem_does_not_undo_the_merge(self):
        self.prepared()
        with mock.patch("collab.merge.archive_card", side_effect=OSError("写不了")):
            self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.state(), "已合并")

    def test_copy_that_cannot_be_removed_does_not_undo_the_merge(self):
        self.prepared()
        self.git("worktree", "lock", str(self.worktree))
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.state(), "已合并")


class StopsAndKeepsTheProjectClean(MergeCase):
    def test_overlap_with_local_changes(self):
        self.prepared()
        before = self.head()
        (Path(self.project) / "src" / "app.py").write_text("用户没提交的改动\n", encoding="utf-8")
        self.assert_stopped(self.merge(), "src/app.py", before)
        self.assertEqual(self.main_file(), "用户没提交的改动\n")

    def test_conflict(self):
        self.prepared()
        self.commit("src/app.py", "主项目也改了\n")
        before = self.head()
        self.assert_stopped(self.merge(), "冲突", before)
        self.assertEqual(self.main_file(), "主项目也改了\n")
        rebase = subprocess.run(["git", "rev-parse", "--git-path", "rebase-merge"], cwd=self.worktree,
                                capture_output=True, text=True).stdout.strip()
        self.assertFalse((self.worktree / rebase).exists(), "冲突后不能留在变基中")

    def test_test_fails_after_rebase(self):
        self.prepared("test ! -f other.txt")
        self.commit("other.txt", "新文件\n")
        before = self.head()
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.state(), "待验收")

    def test_test_fails_in_the_project_rolls_back(self):
        self.prepared('test "$(basename "$PWD")" != 项目')
        before = self.head()
        (Path(self.project) / "note.txt").write_text("用户的笔记", encoding="utf-8")
        self.assert_stopped(self.merge(), "已退回", before)
        self.assertEqual(self.main_file(), "x = 1\n")
        self.assertEqual(self.main_file("note.txt"), "用户的笔记")

    def test_nothing_to_merge(self):
        self.prepared(change="")
        before = self.head()
        self.assert_stopped(self.merge(), "没有改动", before)

    def test_only_a_card_waiting_for_review_can_merge(self):
        self.prepared(state="开发中")
        before = self.head()
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.state(), "开发中")

    def test_missing_copy_is_a_plain_failure(self):
        self.make_card(state="待验收", plan=True)
        answer = self.merge()
        self.assertFalse(answer["ok"])
        self.assertTrue(answer["reason"])
        self.assertNotIn("Traceback", answer["reason"])

    def test_retry_after_clearing_the_problem(self):
        self.prepared()
        target = Path(self.project) / "src" / "app.py"
        target.write_text("用户没提交的改动\n", encoding="utf-8")
        self.assertFalse(self.merge()["ok"])
        self.git("checkout", "--", "src/app.py")
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.state(), "已合并")

    def test_command_line(self):
        self.prepared()
        code, _ = self.cli("merge", self.project, "CARD-001")
        self.assertEqual(code, 0)
        self.assertEqual(self.state(), "已合并")

    def test_command_line_failure_code(self):
        self.prepared(change="")
        code, _ = self.cli("merge", self.project, "CARD-001")
        self.assertEqual(code, 2)


class FromTheWindow(MergeCase):
    def test_review_offers_the_three_actions_and_the_branch(self):
        self.prepared()
        phase = self.phase()
        self.assertEqual(phase["code"], "review")
        self.assertEqual(phase["actions"], ["accept", "revise", "discard"])
        self.assertEqual(phase["branch"], self.out("rev-parse", "--abbrev-ref", "HEAD"))

    def test_button_merges_in_the_background(self):
        self.prepared("sleep 1")
        self.assertEqual(self.act("accept")[0], 200)
        phase = wait_until(lambda: self.phase() if self.phase()["code"] == "merging" else None, 5, "没有显示合并中")
        self.assertEqual((phase["label"], phase["needs_you"], phase["actions"]), ("合并中", False, []))
        self.assertEqual(self.act("accept")[0], 409)
        wait_until(lambda: self.state() == "已合并", 20, "没有合并完")

    def test_second_merge_waits(self):
        self.prepared()
        self.mark("merge", "CARD-999", os.getpid())
        status, answer = self.act("accept")
        self.assertEqual(status, 409)
        self.assertIn("正在合并", answer["error"])

    def test_killed_merge_is_not_restarted(self):
        self.prepared()
        before = self.head()
        record(self.collab / "cards" / "CARD-001", "合并开始", src="程序", dst="程序", summary="开始合并", pid=dead_pid())
        self.assertEqual(self.phase()["code"], "merge_failed")
        self.assertIn("断了", self.phase()["detail"])
        TaskWatcher(Said()).tick(self.project)
        self.assertEqual(self.phase()["code"], "merge_failed")
        self.assertEqual(self.head(), before)

    def test_new_round_clears_the_old_failure(self):
        cdir = self.prepared()
        record(cdir, "合并失败", src="程序", dst="你", summary="冲突")
        self.assertEqual(self.phase()["code"], "merge_failed")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        self.assertEqual(self.phase()["code"], "review")

    def test_reports(self):
        cdir = self.make_card(state="已合并", source=CONV, reported="merging", title="甲")
        said = Said()
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines, ["「甲」已合并。"])
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 0}), encoding="utf-8")
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突")
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines[-1], "「甲」合并没成功：有冲突。")

    @needs_node()
    def test_button_words(self):
        done = run_node("const P = require(" + json.dumps(str(STATIC / "panel.js")) + ");" + """
          const b = P.actionButtons({ actions: ['accept', 'revise'], branch: 'main' }, true);
          if (b[0].label !== '验收通过，合并到 main' || b[1].label !== '还要修改') throw new Error(JSON.stringify(b));
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
