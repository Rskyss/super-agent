"""流程修理：Codex 独立复查第 1 轮（流程那一块）查出的问题，逐条复现后补的测试。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult, ask, cursor_command
from collab.dispatch import cursor_permissions, run_card
from collab.flow import read_flow, record
from collab.judge import PASS, Verdict
from collab.phase import card_phase
from collab.state import Card
from collab.tasks import revise_card
from collab.vcs import revert_paths

CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 -m unittest tests.test_x"]


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


class GitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "proj"
        (self.p / "src").mkdir(parents=True)
        (self.p / "tests").mkdir()
        (self.p / "src" / "app.py").write_text("A = 1\n", encoding="utf-8")
        (self.p / "tests" / "test_old.py").write_text("# old\n", encoding="utf-8")
        git(self.p, "init", "-q")
        git(self.p, "add", ".")
        git(self.p, "commit", "-qm", "init")
        self.collab = self.p / ".collab"
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 允许改动的范围\n- src/app.py\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, tester, dev=None, card=None):
        def ask_fn(adapter, prompt, cwd, **kw):
            if "编写自动化测试" in prompt or "补测试" in prompt:
                tester(Path(cwd))
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if dev:
                dev(Path(cwd))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：好了")
        return run_card(card or Card("CARD-001", state="待派发"), self.collab, self.p, CMD, "codex", "codex",
                        ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                        require_tests=True, allowed=["src/app.py"])[0]


class CursorSandbox(unittest.TestCase):
    def test_cursor_always_runs_in_sandbox(self):
        """1：放行了测试命令，就必须开沙箱（只能写施工副本和临时文件夹）。2026-11-12 实测：开沙箱后 python3 能跑、写不到副本外"""
        cmd = cursor_command("干活")
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "enabled")


class TesterNewProductFile(GitBase):
    def test_new_file_in_product_folder_is_undone(self):
        """2：写测试的在原有产品文件夹里新建文件，不能算测试、更不能进项目"""
        def tester(wt):
            (wt / "src" / "rogue.py").write_text("x = 1\n", encoding="utf-8")
            (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
        self.run_with(tester)
        self.assertFalse((self.p / "src" / "rogue.py").exists())
        self.assertNotIn("rogue", (self.cdir / "测试文件.json").read_text(encoding="utf-8"))

    def test_new_top_folder_still_counts_as_tests(self):
        def tester(wt):
            (wt / "e2e").mkdir()
            (wt / "e2e" / "a.txt").write_text("真测试\n", encoding="utf-8")
        self.run_with(tester)
        self.assertTrue((self.p / "e2e" / "a.txt").exists())


class UndoKeepsDeletion(GitBase):
    def test_developer_deletion_survives_tester_recreating_file(self):
        """3：开发员删掉的文件，写测试的又建回来：撤销后要还是删掉的"""
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"),
                      dev=lambda wt: (wt / "src" / "app.py").unlink())
        revise_card(self.p, "CARD-001", "再改改")
        card = Card.load(self.cdir / "状态.json")

        def tester(wt):
            (wt / "src" / "app.py").write_text("A = 99\n", encoding="utf-8")
            (wt / "tests" / "test_y.py").write_text("# t\n", encoding="utf-8")
        self.run_with(tester, card=card)
        self.assertFalse((self.p / "src" / "app.py").exists())


class UndoStaged(GitBase):
    def test_staged_change_is_really_undone(self):
        """4：写测试的把产品改动暂存了（git add），撤销也要撤干净"""
        def tester(wt):
            (wt / "src" / "app.py").write_text("A = 2\n", encoding="utf-8")
            git(wt, "add", "src/app.py")
            (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
        self.run_with(tester)
        self.assertEqual((self.p / "src" / "app.py").read_text(encoding="utf-8"), "A = 1\n")
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.p, capture_output=True,
                                text=True).stdout
        self.assertNotIn("src/app.py", staged)

    def test_revert_paths_handles_staged_new_file(self):
        (self.p / "src" / "new.py").write_text("x\n", encoding="utf-8")
        git(self.p, "add", "src/new.py")
        revert_paths(self.p, ["src/new.py"])
        self.assertFalse((self.p / "src" / "new.py").exists())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.p, capture_output=True, text=True).stdout
        self.assertNotIn("new.py", status)


class CodexEmptyLastMessage(unittest.TestCase):
    def test_empty_last_file_is_empty_reply_not_transcript(self):
        """5：Codex 最后回复文件是空的，不能拿整段过程当结论（过程里可能读到别处的“结论：通过”）"""
        def runner(cmd, cwd, timeout, **kw):
            Path(cmd[cmd.index("-o") + 1]).write_text("", encoding="utf-8")
            return 0, "codex\n读到旧文档：\n结论：通过\n"
        result = ask("codex", "复查", tempfile.gettempdir(), runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("没有回复任何内容", result.output)


class AcceptOnlyForRealReviewStop(GitBase):
    def test_worker_failure_mentioning_review_does_not_offer_accept(self):
        """6：开发员出错的原话里带着“复查不通过”几个字，也不能出“接受剩下的问题”"""
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")
        record(self.cdir, "转人工", src="程序", dst="调度员",
               summary="开发员（cursor）没有完成：上一次复查不通过的修改任务：环境错误，无法启动工具")
        self.assertNotIn("accept_issues", card_phase(self.p, "CARD-001")["actions"])

    def test_real_review_stop_offers_accept_with_full_issues(self):
        """6、8：真的是复查不通过停下才给按钮；给用户看的问题不截断"""
        long_issue = "1. " + "很长的问题描述" * 20 + "\n2. 第二条问题"
        calls = {"n": 0}

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：不通过\n" + long_issue)
            if "编写自动化测试" in prompt:
                (Path(cwd) / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="好了")
        run_card(Card("CARD-001", state="待派发"), self.collab, self.p, CMD, "codex", "codex", ask_fn=ask_fn,
                 judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
        phase = card_phase(self.p, "CARD-001")
        self.assertIn("accept_issues", phase["actions"])
        self.assertIn("2. 第二条问题", phase.get("issues", ""))


class CursorSettingNotStaged(GitBase):
    def test_restore_also_unstages(self):
        """7：Cursor 干活时把放行设置也暂存了，还原时要从暂存里拿掉，不然挡住放进项目"""
        restore = cursor_permissions(self.p, CMD)
        git(self.p, "add", ".cursor/cli.json")
        restore()
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.p, capture_output=True,
                                text=True).stdout
        self.assertNotIn(".cursor", staged)
        self.assertFalse((self.p / ".cursor").exists())


if __name__ == "__main__":
    unittest.main()
