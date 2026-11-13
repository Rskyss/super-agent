"""流程修理第四轮 任务 7（55）：团队检测改成一件小活（读数加 1、跑 ls 写个数），看用量；工具升级后提醒重新检测。

11-13 千问 4 被拒、DeepSeek 反复压缩，旧检测（建一个文件）都测不出来，开跑后白跑 21 分钟。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collab import check
from collab.agents import AgentResult


def fake_ask(answer=None, output="", ok=True):
    """假工具：读题目、照 answer 写结果文件（answer=None 时照正确答案写）。"""
    seen = {}

    def ask(tool, prompt, folder, expect_file=None, **kw):
        seen["prompt"] = prompt
        n = int((Path(folder) / check.QUESTION_FILE).read_text(encoding="utf-8").strip())
        text = answer if answer is not None else f"{n + 1}\n2\n"
        if text is not False:
            (Path(folder) / expect_file).write_text(text, encoding="utf-8")
        return AgentResult(ok=ok and text is not False, output=output, attempts=1)
    return ask, seen


class SmallJob(unittest.TestCase):
    def run_check(self, ask, version="codex 1.2.3"):
        with patch.object(check, "ask", ask), patch.object(check.shutil, "which", lambda c: "/bin/" + c), \
                patch.object(check, "tool_version", lambda tool: version):
            return check.check_tool("codex", {})

    def test_right_answer_passes(self):
        ask, seen = fake_ask()
        result = self.run_check(ask)
        self.assertTrue(result["ok"], result)
        self.assertIn(check.QUESTION_FILE, seen["prompt"])
        self.assertIn("ls", seen["prompt"])
        self.assertEqual(result["version"], "codex 1.2.3")

    def test_wrong_number_fails(self):
        ask, _ = fake_ask(answer="OK\n")
        result = self.run_check(ask)
        self.assertFalse(result["ok"])
        self.assertIn("读文件", result["reason"])

    def test_missing_ls_line_fails(self):
        ask, _ = fake_ask(answer=None)

        def no_ls(tool, prompt, folder, expect_file=None, **kw):
            n = int((Path(folder) / check.QUESTION_FILE).read_text(encoding="utf-8").strip())
            (Path(folder) / expect_file).write_text(f"{n + 1}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1)
        result = self.run_check(no_ls)
        self.assertFalse(result["ok"])
        self.assertIn("命令", result["reason"])

    def test_big_usage_warns(self):
        ask, _ = fake_ask(output="……\ntokens used\n451,861\n")
        result = self.run_check(ask)
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("warn"), "usage")
        self.assertIn("45 万", result["reason"])

    def test_normal_usage_quiet(self):
        ask, _ = fake_ask(output="tokens used\n60,184\n")
        result = self.run_check(ask)
        self.assertNotIn("warn", result)


class Versions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_remember_and_compare(self):
        check.remember_versions(self.project, [{"tool": "codex", "ok": True, "version": "codex 1.0"},
                                               {"tool": "agy", "ok": False, "version": "agy 2"}])
        saved = json.loads((self.project / ".collab" / check.VERSIONS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved, {"codex": "codex 1.0"}, "没通的不记")
        team = {"dispatcher": "codex", "tester": "codex", "developer": "qwen", "reviewer": "codex"}
        with patch.object(check, "tool_version", lambda tool: "codex 1.0"):
            self.assertEqual(check.version_changes(self.project, team), [])
        with patch.object(check, "tool_version", lambda tool: "codex 1.1"):
            words = check.version_changes(self.project, team)
        self.assertEqual(len(words), 1)
        self.assertIn("Codex", words[0])
        self.assertIn("重新检测", words[0])

    def test_concurrent_saves_keep_both(self):
        import threading
        ts = [threading.Thread(target=check.remember_versions,
                               args=(self.project, [{"tool": t, "ok": True, "version": f"{t} {i}"}]))
              for i in range(20) for t in ("codex", "agy")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        saved = json.loads((self.project / ".collab" / check.VERSIONS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"codex", "agy"})

    def test_qwen_shares_codex_version(self):
        check.remember_versions(self.project, [{"tool": "qwen", "ok": True, "version": "codex 1.0"}])
        team = {"developer": "qwen"}
        with patch.object(check, "tool_version", lambda tool: "codex 1.1"):
            self.assertTrue(check.version_changes(self.project, team))

    def test_unknown_version_is_quiet(self):
        team = {"developer": "codex"}
        with patch.object(check, "tool_version", lambda tool: "codex 9"):
            self.assertEqual(check.version_changes(self.project, team), [], "从没检测过不提醒")
        check.remember_versions(self.project, [{"tool": "codex", "ok": True, "version": "codex 1"}])
        with patch.object(check, "tool_version", lambda tool: ""):
            self.assertEqual(check.version_changes(self.project, team), [], "问不到版本不提醒")


class Shown(unittest.TestCase):
    def test_run_records_upgrade_reminder(self):
        from collab import cli
        self.assertTrue(hasattr(cli, "_remind_upgrades"))

    def test_team_page_gets_upgrades(self):
        text = (Path(__file__).resolve().parents[1] / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertIn("version_changes", text)
        js = (Path(__file__).resolve().parents[1] / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        self.assertIn("upgraded", js)


if __name__ == "__main__":
    unittest.main()
