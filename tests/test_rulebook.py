"""规则包（docs/规则包/，用户 2026-11-20 批准）：给大模型的规则搬进 agents/，程序派活时按角色把规则原样放在最前面。

管的是程序行为：读取器按角色读对、缺文件时报出来不悄悄跑空规则、各角色派活带的是自己那份、
复查员的话里不再自带分级说明（改由《复查分级》提供）、2b（标“极巧合”的不退回，不管实跑还是推演）、打包带上 agents/。
规则文字本身由 tests/test_rulebook_health.py（规则包体检）管。
"""
import inspect
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab import rulebook

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"


class Reader(unittest.TestCase):
    def test_reads_registered_files_in_order(self):
        for role, files in rulebook.roles().items():
            text = rulebook.rules_for(role)
            spots = []
            for rel in files:
                body = (AGENTS / rel).read_text(encoding="utf-8").strip()
                self.assertIn(body, text, f"{role} 缺 {rel}")
                spots.append(text.index(body))
            self.assertEqual(spots, sorted(spots), f"{role} 的文件要按登记顺序拼")

    def test_same_text_every_time_and_no_install_path(self):
        # 每次一字不差，服务才能记住重复的开头；不带本机路径，开发时和装好的应用里一样
        self.assertEqual(rulebook.rules_for("复查"), rulebook.rules_for("复查"))
        self.assertNotIn(str(ROOT), rulebook.rules_for("复查"))

    def test_block_is_marked_and_ends_with_blank_line(self):
        text = rulebook.rules_for("写代码")
        self.assertTrue(text.startswith("【规则】"))
        self.assertTrue(text.endswith("【规则完】\n\n"))

    def test_roles_cover_program_roles(self):
        for role in ("写测试", "写代码", "复查", "写方案", "备交接资料", "起草交代"):
            self.assertIn(role, rulebook.roles())


class MissingIsReported(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, self.root)

    def test_missing_file_is_written_into_the_rules(self):
        (self.root / "角色" / "写测试.md").unlink()
        text = rulebook.rules_for("写测试", root=self.root)
        self.assertIn("规则文件缺失", text)
        self.assertIn("角色/写测试.md", text)
        self.assertIn("说话口径", text, "其余文件照样带上")
        self.assertEqual(rulebook.missing_files(root=self.root), ["角色/写测试.md"])

    def test_missing_index_does_not_crash(self):
        (self.root / "README.md").unlink()
        text = rulebook.rules_for("写测试", root=self.root)
        self.assertIn("规则文件缺失", text)
        self.assertIn("README.md", text)
        self.assertIn("README.md", rulebook.missing_files(root=self.root))

    def test_role_with_no_files_is_reported(self):
        # Codex 复查第 1 轮第 1 条：目录表里某角色的文件被误清空，不能带空规则悄悄跑
        index = self.root / "README.md"
        text = index.read_text(encoding="utf-8")
        old = "| 写代码 | 通用/说话口径.md、通用/四道关卡.md、角色/写代码.md |"
        self.assertIn(old, text)
        index.write_text(text.replace(old, "| 写代码 |  |"), encoding="utf-8")
        self.assertIn("规则文件缺失", rulebook.rules_for("写代码", root=self.root))
        self.assertTrue(any("写代码" in m for m in rulebook.missing_files(root=self.root)))

    def test_role_row_written_any_broken_way_is_reported(self):
        # Codex 复查第 2 轮：“| 写代码 ||”这种写法整行被丢，只提示“没有登记”。程序用到的角色缺了，不管表怎么坏都要报
        index = self.root / "README.md"
        text = index.read_text(encoding="utf-8")
        old = "| 写代码 | 通用/说话口径.md、通用/四道关卡.md、角色/写代码.md |"
        for broken in ("| 写代码 ||", ""):
            with self.subTest(broken=broken):
                index.write_text(text.replace(old, broken), encoding="utf-8")
                self.assertIn("规则文件缺失", rulebook.rules_for("写代码", root=self.root))
                self.assertTrue(any("写代码" in m for m in rulebook.missing_files(root=self.root)))

    def test_program_roles_list_matches_code(self):
        from collab import dispatch, planner
        used = set()
        for module in (dispatch, planner):
            used |= set(re.findall(r"_rules\(['\"]([^'\"]+)['\"]\)", inspect.getsource(module)))
        self.assertEqual(used, set(rulebook.PROGRAM_ROLES))

    def test_empty_file_counts_as_missing(self):
        (self.root / "角色" / "复查.md").write_text("  \n", encoding="utf-8")
        self.assertIn("规则文件缺失：角色/复查.md", rulebook.rules_for("复查", root=self.root))
        self.assertIn("角色/复查.md", rulebook.missing_files(root=self.root))

    def test_unknown_role_is_reported(self):
        text = rulebook.rules_for("不存在的角色", root=self.root)
        self.assertIn("规则文件缺失", text)
        self.assertIn("不存在的角色", text)

    def test_run_card_notes_missing_rules_in_the_flow(self):
        from collab.agents import AgentResult
        from collab.dispatch import run_card
        from collab.flow import read_flow
        from collab.judge import PASS, Verdict
        from collab.state import Card
        from collab.workspace import init_collab
        from review_fmt import listed_ask
        (self.root / "手册" / "复查分级.md").unlink()
        with tempfile.TemporaryDirectory() as d:
            collab = init_collab(d)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("x", encoding="utf-8")
            with mock.patch.object(rulebook, "AGENTS_DIR", self.root):
                run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=d, test_command=["t"],
                         dev_adapter="cursor", test_adapter="codex",
                         ask_fn=listed_ask(lambda a, p, c, **k: AgentResult(ok=True, output="结论：通过", attempts=1)),
                         judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
            notes = [e for e in read_flow(cdir) if e.get("kind") == "提醒" and "规则" in e.get("summary", "")]
        self.assertTrue(notes, "缺规则文件要在流转里记一条，不悄悄跑")
        self.assertIn("复查分级", notes[0]["summary"])

    def test_run_card_notes_broken_message_in_the_flow(self):
        # Codex 复查 T1 第 2 条：交代里多了程序填不了的空位，也要在流转里记提醒
        from collab.agents import AgentResult
        from collab.dispatch import run_card
        from collab.flow import read_flow
        from collab.judge import PASS, Verdict
        from collab.state import Card
        from collab.workspace import init_collab
        from review_fmt import listed_ask
        path = self.root / "交代" / "复查" / "每轮复查.md"
        path.write_text(path.read_text(encoding="utf-8").replace("不要修改任何文件。", "{未登记空位}不要修改任何文件。"),
                        encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            collab = init_collab(d)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("x", encoding="utf-8")
            with mock.patch.object(rulebook, "AGENTS_DIR", self.root):
                run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=d, test_command=["t"],
                         dev_adapter="cursor", test_adapter="codex",
                         ask_fn=listed_ask(lambda a, p, c, **k: AgentResult(ok=True, output="结论：通过", attempts=1)),
                         judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
            notes = [e for e in read_flow(cdir) if e.get("kind") == "提醒" and "规则" in e.get("summary", "")]
        self.assertTrue(notes, "交代坏了要在流转里记一条")
        self.assertIn("未登记空位", notes[0]["summary"])


class EachRoleGetsItsOwnRules(unittest.TestCase):
    def test_card_run_puts_role_rules_first(self):
        from collab.agents import AgentResult
        from collab.dispatch import run_card
        from collab.judge import PASS, Verdict
        from collab.state import Card
        from collab.workspace import init_collab
        from review_fmt import listed_ask
        prompts = {}

        def ask(adapter, prompt, cwd, **kw):
            prompts.setdefault(adapter, prompt)
            return AgentResult(ok=True, output="结论：通过", attempts=1)

        with tempfile.TemporaryDirectory() as d:
            collab = init_collab(d)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("x", encoding="utf-8")
            run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=d, test_command=["t"],
                     dev_adapter="cursor", test_adapter="codex", ask_fn=listed_ask(ask),
                     judge_fn=lambda c, cwd, timeout: Verdict(PASS, 0, "ok"), reviewer_adapter="agy")
        for adapter, role in (("codex", "写测试"), ("cursor", "写代码"), ("agy", "复查")):
            self.assertTrue(prompts[adapter].startswith(rulebook.rules_for(role)), adapter)

    def test_handover_planner_and_reformat_prompts(self):
        from collab import dispatch
        from collab.planner import _build_prompt
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d)
            self.assertTrue(dispatch._handover_prompt("需求卡.md", cdir).startswith(rulebook.rules_for("备交接资料")))
            self.assertTrue(_build_prompt(cdir, "# 改按钮\n", False).startswith(rulebook.rules_for("写方案")))
            self.assertTrue(dispatch._reformat_prompt(cdir, "原回复", ["缺清单"], "需求卡.md", d)
                            .startswith(rulebook.rules_for("复查")))

    def test_no_old_read_the_file_opening_left(self):
        from collab import dispatch, planner
        for module in (dispatch, planner):
            src = inspect.getsource(module)
            self.assertNotIn("_READ_RULES", src)
            self.assertNotIn("开工前先读规则", src)
            for role in re.findall(r"_rules\(['\"]([^'\"]+)['\"]\)", src):
                self.assertIn(role, rulebook.roles(), f"{module.__name__} 用了没登记的角色 {role}")
        # 第③步起两处“测试没过”合成一个函数，按处数数不准了：改查真跑一张卡时每段派活的话都以规则开头
        import prompt_cases
        for case, text in prompt_cases.render("跑卡").items():
            self.assertTrue(text.startswith("【规则】"), case)


class ReviewerPromptLeavesGradingToRulebook(unittest.TestCase):
    def test_grading_words_come_only_from_the_rulebook(self):
        from collab import dispatch
        with tempfile.TemporaryDirectory() as d:
            prompt = dispatch._review_prompt(Path(d), "需求卡.md", "", "", d, False)
        rules = rulebook.rules_for("复查")
        self.assertTrue(prompt.startswith(rules))
        own = prompt[len(rules):]
        for words in ("两步之间", "特定操作", "泄露密钥", "一两秒空档", "没发出去的字", "只有严重的才退回", "一律标【严重】"):
            self.assertNotIn(words, own, f"“{words}”只写在《复查分级》一处")
        for field in ("级别", "来源", "类型", "碰上", "现象", "位置", "证据", "结论：通过", "复查清单"):
            self.assertIn(field, own, "复查清单格式这次不搬，还在派活的话里")


class UnlikelyIsNeverReturned(unittest.TestCase):
    """2b（用户 11-20 定）：标“极巧合”的不退回，不管实跑还是推演；测试写错、安全或丢数据照退。"""

    @staticmethod
    def item(level="严重", kind="功能问题", hit="极巧合", how="实跑"):
        proof = ({"方式": "实跑", "命令": "node x.js", "输出": "FAILED"} if how == "实跑"
                 else {"方式": "推演", "对照": "a.py:1"})
        return {"级别": level, "来源": "这次改的", "类型": kind, "碰上": hit,
                "现象": "x", "位置": "a.py:1", "证据": proof}

    def test_reproduced_but_unlikely_is_not_returned(self):
        from collab.dispatch import _needs_return
        self.assertFalse(_needs_return(self.item()))
        self.assertFalse(_needs_return(self.item(level="中等")))
        self.assertFalse(_needs_return(self.item(), first_round=False))

    def test_wrong_tests_and_data_loss_still_returned(self):
        from collab.dispatch import _needs_return
        self.assertTrue(_needs_return(self.item(kind="测试写错")))
        self.assertTrue(_needs_return(self.item(kind="安全或丢数据")))

    def test_reproduced_rare_still_returned(self):
        from collab.dispatch import _needs_return
        self.assertTrue(_needs_return(self.item(hit="少见")))
        self.assertTrue(_needs_return(self.item(hit="少见", level="中等")))


class Packaging(unittest.TestCase):
    def test_app_build_copies_agents_next_to_collab(self):
        build = (ROOT / "desktop" / "mac" / "build.sh").read_text(encoding="utf-8")
        self.assertRegex(build, r'rsync[^\n]*"\$ROOT/agents"[^\n]*super-agent/')

    def test_reader_finds_agents_next_to_collab(self):
        # 装好的应用里是 super-agent/collab 和 super-agent/agents 并排；开发时是仓库里并排
        self.assertEqual(rulebook.AGENTS_DIR, Path(rulebook.__file__).resolve().parent.parent / "agents")

    def test_records_readme_points_to_agents(self):
        from collab.workspace import RECORDS_README
        self.assertIn(str(rulebook.AGENTS_DIR / "README.md"), RECORDS_README)

    def test_old_rules_file_kept_as_pointer(self):
        # 窗口外壳和安装程序靠它认出“这是协作包”，不能删；内容只指向 agents/，不再写两处
        text = (ROOT / "collab" / "规则.md").read_text(encoding="utf-8")
        self.assertIn("agents/README.md", text)
        self.assertLess(len(text.splitlines()), 8)


if __name__ == "__main__":
    unittest.main()
