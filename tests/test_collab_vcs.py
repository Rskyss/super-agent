import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.vcs import NotGitRepo, add_worktree, is_git_repo, snapshot


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path: Path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-qm", "init")


class VcsTest(unittest.TestCase):
    def test_non_git_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_git_repo(tmp))
            with self.assertRaises(NotGitRepo):
                snapshot(tmp, "pre-CARD-001")

    def test_snapshot_creates_tag_at_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp))
            tag = snapshot(tmp, "pre-CARD-001-test")
            out = subprocess.run(["git", "rev-parse", tag], cwd=tmp,
                                 capture_output=True, text=True, check=True).stdout.strip()
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                                  capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(out, head)

    def test_worktree_is_isolated_and_on_card_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _make_repo(repo)
            dest = Path(tmp) / "wt"
            add_worktree(str(repo), "CARD-001", str(dest))
            self.assertTrue((dest / "a.txt").exists())
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=dest,
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(branch, "card/CARD-001")
            # 副本里的改动不影响主工作区
            (dest / "a.txt").write_text("changed\n")
            self.assertEqual((repo / "a.txt").read_text(), "hello\n")


class LinkDependenciesTest(unittest.TestCase):
    def test_worktree_gets_project_dependencies_by_link(self):
        from collab.vcs import link_dependencies
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            worktree = Path(tmp) / "wt"
            (project / "node_modules" / "pkg").mkdir(parents=True)
            worktree.mkdir()
            link_dependencies(project, worktree)
            self.assertTrue((worktree / "node_modules" / "pkg").exists())

    def test_no_dependencies_in_project_is_fine(self):
        from collab.vcs import link_dependencies
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            worktree = Path(tmp) / "wt"
            project.mkdir()
            worktree.mkdir()
            link_dependencies(project, worktree)  # 不报错
            self.assertFalse((worktree / "node_modules").exists())


class RevertPathsTest(unittest.TestCase):
    def test_reverts_modified_deleted_and_new_files_in_worktree(self):
        from collab.vcs import revert_paths
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_repo(repo)  # 里面有已提交的 a.txt
            (repo / "b.txt").write_text("keep\n")
            _git(repo, "add", "b.txt")
            _git(repo, "commit", "-qm", "b")
            (repo / "a.txt").write_text("被改了\n")      # 改了原有文件
            (repo / "b.txt").unlink()                      # 删了原有文件
            (repo / "ls").write_text("")                   # 新建的杂文件
            (repo / "junk").mkdir()
            (repo / "junk" / "x.py").write_text("x")       # 新建的目录
            revert_paths(repo, ["a.txt", "b.txt", "ls", "junk/"])
            status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(status, "")
            self.assertEqual((repo / "a.txt").read_text(), "hello\n")
            self.assertTrue((repo / "b.txt").exists())


if __name__ == "__main__":
    unittest.main()


class CopyLocalSettingsTest(unittest.TestCase):
    def test_local_env_files_are_copied_into_worktree(self):
        from collab.vcs import copy_local_settings
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            worktree = Path(tmp) / "wt"
            project.mkdir()
            worktree.mkdir()
            (project / ".env.local").write_text("KEY=1\n")
            (project / ".env.bak-old").write_text("OLD=1\n")  # 备份文件不带过去
            copy_local_settings(project, worktree)
            self.assertEqual((worktree / ".env.local").read_text(), "KEY=1\n")
            self.assertFalse((worktree / ".env.bak-old").exists())

    def test_missing_env_files_are_fine(self):
        from collab.vcs import copy_local_settings
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            worktree = Path(tmp) / "wt"
            project.mkdir()
            worktree.mkdir()
            copy_local_settings(project, worktree)  # 不报错
            self.assertFalse((worktree / ".env.local").exists())
