"""流程修理第 4、5、6 条（试跑记录 ⑱⑭⑮）。

⑱ 68b 里点“继续改”后，写测试的 Codex 直接改了产品代码 desk.py，程序把它当成“测试文件”锁住，之后写代码的两轮改动全被撤回。
⑭ 点“继续改”后写测试整个重来（15 分钟）。
⑮ 写测试、复查每次各自跑全部测试（10 分半）。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import is_test_path, run_card
from collab.flow import read_flow, record
from collab.judge import PASS, Verdict
from collab.state import Card
from collab.tasks import revise_card
from review_fmt import listed_ask  # C1.5：老测试的随手复查意见补上固定清单（tests/review_fmt.py）

CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 -m unittest tests.test_x"]


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TestPaths(unittest.TestCase):
    def test_what_counts_as_a_test_file(self):
        for rel in ("tests/test_x.py", "tests/helpers.py", "test/a.js", "web/__tests__/a.js", "spec/a_spec.rb",
                    "test_top.py", "pkg/foo_test.go", "src/a.test.ts", "src/a.spec.js", "tests/naming_check.js",
                    "pkg/x_check.js"):
            self.assertTrue(is_test_path(rel), rel)
        for rel in ("desktop/clerk/desk.py", "README.md", "src/testing_utils.py", "contest/a.py", "docs/tests.md"):
            self.assertFalse(is_test_path(rel), rel)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "proj"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "t@t")
        git(self.project, "config", "user.name", "t")
        (self.project / "app.py").write_text("A = 1\n", encoding="utf-8")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_old.py").write_text("# old\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "init")
        self.wt = self.project  # 用项目本身当施工副本即可
        self.collab = self.project / ".collab"
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        self.prompts = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, tester, dev=None, card=None):
        def ask_fn(adapter, prompt, cwd, **kw):
            self.prompts.append(prompt)
            if "编写自动化测试" in prompt or "补测试" in prompt:
                tester(Path(cwd))
                return AgentResult(ok=True, output="", attempts=1, final="写好了测试")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if dev:
                dev(Path(cwd))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")
        card = card or Card("CARD-001", state="待派发")
        return run_card(card, self.collab, self.wt, CMD, "cursor", "codex", ask_fn=listed_ask(ask_fn),
                        judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                        require_tests=True)[0]


class TesterOnlyTouchesTests(Base):
    def test_product_change_by_tester_is_undone(self):
        def tester(wt):
            (wt / "app.py").write_text("A = 2  # 写测试的偷偷改了\n", encoding="utf-8")
            (wt / "tests" / "test_x.py").write_text("# new test\n", encoding="utf-8")
        self.run_with(tester)
        self.assertEqual((self.wt / "app.py").read_text(encoding="utf-8"), "A = 1\n")
        files = (self.cdir / "测试文件.json").read_text(encoding="utf-8")
        self.assertIn("tests/test_x.py", files)
        self.assertNotIn("app.py", files)
        scope = [e for e in read_flow(self.cdir) if e.get("kind") == "范围" and "写测试" in e.get("summary", "")]
        self.assertTrue(scope, "流转里要记一笔：写测试的改了测试以外的文件，已撤掉")
        self.assertIn("app.py", scope[0]["summary"])

    def test_undo_keeps_developer_work_from_earlier_rounds(self):
        """继续改时写测试的又去改开发员改过的文件：只撤它这次的改动，开发员之前的改动要留着"""
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"),
                      dev=lambda wt: (wt / "app.py").write_text("A = 10  # 开发员改的\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "再加一个按钮")
        card = Card.load(self.cdir / "状态.json")

        def tester(wt):
            (wt / "app.py").write_text("A = 99  # 写测试的乱改\n", encoding="utf-8")
            (wt / "tests" / "test_y.py").write_text("# 补的测试\n", encoding="utf-8")
        self.run_with(tester, card=card)
        self.assertEqual((self.wt / "app.py").read_text(encoding="utf-8"), "A = 10  # 开发员改的\n")


class ReviseOnlyAddsTests(Base):
    def test_revise_asks_tester_to_add_tests_only(self):
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "按钮改成蓝色")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")
        self.run_with(lambda wt: (wt / "tests" / "test_y.py").write_text("# 补\n", encoding="utf-8"), card=card)
        tester_prompt = self.prompts[0]
        self.assertIn("补测试", tester_prompt)
        self.assertIn("按钮改成蓝色", tester_prompt)
        self.assertIn("tests/test_x.py", tester_prompt, "告诉它以前写好的测试有哪些，不要重写")
        files = (self.cdir / "测试文件.json").read_text(encoding="utf-8")
        self.assertIn("tests/test_x.py", files)
        self.assertIn("tests/test_y.py", files)


class OnlyCardTests(Base):
    def test_every_worker_is_told_the_card_test_command(self):
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        self.assertEqual(len(self.prompts), 3)
        for prompt in self.prompts:
            self.assertIn("python3 -m unittest tests.test_x", prompt)
            self.assertIn("不要跑全部测试", prompt)



class ReviseMayUpdateConflictingTests(Base):
    """Claude 最终验收（第 14–21 项）真卡 CARD-020：继续改把 count 从“只输出数字”改成截图里的文字，
    测试员按“已有测试不要重写”保留了旧的纯数字用例，开发员不许改测试，两套互斥、返工 3 次后停下。
    规矩改成：和追加要求冲突的旧用例按追加要求改掉；其余已有测试仍不要重写（保留提速的本意）。"""

    def test_revise_tester_may_fix_old_tests_that_conflict(self):
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "输出改成文字摘要")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")
        self.run_with(lambda wt: (wt / "tests" / "test_y.py").write_text("# 补\n", encoding="utf-8"), card=card)
        tester_prompt = self.prompts[0]
        self.assertIn("和追加要求冲突", tester_prompt)
        self.assertIn("按追加要求改", tester_prompt)
        self.assertIn("其余已有的测试不要重写", tester_prompt)
        self.assertNotIn("已有的测试不要重写；", tester_prompt, "不能再无条件禁止改旧测试")

    def test_reviewer_is_told_which_old_tests_were_changed(self):
        """复查第 1 轮（Codex）：放宽后要有护栏——测试员改了/删了哪些已有测试，复查员要拿到清单逐个核对。"""
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "输出改成文字摘要")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# 改掉了冲突用例\n", encoding="utf-8"), card=card)
        review_prompt = next(p for p in self.prompts if "只读复查" in p)
        self.assertIn("改动了已有的测试", review_prompt)
        self.assertIn("tests/test_x.py", review_prompt)
        self.assertIn("测试前备份", review_prompt)

    def test_reviewer_not_warned_when_old_tests_untouched(self):
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "再加一个按钮")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")
        self.run_with(lambda wt: (wt / "tests" / "test_y.py").write_text("# 只追加\n", encoding="utf-8"), card=card)
        review_prompt = next(p for p in self.prompts if "只读复查" in p)
        self.assertNotIn("改动了已有的测试", review_prompt)


    def test_reviewer_told_when_committed_old_test_is_loosened_or_deleted(self):
        """复查第 2 轮（Codex）：项目里早就提交好的旧测试（派测试员前没改动、不在备份里）被放松或整个删掉，也要告诉复查员。"""
        (self.project / "tests" / "test_keep.py").write_text("# keep\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "more tests")
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "输出改成文字摘要")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")

        def tester(wt):
            (wt / "tests" / "test_old.py").write_text("# 放松了\n", encoding="utf-8")
            (wt / "tests" / "test_keep.py").unlink()
        self.run_with(tester, card=card)
        review_prompt = next(p for p in self.prompts if "只读复查" in p)
        self.assertIn("tests/test_old.py", review_prompt)
        self.assertIn("tests/test_keep.py（整个删了）", review_prompt)


    def test_chinese_named_old_test_is_reported(self):
        """复查第 3 轮（Codex）：git 默认把中文文件名转义，中文名的旧测试被改会漏报。"""
        (self.project / "tests" / "test_中文.py").write_text("# 中文\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "zh test")
        self.run_with(lambda wt: (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8"))
        revise_card(self.project, "CARD-001", "输出改成文字摘要")
        self.prompts.clear()
        card = Card.load(self.cdir / "状态.json")
        self.run_with(lambda wt: (wt / "tests" / "test_中文.py").write_text("# 放松\n", encoding="utf-8"), card=card)
        review_prompt = next(p for p in self.prompts if "只读复查" in p)
        self.assertIn("tests/test_中文.py", review_prompt)
        self.assertIn("git show HEAD:", review_prompt, "备份里没有的旧测试，要告诉复查员去看提交时的样子")


if __name__ == "__main__":
    unittest.main()
