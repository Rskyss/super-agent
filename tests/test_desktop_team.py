"""团队配置的本机地址与面板：读/存配置、后台检测逐个报结果、面板显示配置里的人。用假工具，不启动真的 AI。"""
import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from collab.config import save_team
from collab.workspace import init_collab
from desktop.view import card_view

OK_TOOL = "#!/bin/sh\nprintf OK > 连通检测.txt\n"


class TeamServerTest(unittest.TestCase):
    def setUp(self):
        from desktop.server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        init_collab(self.project)
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in ("codex", "agent", "agy"):
            p = self.bin / name
            p.write_text(OK_TOOL, encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.server = make_server(port=0, token="t1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        os.environ["PATH"] = self._path
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post(self, route, body, token="t1"):
        req = urllib.request.Request(self.base + route, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "X-Chat-Token": token})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def _get(self, route, **q):
        q.setdefault("token", "t1")
        return json.loads(urllib.request.urlopen(self.base + route + "?" + urllib.parse.urlencode(q), timeout=10).read())

    def test_team_needs_the_password(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/team", project=str(self.project), token="错的")
        self.assertEqual(ctx.exception.code, 403)

    def test_read_defaults_then_save_and_read_back(self):
        data = self._get("/api/team", project=str(self.project))
        self.assertEqual(data["team"]["developer"], "cursor")
        self.assertIsNone(data["team"]["dispatcher"])
        self.assertTrue(data["git"])
        self.assertEqual([o["value"] for o in data["options"]["dispatcher"]], ["codex", "cursor", "agy"])
        team = {"dispatcher": "agy", "tester": "codex", "developer": "cursor", "reviewer": "none"}
        self.assertTrue(self._post("/api/team/save", {"project": str(self.project), "team": team})["ok"])
        self.assertEqual(self._get("/api/team", project=str(self.project))["team"]["dispatcher"], "agy")

    def test_bad_choice_is_refused_in_plain_words(self):
        team = {"dispatcher": "claude", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/team/save", {"project": str(self.project), "team": team})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("调度员", json.loads(ctx.exception.read())["error"])

    def test_check_runs_in_background_and_reports_each_member(self):
        team = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        self.assertTrue(self._post("/api/team/check", {"project": str(self.project), "team": team})["ok"])
        end = time.time() + 20
        data = {}
        while time.time() < end:
            data = self._get("/api/team/check", project=str(self.project))
            if data["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(data["done"])
        roles = {r["role"]: r for r in data["results"]}
        self.assertEqual(set(roles), {"dispatcher", "tester", "developer", "reviewer"})
        self.assertTrue(all(r["ok"] for r in roles.values()))


class PanelShowsTeamTest(unittest.TestCase):
    def test_panel_names_come_from_the_team_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            init_collab(project)
            save_team(project, {"dispatcher": "agy", "tester": "cursor", "developer": "codex", "reviewer": "none"})
            stations = {s["key"]: s for s in card_view(project)["stations"]}
            self.assertEqual(stations["disp"]["who"], "agy")
            self.assertEqual(stations["test"]["who"], "Cursor")
            self.assertEqual(stations["dev"]["who"], "Codex")
            self.assertEqual(stations["rev"]["who"], "不复查")


if __name__ == "__main__":
    unittest.main()
