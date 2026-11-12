"""流程修理第 14–16 条（试跑记录 ①②③④）：团队页按岗位选模型、存不了要说、检测到时间给结果、没保存不丢。"""
import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageRules(unittest.TestCase):
    def test_node_checks(self):
        out = subprocess.run(["node", str(ROOT / "tests" / "flowfix_team_check.js")], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_page_has_the_new_pieces(self):
        js = (ROOT / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        for words in ("role_models: rolePicks()", "团队的改动还没存", "上次改了还没保存，已帮你恢复", "还没有结果，算没通"):
            self.assertIn(words, js)


class Server(unittest.TestCase):
    def setUp(self):
        from desktop.server import make_server
        import threading
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "项目"
        (self.project / ".collab" / "cards").mkdir(parents=True)
        (self.project / ".git").mkdir()
        self.server = make_server(0, token="t")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        from urllib.parse import quote
        with urllib.request.urlopen(f"{self.base}{path}?project={quote(str(self.project))}&token=t") as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Chat-Token": "t"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_role_models_round_trip(self):
        team = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "codex"}
        picks = {"dispatcher": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
                 "tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"},
                 "developer": {"tool": "cursor", "model": "auto"},
                 "reviewer": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}}
        self.assertTrue(self.post("/api/team/save", {"project": str(self.project), "team": team, "role_models": picks})["ok"])
        got = self.get("/api/team")
        self.assertEqual(got["role_models"]["dispatcher"]["effort"], "medium")
        self.assertEqual(got["role_models"]["reviewer"]["effort"], "xhigh")
        self.assertEqual(got["check_timeout"], 120)


class CheckAlwaysEnds(unittest.TestCase):
    def test_tool_that_ignores_stop_still_gives_a_result(self):
        """③ 一家工具卡住、连“停下”都不理：检测也要在时限内给出“没通（超时）”"""
        from collab import check
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            tool = bin_dir / "agy"
            tool.write_text("#!/bin/sh\ntrap '' TERM\nwhile true; do sleep 1; done\n", encoding="utf-8")
            tool.chmod(0o755)
            import os
            from unittest import mock
            with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
                import time
                start = time.time()
                out = check.check_tool("agy", {"agy": "x", "codex": "x", "codex_effort": "high", "cursor": "auto"}, timeout=2)
                spent = time.time() - start
        self.assertFalse(out["ok"])
        self.assertIn("超时", out["reason"])
        self.assertLess(spent, 20)


if __name__ == "__main__":
    unittest.main()
