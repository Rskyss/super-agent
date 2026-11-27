"""跑卡卡点修理 卡 C′ 验收（11-27 缩小版，docs/跑卡卡点修理/方案.md “第三次修订”、任务清单 C′ 行）。

（11-27 用户定：“中断了加继续改”撤回、单列，这张卡只做方括号路径。）
① 补要求填 app/x/[id]/page.tsx 能写进允许范围；* ? 和会被当成备注的写法不许
② 交给 git 的路径按字面认：暂存、最终提交、撤回、写测试的越界撤回、判断是否被管理都不碰旁边 app/x/i、app/x/d 的文件
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab import tasks as collab_tasks
from collab.state import Card
from collab.workspace import init_collab
from desktop.clerk import tasks as clerk_tasks


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class BracketRevise(unittest.TestCase):
    """正常的“继续改”（等验收的卡）带方括号路径：写进允许范围。"""

    def test_revise_with_bracket_path(self):
        from collab.scope import read_allowed
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            project.mkdir()
            cdir = init_collab(str(project)) / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("改页面\n\n## 允许改动的范围\n- a.py\n", encoding="utf-8")
            (cdir / "开工.json").write_text(json.dumps({"test_command": "true"}), encoding="utf-8")
            Card("CARD-001", state="待验收").save(cdir / "状态.json")
            with mock.patch.object(clerk_tasks, "running", return_value=None), \
                    mock.patch.object(clerk_tasks, "merge_unfinished", return_value=False), \
                    mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "review", "actions": ["accept", "revise"]}), \
                    mock.patch.object(clerk_tasks, "sync_copy", return_value=[]), \
                    mock.patch.object(clerk_tasks, "launch", return_value=1):
                clerk_tasks.apply_revise(str(project), "CARD-001", "详情页也改", files=["app/x/[id]/page.tsx"])
            self.assertIn("app/x/[id]/page.tsx", read_allowed((cdir / "需求卡.md").read_text(encoding="utf-8")))


class BracketScope(unittest.TestCase):
    def test_clean_scope(self):
        self.assertEqual(collab_tasks._clean_scope_files(["app/x/[id]/page.tsx"]), ["app/x/[id]/page.tsx"])
        for bad in ("app/*.tsx", "app/?.tsx", "/abs.py", "../x.py", ".collab/x", "app/[id]/logo.svg (old)",
                    "app/[id]/logo.svg(old)", "app/[id]/logo.svg（旧版）", "app/[id]/logo.svg(old)/.",
                    "./-draft.md", "./#draft.md", "./ [id]/page.tsx", "app/[id]/logo /."):
            with self.subTest(bad), self.assertRaises(collab_tasks.TaskError):
                collab_tasks._clean_scope_files([bad])


class LiteralGitPaths(unittest.TestCase):
    """三个兄弟目录 [id]、i、d 各有一个 page.tsx：只动 [id] 那个，另两个的暂存区和工作区内容都不能变。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name)
        _git(self.wt, "init", "-q")
        for d in ("[id]", "i", "d"):
            (self.wt / "app" / "x" / d).mkdir(parents=True)
            (self.wt / "app" / "x" / d / "page.tsx").write_text("BASE\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "base")
        for d in ("i", "d"):
            (self.wt / "app" / "x" / d / "page.tsx").write_text("STAGED\n", encoding="utf-8")
            _git(self.wt, "add", f"app/x/{d}/page.tsx")
            (self.wt / "app" / "x" / d / "page.tsx").write_text("WORK\n", encoding="utf-8")
        (self.wt / "app" / "x" / "[id]" / "page.tsx").write_text("LOCAL\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def neighbours_untouched(self):
        for d in ("i", "d"):
            rel = f"app/x/{d}/page.tsx"
            self.assertEqual((self.wt / rel).read_text(encoding="utf-8"), "WORK\n", f"{rel} 工作区")
            self.assertEqual(_git(self.wt, "show", f":{rel}").stdout, "STAGED\n", f"{rel} 暂存区")

    def test_stage_only_bracket_file(self):
        from collab.merge import _stage
        _stage(self.wt, ["app/x/[id]/page.tsx"])
        self.assertEqual(_git(self.wt, "show", ":app/x/[id]/page.tsx").stdout, "LOCAL\n")
        self.neighbours_untouched()

    def test_revert_only_bracket_file(self):
        from collab.vcs import revert_paths
        revert_paths(self.wt, ["app/x/[id]/page.tsx"])
        self.assertEqual((self.wt / "app/x/[id]/page.tsx").read_text(encoding="utf-8"), "BASE\n")
        self.neighbours_untouched()

    def test_tracked_check_is_literal(self):
        from collab.dispatch import _tracked
        (self.wt / "app" / "x" / "[id]" / "page.tsx").unlink()
        _git(self.wt, "rm", "-q", "--cached", ":(literal)app/x/[id]/page.tsx")
        self.assertFalse(_tracked(self.wt, "app/x/[id]/page.tsx"), "方括号不能匹配到 i、d 两个文件")

    def test_final_commit_only_bracket_file(self):
        """最终提交只收 [id] 那个文件，项目自己的钩子也看得到这个文件名。"""
        hook = self.wt / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ngit diff --cached --name-only > \"$(git rev-parse --git-dir)/hook-saw.txt\"\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        from collab.merge import _literal, _stage
        _stage(self.wt, ["app/x/[id]/page.tsx"])
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c", "--only", "--",
                        *_literal(["app/x/[id]/page.tsx"])], cwd=self.wt, check=True, capture_output=True)
        changed = _git(self.wt, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(changed, ["app/x/[id]/page.tsx"])
        self.assertIn("app/x/[id]/page.tsx", (self.wt / ".git" / "hook-saw.txt").read_text(encoding="utf-8"))
        self.neighbours_untouched()

    def test_hook_rejection_respected(self):
        """项目钩子按 *.tsx 拒绝时，提交真的被挡住（HEAD 不变）。"""
        hook = self.wt / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ngit diff --cached --name-only | grep -q '\\.tsx$' && exit 1\nexit 0\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        from collab.merge import _literal, _stage
        head = _git(self.wt, "rev-parse", "HEAD").stdout
        _stage(self.wt, ["app/x/[id]/page.tsx"])
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c", "--only", "--",
                               *_literal(["app/x/[id]/page.tsx"])], cwd=self.wt, capture_output=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(_git(self.wt, "rev-parse", "HEAD").stdout, head)

    def test_git_literal_env_plain_paths(self):
        """git 整个开了字面模式（GIT_LITERAL_PATHSPECS=1）：普通文件照样能暂存、撤回、暂存删除。"""
        from collab.merge import _stage
        from collab.vcs import revert_paths
        (self.wt / "a.py").write_text("A\n", encoding="utf-8")
        _git(self.wt, "add", "a.py")
        _git(self.wt, "commit", "-qm", "a")
        with mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": "1"}):
            (self.wt / "a.py").write_text("B\n", encoding="utf-8")
            _stage(self.wt, ["a.py"])
            self.assertEqual(_git(self.wt, "show", ":a.py").stdout, "B\n")
            (self.wt / "a.py").write_text("C\n", encoding="utf-8")
            revert_paths(self.wt, ["a.py"])
            self.assertEqual((self.wt / "a.py").read_text(encoding="utf-8"), "A\n")
            (self.wt / "a.py").unlink()
            _stage(self.wt, ["a.py"])
            self.assertIn("a.py", _git(self.wt, "diff", "--cached", "--name-only", "--diff-filter=D").stdout)


class LiteralMore(LiteralGitPaths):
    def test_env_false_still_literal(self):
        from collab.merge import _stage
        from collab.vcs import revert_paths
        for value in ("false", "no", "off", "0"):
            with self.subTest(value), mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": value}):
                _stage(self.wt, ["app/x/[id]/page.tsx"])
                revert_paths(self.wt, ["app/x/[id]/page.tsx"])
                self.neighbours_untouched()

    def test_tester_undo_spares_neighbour_index(self):
        from collab.dispatch import _undo_tester_changes
        cdir = self.wt / ".collab-card"
        (cdir / "测试前备份" / "app" / "x" / "[id]").mkdir(parents=True)
        _undo_tester_changes(cdir, self.wt, ["app/x/[id]/page.tsx"], {"app/x/[id]/page.tsx": None}, lambda w, p: None)
        self.neighbours_untouched()

    def commit_work(self):
        from collab import merge
        cdir = self.wt / ".collab" / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("改页面\n\n## 允许改动的范围\n- app/x/[id]/page.tsx\n", encoding="utf-8")
        (self.wt / ".gitignore").write_text(".collab/\n", encoding="utf-8")
        base = _git(self.wt, "rev-parse", "HEAD").stdout.strip()
        with mock.patch.object(merge, "archive_card", side_effect=RuntimeError("不归档")):
            return merge._commit_work(self.wt, self.wt, cdir, "CARD-001", base), base

    def test_real_commit_only_bracket_file(self):
        """正式提交（merge._commit_work）只收 [id] 那个文件，邻居的暂存、工作区内容都不动。"""
        (self.wt / ".gitignore").write_text(".collab/\n", encoding="utf-8")
        _git(self.wt, "add", ".gitignore")
        _git(self.wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore", "--only", ".gitignore")
        for d in ("i", "d"):   # 邻居先回到“暂存 STAGED、工作区 WORK”
            pass
        self.commit_work()
        changed = _git(self.wt, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(changed, ["app/x/[id]/page.tsx"])
        self.neighbours_untouched()

    def test_real_commit_hook_rejection(self):
        from collab.merge import _Stop
        (self.wt / ".gitignore").write_text(".collab/\n", encoding="utf-8")
        _git(self.wt, "add", ".gitignore")
        _git(self.wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore", "--only", ".gitignore")
        hook = self.wt / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ngit diff --cached --name-only | grep -q '\\.tsx$' && exit 1\nexit 0\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        head = _git(self.wt, "rev-parse", "HEAD").stdout
        with self.assertRaises(_Stop):
            self.commit_work()
        self.assertEqual(_git(self.wt, "rev-parse", "HEAD").stdout, head)


class LiteralNumeric(LiteralGitPaths):
    def test_numeric_true_values_plain_files(self):
        from collab.merge import _literal
        for value in ("2", "01", "-1", "true", "on", "0x1", "1k"):
            with self.subTest(value), mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": value}):
                self.assertEqual(_literal(["a.py"]), ["a.py"])
        for value in ("0", "false", "no", "off", ""):
            with self.subTest(value), mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": value}):
                self.assertEqual(_literal(["a.py"]), [":(literal)a.py"])

    def test_stage_deletion_spares_neighbours(self):
        from collab.merge import _stage
        (self.wt / "app/x/[id]/page.tsx").unlink()
        _stage(self.wt, ["app/x/[id]/page.tsx"])
        self.assertIn("app/x/[id]/page.tsx", _git(self.wt, "diff", "--cached", "--name-only", "--diff-filter=D").stdout)
        self.neighbours_untouched()

    def test_tester_undo_backup_branch_spares_neighbours(self):
        from collab.dispatch import _TEST_BACKUP, _undo_tester_changes
        cdir = self.wt / ".collab-card"
        saved = cdir / _TEST_BACKUP / "app" / "x" / "[id]" / "page.tsx"
        saved.parent.mkdir(parents=True)
        saved.write_text("SAVED\n", encoding="utf-8")
        _undo_tester_changes(cdir, self.wt, ["app/x/[id]/page.tsx"], {"app/x/[id]/page.tsx": "x"}, lambda w, p: None)
        self.assertEqual((self.wt / "app/x/[id]/page.tsx").read_text(encoding="utf-8"), "SAVED\n")
        self.neighbours_untouched()

    def test_hook_with_pathspec_sees_bracket_file(self):
        """项目钩子用 git diff --cached -- '*.tsx' 挑文件：正式提交时也看得到方括号文件，并能拒绝。"""
        from collab.merge import _Stop
        hook = self.wt / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\n[ -n \"$(git diff --cached --name-only -- '*.tsx')\" ] && exit 1\nexit 0\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        (self.wt / ".gitignore").write_text(".collab/\n", encoding="utf-8")
        _git(self.wt, "add", ".gitignore")
        _git(self.wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore", "--only", ".gitignore",
             "--no-verify")
        head = _git(self.wt, "rev-parse", "HEAD").stdout
        with self.assertRaises(_Stop):
            LiteralMore.commit_work(self)
        self.assertEqual(_git(self.wt, "rev-parse", "HEAD").stdout, head)


class SaveBeforeStart(LiteralGitPaths):
    """“开工前先存”真接口：只提交选中的方括号施工单，旁边的草稿不动。"""

    def test_save_pending_only_selected(self):
        from desktop import server
        (self.wt / "docs" / "施工单").mkdir(parents=True)
        for name in ("[id].md", "i.md", "d.md"):
            (self.wt / "docs" / "施工单" / name).write_text("草稿\n", encoding="utf-8")
        (self.wt / ".collab" / "cards" / "CARD-001").mkdir(parents=True)
        handler = object.__new__(server.Handler)
        handler.server = mock.MagicMock(desk=None)
        sent = []
        handler._json = lambda data, status=200: sent.append((status, data))
        with mock.patch.object(server, "save_paths", return_value=(["docs/施工单/[id].md"], [])), \
                mock.patch.object(server, "running", return_value=None), \
                mock.patch.object(server, "merge_unfinished", return_value=False), \
                mock.patch.object(server, "card_title", return_value="t"):
            handler._clerk_post("/api/clerk/save_pending", {"project": str(self.wt), "card": "CARD-001"})
        self.assertEqual(sent[-1][1].get("ok"), True, sent)
        changed = _git(self.wt, "-c", "core.quotepath=off", "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(changed, ["docs/施工单/[id].md"])
        untracked = _git(self.wt, "-c", "core.quotepath=off", "status", "--porcelain").stdout
        self.assertIn("docs/施工单/i.md", untracked.replace('"', ""))


class Round5(LiteralGitPaths):
    def test_product_folder_check_literal(self):
        """仓库顶层有文件 i：新目录 [id] 不能被认成已有的产品目录。"""
        from collab.dispatch import _in_product_folder
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            _git(wt, "init", "-q")
            (wt / "i").write_text("x\n", encoding="utf-8")
            _git(wt, "add", "i")
            _git(wt, "commit", "-qm", "i")
            (wt / "[id]").mkdir()
            (wt / "[id]" / "new.ts").write_text("x\n", encoding="utf-8")
            self.assertFalse(_in_product_folder(wt, "[id]/new.ts"))

    def test_tester_undo_resets_index_for_bracket(self):
        """写测试的越界撤回（底稿里删掉的分支）：目标的暂存也拿掉，邻居的暂存、工作区都不动。"""
        from collab.dispatch import _undo_tester_changes
        _git(self.wt, "add", ":(literal)app/x/[id]/page.tsx")
        _undo_tester_changes(self.wt / ".card", self.wt, ["app/x/[id]/page.tsx"], {"app/x/[id]/page.tsx": None},
                             lambda w, p: None)
        self.assertEqual(_git(self.wt, "show", ":app/x/[id]/page.tsx").stdout, "BASE\n", "目标的暂存回到提交时的样子")
        self.neighbours_untouched()

    def test_revert_new_file_unstage_literal(self):
        """撤回只在副本里新建、已暂存的方括号文件：邻居新文件的暂存不动。"""
        from collab.vcs import revert_paths
        for d in ("[id]", "i", "d"):
            (self.wt / "app" / "x" / d / "new.ts").write_text("NEW\n", encoding="utf-8")
            _git(self.wt, "add", f":(literal)app/x/{d}/new.ts")
        revert_paths(self.wt, ["app/x/[id]/new.ts"])
        self.assertFalse((self.wt / "app/x/[id]/new.ts").exists())
        for d in ("i", "d"):
            self.assertEqual(_git(self.wt, "show", f":app/x/{d}/new.ts").stdout, "NEW\n")

    def test_planner_bracket_rules(self):
        from collab.planner import _bad_scope_reason as bad
        for ok in ("[id]/page.tsx", "[...slug]/page.tsx", "[[...slug]]/page.tsx", "src/*.py", "tests/[id].test.ts"):
            self.assertIsNone(bad(ok), ok)
        for no in ("[id]/*.tsx", "[id]/logo.svg(草稿)", "[.]env", "[.]git/config", "*/page.tsx"):
            self.assertIsNotNone(bad(no), no)

    def test_omitted_list_keeps_real_names(self):
        """没带进项目的提醒：首尾带空格的真实文件名也要列出来，不能因为拿名字配自己而漏掉。"""
        from collab.merge import _keep
        self.assertTrue(_keep(" [id]/page.tsx", None, {" [id]/page.tsx"}))
        self.assertFalse(_keep("node_modules/x.js", None, {"node_modules/x.js"}))

    def test_undo_backup_branch_resets_target_index(self):
        from collab.dispatch import _TEST_BACKUP, _undo_tester_changes
        target = self.wt / "app/x/[id]/page.tsx"
        target.write_text("TESTER\n", encoding="utf-8")
        _git(self.wt, "add", ":(literal)app/x/[id]/page.tsx")
        cdir = self.wt / ".card2"
        saved = cdir / _TEST_BACKUP / "app" / "x" / "[id]" / "page.tsx"
        saved.parent.mkdir(parents=True)
        saved.write_text("DEV\n", encoding="utf-8")
        _undo_tester_changes(cdir, self.wt, ["app/x/[id]/page.tsx"], {"app/x/[id]/page.tsx": "x"}, lambda w, p: None)
        self.assertEqual(target.read_text(encoding="utf-8"), "DEV\n")
        self.assertEqual(_git(self.wt, "show", ":app/x/[id]/page.tsx").stdout, "BASE\n", "目标的暂存也回到提交时")
        self.neighbours_untouched()

    def test_revert_new_file_target_unstaged(self):
        from collab.vcs import revert_paths
        (self.wt / "app/x/[id]/new.ts").write_text("NEW\n", encoding="utf-8")
        _git(self.wt, "add", ":(literal)app/x/[id]/new.ts")
        revert_paths(self.wt, ["app/x/[id]/new.ts"])
        done = subprocess.run(["git", "ls-files", "--error-unmatch", "--", ":(literal)app/x/[id]/new.ts"], cwd=self.wt,
                              capture_output=True)
        self.assertNotEqual(done.returncode, 0, "目标也移出暂存区")

    def test_planner_allows_bracket_first_segment(self):
        from collab import planner
        bad = [n for n in dir(planner) if callable(getattr(planner, n))]
        checker = next(getattr(planner, n) for n in bad
                       if getattr(getattr(planner, n), "__code__", None)
                       and "第一段不能用通配符" in getattr(planner, n).__code__.co_consts)
        self.assertIsNone(checker("[id]/page.tsx"))
        self.assertIsNotNone(checker("*/page.tsx"))

    def test_preview_exclude_escapes_brackets(self):
        from desktop.preview import _escape_ignore
        self.assertEqual(_escape_ignore("[id]/node_modules"), "\\[id\\]/node_modules")

    def test_keep_does_not_strip_real_names(self):
        from collab.merge import _keep
        self.assertFalse(_keep(" [id]/page.tsx", ["[id]/page.tsx"], set()), "名字前面带空格的是另一个文件")
        self.assertTrue(_keep("[id]/page.tsx", ["[id]/page.tsx"], set()))


if __name__ == "__main__":
    unittest.main()


class OrderScopeChecked(unittest.TestCase):
    """复查第 7 轮严重 1：照施工单直接生成方案，也走 AI 方案那道范围检查（11-27 用户定收口，只补这一条）。"""

    def _gaps(self, allowed):
        from collab import planner
        return planner._order_gaps_of(dict(summary="修页面", allowed=allowed, test_command="true"), "")

    def test_bad_bracket_scope_rejected(self):
        from collab import planner
        for bad in ("[id]/*.tsx", "[id]/logo.svg(草稿)"):
            gaps = self._gaps([bad])
            self.assertIn("合法的允许范围", gaps, bad)
            text = planner._order_gap_text("施工单.md", gaps, dict(allowed=[bad]), "")
            self.assertIn(bad, text)

    def test_normal_scope_still_ok(self):
        for ok in (["src/*.py"], ["app/x/[id]/page.tsx"], ["a.py", "docs/说明.md"]):
            self.assertEqual(self._gaps(ok), [], ok)
