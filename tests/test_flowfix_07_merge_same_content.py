"""流程修理第 10 条（试跑记录 ㉔、⑫）：卡片提交里已经和项目一模一样的文件，不算这张卡的改动。

68b 里 Claude 中途补测试：在项目里提交、再搬进施工副本；用户点“确认没问题，放进项目”时，
程序看卡片全部提交，测试文件不在允许范围，拦下了——其实那份测试项目里早就有了。
"""
import subprocess
from pathlib import Path

from test_acceptance_20_merge import MergeCase


class SameContentIsNotTheCardsChange(MergeCase):
    def wt_git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.worktree,
                       check=True, capture_output=True, text=True)

    def test_test_commit_already_in_project_does_not_block(self):
        self.prepared()
        # 卡片先有自己的提交（和 68b 一样，卡片分支已经和项目分开走了）
        (self.worktree / "src" / "app.py").write_text("x = 3\n", encoding="utf-8")
        self.wt_git("commit", "-qam", "卡片自己的改动")
        # 项目里再提交一份测试（Claude 中途补的），原样搬进卡片副本
        (Path(self.project) / "tests").mkdir(exist_ok=True)
        self.commit("tests/test_extra.py", "# Claude 补的测试\n")
        sha = self.out("rev-parse", "HEAD")
        self.wt_git("cherry-pick", sha)
        self.assertNotEqual(self.out("rev-parse", "HEAD", cwd=self.worktree), sha, "卡片上是另一笔提交")
        answer = self.merge()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.main_file(), "x = 3\n")

    def test_real_out_of_scope_commit_is_still_blocked(self):
        self.prepared()
        (self.worktree / "stray.txt").write_text("范围外", encoding="utf-8")
        self.wt_git("add", "stray.txt")
        self.wt_git("commit", "-qm", "越界")
        before = self.head()
        answer = self.merge()
        self.assert_stopped(answer, "不该进项目的文件", before)
