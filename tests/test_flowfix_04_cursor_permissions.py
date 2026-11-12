"""流程修理第 7 条（试跑记录 ⑯⑰）：写代码的 Cursor 能跑本卡测试命令，不许用浏览器等插件。

68b 里 Cursor 跑不了测试，就写调试脚本、写模拟网页、开浏览器卡死 20 多分钟；派给它的活还带着用户自己的全部插件。
2026-11-12 Claude 实测：施工副本里写 .cursor/cli.json 放行 Shell(python3) 后，Cursor 能跑 python3，node 和浏览器仍被拒；不写就全被拒。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _programs_in, cursor_permissions, run_card
from collab.judge import PASS, Verdict
from collab.scope import is_cache, out_of_scope
from collab.state import Card

CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 -m unittest tests.test_x"]


class Programs(unittest.TestCase):
    def test_programs_found_in_test_command(self):
        self.assertEqual(_programs_in("python3 -m unittest tests.a"), ["python3"])
        self.assertEqual(_programs_in("cd web && npm test; /usr/bin/python3 -m x"), ["npm", "python3"])
        self.assertEqual(_programs_in("PYTHONDONTWRITEBYTECODE=1 python3 -m unittest"), ["python3"])
        self.assertEqual(_programs_in(""), [])


class PermissionFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        return json.loads((self.wt / ".cursor" / "cli.json").read_text(encoding="utf-8"))["permissions"]

    def test_allows_test_program_and_denies_plugins(self):
        cursor_permissions(self.wt, CMD)
        perms = self.read()
        self.assertEqual(perms["allow"], ["Shell(python3)"])
        self.assertIn("Mcp(*:*)", perms["deny"])

    def test_keeps_project_own_settings(self):
        (self.wt / ".cursor").mkdir()
        (self.wt / ".cursor" / "cli.json").write_text(json.dumps({"permissions": {"allow": ["Shell(ls)"]}, "x": 1}),
                                                       encoding="utf-8")
        cursor_permissions(self.wt, CMD)
        cursor_permissions(self.wt, CMD)
        perms = self.read()
        self.assertEqual(perms["allow"], ["Shell(ls)", "Shell(python3)"], "不重复、不丢项目自己的")

    def test_not_counted_as_a_change(self):
        self.assertTrue(is_cache(".cursor/cli.json"))
        self.assertEqual(out_of_scope([".cursor/cli.json", "a.py"], ["b.py"]), ["a.py"])


class OnlyForCursor(unittest.TestCase):
    def test_written_when_cursor_is_dispatched_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wt = root / "wt"
            wt.mkdir()
            cdir = root / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            seen = {}

            def ask_fn(adapter, prompt, cwd, **kw):
                seen[adapter] = (Path(cwd) / ".cursor" / "cli.json").is_file()
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过" if "只读复查" in prompt else "好了")
            run_card(Card("CARD-001", state="待派发"), root / ".collab", wt, CMD, "cursor", "codex", ask_fn=ask_fn,
                     judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex")
            self.assertTrue(seen["cursor"])


if __name__ == "__main__":
    unittest.main()
