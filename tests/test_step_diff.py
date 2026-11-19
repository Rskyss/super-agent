"""第七批 7-2：轨迹“看改动”——每次写代码（含返工）做完，把这一步改了什么存进卡片资料，回复记录里写明位置。

做法：开始前、结束后各给施工副本拍一个快照（临时索引 + write-tree，不动真正的暂存区和提交），两次一比就是这一步的改动。"""
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _dispatch
from collab.flow import read_flow


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class StepDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cdir = root / "card"
        self.cdir.mkdir()
        self.wt = root / "worktree"
        self.wt.mkdir()
        git(self.wt, "init", "-q")
        (self.wt / "app.py").write_text("print('old')\n", encoding="utf-8")
        (self.wt / "keep.py").write_text("x = 1\n", encoding="utf-8")
        git(self.wt, "add", "-A")
        git(self.wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        # 上一步（写测试）留下、还没提交的改动：不能算到写代码头上
        (self.wt / "test_app.py").write_text("def test(): pass\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _reply(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "回复"][-1]

    def test_dev_step_saves_only_its_own_changes(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "app.py").write_text("print('new')\n", encoding="utf-8")
            (Path(cwd) / "added.py").write_text("y = 2\n", encoding="utf-8")
            return AgentResult(ok=True, output="改好了", attempts=1)

        status_before = git(self.wt, "status", "--porcelain")
        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        reply = self._reply()
        self.assertTrue(reply.get("diff"), "写代码这一步要记下改动位置")
        text = (self.cdir / reply["diff"]).read_text(encoding="utf-8")
        self.assertIn("-print('old')", text)
        self.assertIn("+print('new')", text)
        self.assertIn("added.py", text)
        self.assertNotIn("test_app.py", text, "上一步的改动不能算到这一步")
        # 不碰真正的暂存区：之前没暂存的照样没暂存
        self.assertNotIn("A  ", status_before)
        staged = git(self.wt, "diff", "--cached", "--name-only")
        self.assertEqual(staged.strip(), "")

    def test_rework_step_also_saves_diff(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "keep.py").write_text("x = 3\n", encoding="utf-8")
            return AgentResult(ok=True, output="返工好了", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "返工修改", "请返工", self.wt, {}, 2)
        self.assertIn("+x = 3", (self.cdir / self._reply()["diff"]).read_text(encoding="utf-8"))

    def test_no_change_no_diff_and_other_steps_untouched(self):
        def idle(adapter, prompt, cwd, **kwargs):
            return AgentResult(ok=True, output="没改", attempts=1)

        _dispatch(idle, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        self.assertNotIn("diff", self._reply())
        _dispatch(idle, self.cdir, "复查员", "codex", "复查", "请复查", self.wt, {}, 1)
        self.assertNotIn("diff", self._reply())

    def test_not_a_git_folder_is_quietly_skipped(self):
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()

        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "a.py").write_text("1\n", encoding="utf-8")
            return AgentResult(ok=True, output="好", attempts=1)

        result = _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", plain, {}, 1)
        self.assertTrue(result.ok)
        self.assertNotIn("diff", self._reply())


if __name__ == "__main__":
    unittest.main()


class StepDiffReviewFixTest(StepDiffTest):
    """复查第 1 轮中等 11、12、14、16、17。"""

    def test_cursor_tool_settings_and_caches_not_counted(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "__pycache__").mkdir(exist_ok=True)
            (Path(cwd) / "__pycache__" / "a.pyc").write_bytes(b"x")
            (Path(cwd) / ".pytest_cache").mkdir(exist_ok=True)
            (Path(cwd) / ".pytest_cache" / "lastfailed").write_text("{}", encoding="utf-8")
            (Path(cwd) / ".collab").mkdir(exist_ok=True)
            (Path(cwd) / ".collab" / "progress.log").write_text("x", encoding="utf-8")
            return AgentResult(ok=True, output="没改", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "cursor", "开发", "请写代码", self.wt, {}, 1,
                  test_command=["python3", "-m", "unittest"])
        self.assertNotIn("diff", self._reply(), "工具放行设置、缓存、协作区不算这一步的改动")

    def test_tracked_but_ignored_file_change_is_shown(self):
        (self.wt / ".gitignore").write_text("generated.py\n", encoding="utf-8")
        (self.wt / "generated.py").write_text("old\n", encoding="utf-8")
        git(self.wt, "add", "-f", "generated.py", ".gitignore")
        git(self.wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "gen")

        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "generated.py").write_text("new\n", encoding="utf-8")
            return AgentResult(ok=True, output="改了", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        self.assertIn("+new", (self.cdir / self._reply()["diff"]).read_text(encoding="utf-8"))

    def test_write_test_step_also_has_diff(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "test_b.py").write_text("def test_b(): pass\n", encoding="utf-8")
            return AgentResult(ok=True, output="写好了", attempts=1)

        _dispatch(ask, self.cdir, "测试员", "codex", "写测试", "请写测试", self.wt, {}, 1)
        self.assertIn("test_b.py", (self.cdir / self._reply()["diff"]).read_text(encoding="utf-8"))

    def test_big_diff_is_cut_below_what_the_page_can_read(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "big.txt").write_text("abcde\n" * 100000, encoding="utf-8")
            return AgentResult(ok=True, output="好", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        data = (self.cdir / self._reply()["diff"]).read_bytes()
        self.assertLessEqual(len(data), 500 * 1024)
        self.assertIn("改动太长".encode("utf-8"), data)

    def test_big_untracked_file_is_not_hashed_every_step(self):
        (self.wt / "blob.bin").write_bytes(__import__("os").urandom(8 * 1024 * 1024))

        def objects_kb():
            out = git(self.wt, "count-objects", "-v")
            return int(next(l.split()[1] for l in out.splitlines() if l.startswith("size:")))

        before = objects_kb()

        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "app.py").write_text("print('new')\n", encoding="utf-8")
            return AgentResult(ok=True, output="好", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        self.assertLess(objects_kb() - before, 1024, "没改的大文件不能每步都塞进 git 对象库")
        self.assertIn("+print('new')", (self.cdir / self._reply()["diff"]).read_text(encoding="utf-8"))


class StepDiffRound2Test(StepDiffTest):
    """复查第 2 轮中等 4：工具自己 git add 过的缓存、协作区、依赖也不能算改动。"""

    def test_staged_caches_are_not_counted(self):
        def ask(adapter, prompt, cwd, **kwargs):
            for rel, data in ((".collab/progress.log", "x"), ("__pycache__/x.pyc", "x"), ("node_modules/pkg/a.js", "x")):
                path = Path(cwd) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding="utf-8")
            git(cwd, "add", "-A", "-f")
            return AgentResult(ok=True, output="没改", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        self.assertNotIn("diff", self._reply())


class StepDiffRound3Test(StepDiffTest):
    """复查第 3 轮中等：缓存暂存后又被改过，不能让整步“看改动”消失。"""

    def test_cache_restaged_then_rewritten_keeps_real_diff(self):
        def ask(adapter, prompt, cwd, **kwargs):
            (Path(cwd) / "app.py").write_text("print('new')\n", encoding="utf-8")
            log = Path(cwd) / ".collab" / "progress.log"
            log.parent.mkdir(exist_ok=True)
            log.write_text("staged\n", encoding="utf-8")
            git(cwd, "add", "-A", "-f")
            log.write_text("newer\n", encoding="utf-8")   # 暂存后又写了一次，暂存内容和磁盘、HEAD 都不一样
            return AgentResult(ok=True, output="好", attempts=1)

        _dispatch(ask, self.cdir, "开发员", "agy", "开发", "请写代码", self.wt, {}, 1)
        text = (self.cdir / self._reply()["diff"]).read_text(encoding="utf-8")
        self.assertIn("+print('new')", text)
        self.assertNotIn("progress.log", text)


class StepDiffRacyTest(StepDiffTest):
    """刚提交就在同一秒里把文件改成同样长度的内容：git 的快速判断会当没变（偶发漏记，复查期间发现）。"""

    def test_same_second_same_size_edit_is_seen(self):
        from collab.dispatch import _tree_snapshot
        for i in range(30):
            git(self.wt, "add", "-A")
            git(self.wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", f"c{i}", "--allow-empty")
            before = _tree_snapshot(self.wt)
            (self.wt / "keep.py").write_text("x = 2\n" if i % 2 == 0 else "x = 3\n", encoding="utf-8")   # 和上一次不同、长度一样
            after = _tree_snapshot(self.wt)
            self.assertNotEqual(before, after, f"第 {i} 次：改了文件，快照前后却一样")

    def test_unchanged_recent_files_are_not_reread(self):
        """复查第 4 轮中等：没改的近期文件不能每次快照都重新读内容（大仓库会慢 18 倍）。
        做法：文件时间设成一小时前并提交，再把它设成不可读——要重新读就会失败，走快速判断就不读。"""
        import os
        import stat
        from collab.dispatch import _tree_snapshot
        big = self.wt / "big.bin"
        big.write_bytes(b"x" * 64)
        old = time.time() - 3600
        os.utime(big, (old, old))
        git(self.wt, "add", "-A")
        git(self.wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "big")
        # 改权限会动文件的“状态改变时间”，git 默认把它当“可能改过”，并行跑时偶发重读而失败（11-18 全量卡一起处理）。
        # 这里要测的是“内容和修改时间都没变就不重读”，所以让 git 不看状态改变时间
        git(self.wt, "config", "core.trustctime", "false")
        big.chmod(0)
        try:
            if os.access(big, os.R_OK):
                self.skipTest("当前用户无视文件权限（如 root），无法用不可读来判断")
            self.assertIsNotNone(_tree_snapshot(self.wt), "没改的文件被重新读内容了")
        finally:
            big.chmod(stat.S_IRUSR | stat.S_IWUSR)
