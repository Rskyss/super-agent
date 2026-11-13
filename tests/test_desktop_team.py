"""团队配置的本机地址与面板：读/存配置、后台检测逐个报结果、面板显示配置里的人。用假工具，不启动真的 AI。"""
import json
import os
import shutil
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

OK_TOOL = "#!/bin/sh\nn=$(cat 题目.txt); printf '%s\\n%s\\n' $((n+1)) $(ls | wc -l) > 连通检测.txt\n"


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
        self.assertIn("models", data)
        self.assertIn("model_options", data)
        team = {"dispatcher": "agy", "tester": "codex", "developer": "cursor", "reviewer": "none"}
        models = {"codex": "gpt-5.6-sol", "codex_effort": "high", "cursor": "gpt-5.6-sol-xhigh", "agy": "claude-opus-5-5-high"}
        self.assertTrue(self._post("/api/team/save", {"project": str(self.project), "team": team, "models": models})["ok"])
        after = self._get("/api/team", project=str(self.project))
        self.assertEqual(after["team"]["dispatcher"], "agy")
        self.assertEqual(after["models"]["codex"], "gpt-5.6-sol")
        self.assertEqual(after["models"]["codex_effort"], "high")
        self.assertEqual(after["models"]["cursor"], "gpt-5.6-sol-xhigh")
        self.assertEqual(after["models"]["agy"], "claude-opus-5-5-high")

    def test_bad_choice_is_refused_in_plain_words(self):
        team = {"dispatcher": "claude", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/team/save", {"project": str(self.project), "team": team})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("调度员", json.loads(ctx.exception.read())["error"])

    def _wait(self, run):
        end = time.time() + 20
        data = {}
        while time.time() < end:
            data = self._get("/api/team/check", project=str(self.project), run=run)
            if data["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(data["done"])
        return {r["role"]: r for r in data["results"]}

    def test_check_runs_in_background_and_reports_each_member(self):
        team = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        run = self._post("/api/team/check", {"project": str(self.project), "team": team})["run"]
        roles = self._wait(run)
        self.assertEqual(set(roles), {"dispatcher", "tester", "developer", "reviewer"})
        self.assertTrue(all(r["ok"] for r in roles.values()))

    def test_checking_one_member_leaves_the_others_alone(self):
        team = {"dispatcher": "codex", "tester": "cursor", "developer": "agy", "reviewer": "none"}
        run = self._post("/api/team/check", {"project": str(self.project), "team": team, "roles": ["tester"]})["run"]
        self.assertEqual(set(self._wait(run)), {"tester"})

    def test_a_member_can_be_checked_before_a_dispatcher_is_chosen(self):
        team = {"dispatcher": "", "tester": "cursor", "developer": "agy", "reviewer": "agy"}
        run = self._post("/api/team/check", {"project": str(self.project), "team": team, "roles": ["tester"]})["run"]
        self.assertTrue(self._wait(run)["tester"]["ok"])

    def test_same_tool_rows_share_one_check(self):
        team = {"dispatcher": "codex", "tester": "codex", "developer": "agy", "reviewer": "agy"}
        run = self._post("/api/team/check", {"project": str(self.project), "team": team,
                                             "roles": ["developer", "reviewer"]})["run"]
        roles = self._wait(run)
        self.assertEqual(set(roles), {"developer", "reviewer"})
        self.assertEqual(roles["reviewer"]["shared_with"], "developer")

    def test_two_checks_at_once_keep_their_own_results(self):
        slow = self.bin / "agy"
        slow.write_text("#!/bin/sh\nsleep 1\nn=$(cat 题目.txt); printf '%s\\n%s\\n' $((n+1)) $(ls | wc -l) > 连通检测.txt\n", encoding="utf-8")
        team = {"dispatcher": "codex", "tester": "cursor", "developer": "agy", "reviewer": "none"}
        first = self._post("/api/team/check", {"project": str(self.project), "team": team, "roles": ["developer"]})["run"]
        second = self._post("/api/team/check", {"project": str(self.project), "team": team, "roles": ["tester"]})["run"]
        self.assertNotEqual(first, second)
        self.assertEqual(set(self._wait(second)), {"tester"})
        self.assertEqual(set(self._wait(first)), {"developer"})

    def test_unknown_check_reports_not_started(self):
        data = self._get("/api/team/check", project=str(self.project), run="没有这一次")
        self.assertFalse(data["started"])
        self.assertTrue(data["done"])


class TeamPageTest(unittest.TestCase):
    static = Path(__file__).resolve().parent.parent / "desktop" / "static"

    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则层检查")
    def test_save_and_same_tool_rules(self):
        script = Path(__file__).resolve().parent / "team_page_check.js"
        proc = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    def test_every_role_has_its_own_button_and_the_shared_one_is_gone(self):
        html = (self.static / "team.html").read_text(encoding="utf-8")
        self.assertNotIn('id="check"', html)
        self.assertEqual(html.count('class="check-one"'), 4)
        self.assertEqual(html.count('class="member"'), 4)


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
