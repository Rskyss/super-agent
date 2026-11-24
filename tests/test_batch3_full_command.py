"""第三批一（⑯）：网页项目也跑“全部测试”——项目配一次“全部测试命令”，复查同时在副本里跑。

依据 docs/外部项目试跑修复/第三批/方案.md 第一节。"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow
from collab.fullrun import STOPPED
from collab.judge import FAIL, PASS, TIMEOUT, Verdict
from collab.state import Card
from review_fmt import listed_ask


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class ConfigCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_clear_keeps_other_settings(self):
        from collab.config import load_full_test, save_full_test
        (self.project / ".collab" / "配置.json").write_text('{"codex": "gpt-x"}', encoding="utf-8")
        self.assertEqual(load_full_test(self.project), "")
        save_full_test(self.project, "  npm test  ")
        self.assertEqual(load_full_test(self.project), "npm test")
        import json
        data = json.loads((self.project / ".collab" / "配置.json").read_text(encoding="utf-8"))
        self.assertEqual(data["codex"], "gpt-x", "别的设置原样保留")
        save_full_test(self.project, "")
        self.assertEqual(load_full_test(self.project), "")

    def test_cli_sets_and_shows(self):
        from collab.cli import main
        self.assertEqual(main(["config", "full-test", str(self.project), "pnpm test"]), 0)
        from collab.config import load_full_test
        self.assertEqual(load_full_test(self.project), "pnpm test")
        self.assertEqual(main(["config", "full-test", str(self.project), "--clear"]), 0)
        self.assertEqual(load_full_test(self.project), "")

    def test_runner_choice(self):
        from collab.config import save_full_test
        from collab.fullrun import CommandRunner, FullRunner, runner_for
        self.assertIsInstance(runner_for(self.project), FullRunner)
        save_full_test(self.project, "true")
        got = runner_for(self.project)
        self.assertIsInstance(got, CommandRunner)
        self.assertEqual(got.command, "true")


class CommandRunnerReal(unittest.TestCase):
    """真起进程：在副本里跑命令；node_modules 链接过去不复制；改之前的版本也能跑一次。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        self.wt.mkdir()
        (self.wt / "check.sh").write_text("grep -q good state.txt\n", encoding="utf-8")
        (self.wt / "state.txt").write_text("good\n", encoding="utf-8")
        (self.wt / ".gitignore").write_text("node_modules\n", encoding="utf-8")
        _git(self.wt, "init", "-q")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "base")
        (self.wt / "node_modules").mkdir()
        (self.wt / "node_modules" / "marker").write_text("dep\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_and_fail_and_base(self):
        from collab.fullrun import CommandRunner
        runner = CommandRunner("sh check.sh && cat node_modules/marker")
        ok = runner.run(self.wt)
        self.assertEqual(ok.result, PASS, ok.output)
        self.assertIn("dep", ok.output, "node_modules 要在副本里能用")
        (self.wt / "state.txt").write_text("bad\n", encoding="utf-8")
        now = runner.run(self.wt)
        self.assertEqual(now.result, FAIL)
        before = runner.base_all(self.wt)
        self.assertEqual(before.result, PASS, "改之前的版本是过的：" + (before.output or ""))

    def test_node_modules_linked_not_copied(self):
        from collab.fullrun import CommandRunner
        v = CommandRunner("test -L node_modules").run(self.wt)
        self.assertEqual(v.result, PASS, "副本里的 node_modules 应该是链接：" + (v.output or ""))

    def test_ai_tools_hidden_but_node_tools_visible(self):
        from collab.fullrun import CommandRunner
        v = CommandRunner("command -v codex || echo NO_CODEX").run(self.wt)
        self.assertIn("NO_CODEX", v.output)
        if shutil.which("npm"):
            v = CommandRunner("command -v npm").run(self.wt)
            self.assertEqual(v.result, PASS, "npm 这类项目工具要找得到")

    def test_timeout(self):
        from collab.fullrun import CommandRunner
        v = CommandRunner("sleep 5", timeout=1).run(self.wt)
        self.assertEqual(v.result, TIMEOUT)


class CmdRunnerFake:
    """假的命令跑法：给定现在和改之前各跑出什么。"""
    command = "pnpm test"

    def __init__(self, now, before=None):
        self.now = list(now)
        self.before = before or Verdict(PASS, 0, "ok")
        self.base_calls = 0

    def applicable(self, worktree):
        return True

    def run(self, worktree, stop=None):
        return self.now.pop(0) if self.now else Verdict(PASS, 0, "all good")

    def base_all(self, worktree, stop=None):
        self.base_calls += 1
        return self.before


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.collab = root / ".collab"
        self.worktree = root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("资料\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, runner):
        calls = {"dev": []}
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "a.test.ts").write_text("// 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            calls["dev"].append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex", full_runner=runner)
        return card, calls

    def fulls(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "全量"]

    def test_pass(self):
        card, calls = self.go(CmdRunnerFake([Verdict(PASS, 0, "ok")]))
        self.assertEqual(card.state, "待验收")
        self.assertTrue(any("pnpm test" in (e.get("summary") or "") for e in self.fulls()), "右栏写明用的哪条命令")

    def test_broken_goes_back_to_dev_with_output(self):
        runner = CmdRunnerFake([Verdict(FAIL, 1, "✖ tests/old.test.ts 计分不对\n# fail 1"), Verdict(PASS, 0, "ok")])
        card, calls = self.go(runner)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(calls["dev"]), 2, "退回写代码的再改一次")
        self.assertIn("计分不对", calls["dev"][1], "交代里附上没过的那段输出")
        self.assertIn("pnpm test", calls["dev"][1])
        self.assertEqual(runner.base_calls, 1)

    def test_broken_before_stops_card(self):
        runner = CmdRunnerFake([Verdict(FAIL, 1, "# fail 1")], before=Verdict(FAIL, 1, "# fail 1"))
        card, calls = self.go(runner)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(calls["dev"]), 1, "改之前就不过不算写代码的错，不退回")
        self.assertIn("本来就不过", self.fulls()[-1]["summary"])
        from desktop.view import card_view
        check = next(s for s in card_view(self.collab.parent, "CARD-001")["stations"] if s["key"] == "check")
        self.assertEqual(check["state"], "fail", "停下的情况右栏不能显示成过了")

    def test_base_not_runnable_stops_card(self):
        runner = CmdRunnerFake([Verdict(FAIL, 1, "# fail 1")], before=Verdict(TIMEOUT, None, ""))
        card, calls = self.go(runner)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(calls["dev"]), 1)

    def test_timeout_stops_card(self):
        card, _ = self.go(CmdRunnerFake([Verdict(TIMEOUT, None, "")]))
        self.assertEqual(card.state, "需Claude介入")

    def test_not_python_no_command_says_no_safety_net(self):
        from collab.fullrun import FullRunner
        card, _ = self.go(FullRunner())
        skipped = [e for e in self.fulls() if e.get("phase") == "跳过"]
        self.assertTrue(skipped)
        self.assertIn("没配全部测试命令", skipped[-1]["summary"])
        self.assertIn("没有兜底", skipped[-1]["summary"])


if __name__ == "__main__":
    unittest.main()


class Round1Fixes(unittest.TestCase):
    """第三批复查第 1 轮 2、3。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        self.wt.mkdir()
        (self.wt / "a.txt").write_text("x\n", encoding="utf-8")
        _git(self.wt, "init", "-q")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pipe_failure_is_failure(self):
        from collab.fullrun import CommandRunner
        self.assertEqual(CommandRunner("false | cat").run(self.wt).result, FAIL, "接了管道也要认出前面没过")

    def test_secrets_not_passed(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(OPENAI_API_KEY="fake1", ANTHROPIC_API_KEY="fake2", GH_TOKEN="fake3", MY_SECRET="fake4")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        for word in ("fake1", "fake2", "fake3", "fake4"):
            self.assertNotIn(word, v.output)


class Round2EnvFix(Round1Fixes):
    def test_db_secrets_not_passed(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(PGPASSWORD="fake5", DATABASE_URL="postgres://u:fake6@h/db", SUPABASE_SERVICE_ROLE_KEY="fake7")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        for word in ("fake5", "fake6", "fake7"):
            self.assertNotIn(word, v.output)
        self.assertIn("HOME=", v.output, "正常变量照带")


class Round3Fixes(Round1Fixes):
    def test_only_safe_env_passed(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(REDIS_URL="redis://:fake8@h", POSTGRES_URL="postgres://u:fake9@h/db", WHATEVER="keep10")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        for word in ("fake8", "fake9"):
            self.assertNotIn(word, v.output, "地址里的账号密码去掉")
        self.assertIn("REDIS_URL=redis://h", v.output)
        self.assertIn("WHATEVER=keep10", v.output, "项目测试自己要的设置照带")
        self.assertIn("HOME=", v.output)

    def test_needed_env_kept_and_own_secrets_dropped(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(PYTHONPATH="/x/src", NODE_OPTIONS="--max-old-space-size=99", HTTPS_PROXY="http://proxy:1",
                          SUPERAGENT_TENCENT_KEY="fake11", COLLAB_REDIS_URL="fake12", FORCE_COLOR="1")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        for need in ("PYTHONPATH=/x/src", "NODE_OPTIONS=--max-old-space-size=99", "HTTPS_PROXY=http://proxy:1"):
            self.assertIn(need, v.output, "测试跑起来要用的设置要带上")
        self.assertNotIn("fake11", v.output, "名字像密钥的不带")
        self.assertIn("COLLAB_REDIS_URL=fake12", v.output, "不是账号密码地址、名字也不像密钥的照带")

    def test_colored_zero_tests_not_pass(self):
        from collab.dispatch import _zero_tests_ran
        from collab.fullrun import zero_tests
        out = "\x1b[34mℹ tests 0\x1b[39m\n\x1b[34mℹ pass 0\x1b[39m\n"
        self.assertTrue(_zero_tests_ran(out))
        self.assertTrue(zero_tests(out))

    def test_node_zero_tests_not_pass(self):
        from collab.dispatch import _zero_tests_ran
        from collab.fullrun import zero_tests
        out = "ℹ tests 0\nℹ suites 0\nℹ pass 0\nℹ fail 0\n"
        self.assertTrue(_zero_tests_ran(out))
        self.assertTrue(zero_tests(out))
        self.assertFalse(_zero_tests_ran("ℹ tests 356\nℹ pass 356\n"))
        self.assertTrue(zero_tests("===== no tests ran in 0.01s ====="))


class SecondConfirmFixes(Round1Fixes):
    def test_proxy_password_dropped_groups_file_kept(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(HTTPS_PROXY="http://bob:fake13@proxy:8080", SUPERAGENT_GROUPS_FILE="/tmp/g.txt")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("fake13", v.output)
        self.assertIn("HTTPS_PROXY=http://proxy:8080", v.output)
        self.assertIn("SUPERAGENT_GROUPS_FILE=/tmp/g.txt", v.output, "停下要靠它找到在跑的测试")


class FinalConfirmEnv(Round1Fixes):
    def test_query_password_and_ssh_user(self):
        from collab.fullrun import CommandRunner
        old = dict(os.environ)
        os.environ.update(DB_LINK="postgres://h/db?user=u&password=fake14", GIT_REMOTE_URL="ssh://git@host/repo")
        try:
            v = CommandRunner("env").run(self.wt)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("fake14", v.output)
        self.assertIn("GIT_REMOTE_URL=ssh://git@host/repo", v.output, "只有用户名的地址不动")
