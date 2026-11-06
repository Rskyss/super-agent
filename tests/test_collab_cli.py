import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collab.agents import AgentResult
from collab.cli import main
from collab.judge import PASS, Verdict


_SAVED_SANDBOX = None


def setUpModule():
    """在 Codex 的受保护环境里跑这些测试时，先去掉那个标记，免得“用假执行者开工”的测试被守卫误拦。"""
    global _SAVED_SANDBOX
    _SAVED_SANDBOX = os.environ.pop("CODEX_SANDBOX", None)


def tearDownModule():
    if _SAVED_SANDBOX is not None:
        os.environ["CODEX_SANDBOX"] = _SAVED_SANDBOX


def _git_tags(path) -> list:
    out = subprocess.run(["git", "tag"], cwd=path, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path: Path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("demo\n")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")


def _run(argv, **injected):
    """返回 (退出码, 标准输出+标准错误)，错误信息也要能被断言到。"""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = main(argv, **injected)
    return code, buf.getvalue()


def _fill_profile(project: Path):
    (project / ".collab" / "项目档案.md").write_text(
        "# 项目档案\n\n- 怎么启动：python3 greet.py\n- 怎么测试：python3 -m unittest\n"
        "- 禁区：不改 README\n- 技术栈：纯 Python\n", encoding="utf-8")


def _confirm_profile_and_plan(project: Path, card_id: str = "CARD-001"):
    _fill_profile(project)
    _run(["confirm-profile", str(project)])
    card_dir = project / ".collab" / "cards" / card_id
    (card_dir / "方案.md").write_text("第一步：加函数。第二步：加测试。", encoding="utf-8")
    code, out = _run(["approve", str(project), card_id])
    assert code == 0, out


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "demo"
        _make_repo(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_collab_and_is_idempotent(self):
        code, _ = _run(["init", str(self.project)])
        self.assertEqual(code, 0)
        self.assertTrue((self.project / ".collab" / "任务板.md").exists())
        code, _ = _run(["init", str(self.project)])
        self.assertEqual(code, 0)

    def test_new_card_writes_requirement_and_sets_waiting_state(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("加一个函数", encoding="utf-8")
        code, out = _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        self.assertEqual(code, 0)
        self.assertIn("待对齐", out)  # 提示必须和真实状态一致
        self.assertNotIn("待派发", out)
        card_dir = self.project / ".collab" / "cards" / "CARD-001"
        self.assertEqual((card_dir / "需求卡.md").read_text(encoding="utf-8"), "加一个函数")
        state = json.loads((card_dir / "状态.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "待对齐")  # 没有方案和确认前不能派发

    def test_status_prints_board(self):
        _run(["init", str(self.project)])
        code, out = _run(["status", str(self.project)])
        self.assertEqual(code, 0)
        self.assertIn("任务板", out)

    def test_run_refuses_before_plan_is_approved(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"])
        self.assertNotEqual(code, 0)
        self.assertIn("待对齐", out)

    def test_approve_needs_project_profile_confirmed(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        (self.project / ".collab" / "cards" / "CARD-001" / "方案.md").write_text("方案", encoding="utf-8")
        code, out = _run(["approve", str(self.project), "CARD-001"])
        self.assertNotEqual(code, 0)
        self.assertIn("项目说明", out)

    def test_approve_needs_plan_file(self):
        _run(["init", str(self.project)])
        _fill_profile(self.project)
        _run(["confirm-profile", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        code, out = _run(["approve", str(self.project), "CARD-001"])
        self.assertNotEqual(code, 0)
        self.assertIn("方案", out)

    def test_approve_moves_card_to_dispatchable(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)
        state = json.loads((self.project / ".collab" / "cards" / "CARD-001" / "状态.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "待派发")

    def test_confirm_profile_is_recorded_in_project_ledger(self):
        _run(["init", str(self.project)])
        _fill_profile(self.project)
        _run(["confirm-profile", str(self.project)])
        ledger = (self.project / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn("项目说明已确认", ledger)

    def test_confirm_profile_records_flag_without_losing_other_settings(self):
        _run(["init", str(self.project)])
        _fill_profile(self.project)
        (self.project / ".collab" / "配置.json").write_text(
            json.dumps({"codex": "gpt-x"}), encoding="utf-8")
        _run(["confirm-profile", str(self.project)])
        settings = json.loads((self.project / ".collab" / "配置.json").read_text(encoding="utf-8"))
        self.assertTrue(settings["project_profile_confirmed"])
        self.assertEqual(settings["codex"], "gpt-x")

    def test_new_card_creates_plan_template_with_blanks(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        plan = (self.project / ".collab" / "cards" / "CARD-001" / "方案.md").read_text(encoding="utf-8")
        self.assertIn("拆成几步", plan)
        self.assertIn("（待填）", plan)

    def test_confirm_profile_refuses_when_template_still_has_blanks(self):
        from collab.workspace import PROFILE_TEMPLATE
        _run(["init", str(self.project)])
        (self.project / "docs").mkdir(exist_ok=True)
        (self.project / "docs" / "项目说明.md").write_text(PROFILE_TEMPLATE, encoding="utf-8")
        code, out = _run(["confirm-profile", str(self.project)])
        self.assertNotEqual(code, 0)
        self.assertIn("空白", out)

    def test_approve_refuses_when_plan_still_has_blanks(self):
        _run(["init", str(self.project)])
        _fill_profile(self.project)
        _run(["confirm-profile", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        code, out = _run(["approve", str(self.project), "CARD-001"])
        self.assertNotEqual(code, 0)
        self.assertIn("空白", out)

    def test_approve_and_run_are_recorded_in_project_ledger(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)
        ledger = (self.project / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn("CARD-001", ledger)
        self.assertIn("方案已确认", ledger)

    def test_run_refuses_when_requirement_has_no_scope(self):
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("只有正文，没有范围", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)
        code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"])
        self.assertNotEqual(code, 0)
        self.assertIn("允许改动的文件范围", out)

    def test_run_refuses_inside_codex_protected_environment(self):
        """在 Codex 的受保护环境里（调度员没申请权限）不准开工，逼它申请，让用户看到“允许吗？”。"""
        _run(["init", str(self.project)])
        req = self.root / "req.md"
        req.write_text("加 hello\n\n## 允许改动的范围\n- hello.txt\n", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)
        os.environ["CODEX_SANDBOX"] = "seatbelt"
        try:
            code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"])
        finally:
            del os.environ["CODEX_SANDBOX"]
        self.assertNotEqual(code, 0)
        self.assertIn("申请", out)
        state = json.loads((self.project / ".collab" / "cards" / "CARD-001" / "状态.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "待派发")  # 什么都没开始
        self.assertEqual(_git_tags(self.project), [])

    def test_run_refuses_non_git_project(self):
        plain = self.root / "plain"
        plain.mkdir()
        _run(["init", str(plain)])
        req = self.root / "req.md"
        req.write_text("x", encoding="utf-8")
        _run(["new-card", str(plain), "CARD-001", "--requirement", str(req)])
        code, out = _run(["run", str(plain), "CARD-001", "--test-command", "true"])
        self.assertNotEqual(code, 0)
        self.assertIn("git", out)

    def test_run_end_to_end_with_fake_agents(self):
        _run(["init", str(self.project)])
        (self.project / ".collab" / "项目档案.md").write_text("测试命令：true\n", encoding="utf-8")
        req = self.root / "req.md"
        req.write_text("加 hello 函数\n\n## 允许改动的范围\n- hello.txt\n", encoding="utf-8")
        _run(["new-card", str(self.project), "CARD-001", "--requirement", str(req)])
        _confirm_profile_and_plan(self.project)

        calls = []

        def fake_ask(adapter, prompt, cwd, **kwargs):
            calls.append((adapter, Path(cwd).name))
            (Path(cwd) / "hello.txt").write_text("hi")  # 开发员在副本里改了文件
            return AgentResult(ok=True, output="结论：通过", attempts=1)

        def fake_judge(command, cwd, timeout):
            return Verdict(PASS, 0, "全部通过")

        code, out = _run(["run", str(self.project), "CARD-001", "--test-command", "true"],
                         ask_fn=fake_ask, judge_fn=fake_judge)
        self.assertEqual(code, 0, out)

        worktree = self.root / "demo-worktrees" / "CARD-001"
        self.assertTrue(worktree.exists())
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=worktree,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "card/CARD-001")
        state = json.loads((self.project / ".collab" / "cards" / "CARD-001" / "状态.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "待验收")
        tags = subprocess.run(["git", "tag"], cwd=self.project, capture_output=True,
                              text=True).stdout
        self.assertIn("pre-CARD-001", tags)
        self.assertTrue((self.project / ".collab" / "备份记录.md").read_text(encoding="utf-8")
                        .count("pre-CARD-001") >= 1)
        # 主工作区不受影响
        self.assertFalse((self.project / "hello.txt").exists())


class EntryPointTest(unittest.TestCase):
    def test_package_folder_runs_from_any_working_directory(self):
        package_dir = Path(__file__).resolve().parent.parent / "collab"
        with tempfile.TemporaryDirectory() as elsewhere:
            proc = subprocess.run([sys.executable, str(package_dir), "--help"],
                                  cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("new-card", proc.stdout)


if __name__ == "__main__":
    unittest.main()
