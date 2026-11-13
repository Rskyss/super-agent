"""检测连通：每人真干一件小事（在临时文件夹里建一个文件），建出来才算通。用“假工具”测，不启动任何真的 AI。"""
import io
import json
import os
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collab.check import check_team
from collab.cli import main
from collab.config import save_team
from collab.workspace import init_collab

# 假工具：按环境变量 FAKE_<名字> 决定表现。ok=建文件；login/quota/model=报错；nofile=不建文件；sleep=一直不结束
FAKE = """#!/bin/sh
mode=$(printenv FAKE_{name})
case "$mode" in
  ok) n=$(cat 题目.txt); printf "%s\n%s\n" $((n+1)) $(ls | wc -l) > 连通检测.txt; echo done ;;
  login) echo "Error: Not logged in. Please run login first." ; exit 1 ;;
  quota) echo "Error: usage limit reached (429)" ; exit 1 ;;
  model) echo "Error: model 'gpt-9' not found" ; exit 1 ;;
  nofile) echo "我觉得已经建好了" ;;
  sleep) sleep 30 ;;
esac
"""


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.project = root / "项目"
        self.project.mkdir()
        init_collab(self.project)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self._keys = []

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        for k in self._keys:
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def tool(self, command: str, mode: str):
        """放一个假工具到 PATH 最前面。command：codex / agent（Cursor）/ agy。"""
        name = command.upper()
        path = self.bin / command
        path.write_text(FAKE.replace("{name}", name), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        os.environ[f"FAKE_{name}"] = mode
        self._keys.append(f"FAKE_{name}")

    def team(self, **kw):
        t = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        t.update(kw)
        save_team(self.project, t)

    def by_role(self, results):
        return {r["role"]: r for r in results}

    def test_everyone_connected(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertTrue(all(x["ok"] for x in r.values()), r)
        self.assertEqual(r["developer"]["tool"], "cursor")
        self.assertIn("seconds", r["tester"])

    def test_same_tool_in_two_roles_is_checked_once(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertEqual(r["tester"]["shared_with"], "dispatcher")  # Codex 当调度员和写测试，只测一次
        self.assertIsNone(r["dispatcher"]["shared_with"])

    def test_missing_tool_is_reported_without_waiting(self):
        self.tool("codex", "ok")
        self.tool("agent", "ok")  # 没放 agy
        self.team()
        start = time.time()
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertFalse(r["reviewer"]["ok"])
        self.assertEqual(r["reviewer"]["status"], "missing")
        self.assertIn("没装", r["reviewer"]["reason"])
        self.assertLess(time.time() - start, 8)

    def test_reasons_are_in_plain_words(self):
        self.tool("codex", "login")
        self.tool("agent", "quota")
        self.tool("agy", "model")
        self.team()
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertIn("没登录", r["tester"]["reason"])
        self.assertIn("codex", r["tester"]["how"])
        self.assertIn("额度", r["developer"]["reason"])
        self.assertIn("模型", r["reviewer"]["reason"])

    def test_claiming_success_without_the_file_is_not_connected(self):
        for c in ("codex", "agy"):
            self.tool(c, "ok")
        self.tool("agent", "nofile")
        self.team()
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertFalse(r["developer"]["ok"])
        self.assertIn("没建出文件", r["developer"]["reason"])

    def test_stuck_tool_times_out(self):
        for c in ("codex", "agent"):
            self.tool(c, "ok")
        self.tool("agy", "sleep")
        self.team()
        start = time.time()
        r = self.by_role(check_team(self.project, timeout=3))
        self.assertFalse(r["reviewer"]["ok"])
        self.assertIn("超时", r["reviewer"]["reason"])
        self.assertLess(time.time() - start, 15)

    def test_no_reviewer_is_skipped(self):
        for c in ("codex", "agent"):
            self.tool(c, "ok")
        self.team(reviewer="none")
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertEqual(r["reviewer"]["status"], "skipped")
        self.assertTrue(r["reviewer"]["ok"])

    def test_custom_dispatcher_is_only_checked_for_being_installed(self):
        self.tool("codex", "ok")
        self.tool("agent", "ok")
        self.tool("agy", "ok")
        (self.bin / "qwen").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.bin / "qwen").chmod(0o755)
        self.team(dispatcher="custom:qwen --yolo")
        r = self.by_role(check_team(self.project, timeout=10))
        self.assertTrue(r["dispatcher"]["ok"])
        self.assertIn("只能检查装没装", r["dispatcher"]["reason"])

    def test_results_arrive_one_by_one(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        seen = []
        check_team(self.project, timeout=10, on_result=seen.append)
        self.assertEqual(sorted(r["role"] for r in seen), ["developer", "dispatcher", "reviewer", "tester"])

    def test_retry_checks_only_the_chosen_roles(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        r = check_team(self.project, timeout=10, roles=["reviewer"])
        self.assertEqual([x["role"] for x in r], ["reviewer"])

    def test_temporary_folders_are_cleaned_up(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        before = set(Path(tempfile.gettempdir()).glob("超级助手检测-*"))
        check_team(self.project, timeout=10)
        after = set(Path(tempfile.gettempdir()).glob("超级助手检测-*"))
        self.assertEqual(after - before, set())

    def test_cli_check_command(self):
        for c in ("codex", "agent", "agy"):
            self.tool(c, "ok")
        self.team()
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = main(["check", str(self.project), "--json", "--timeout", "10"])
        self.assertEqual(code, 0, buf.getvalue())
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data), 4)


if __name__ == "__main__":
    unittest.main()
