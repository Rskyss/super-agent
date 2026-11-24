"""团队页工具升级提示自动消除与一键重新检测受影响岗位。"""
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from collab import check
from collab.config import save_team
from collab.workspace import init_collab

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "desktop" / "static"

FAKE_CODEX = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "codex-cli 0.171.0"
  exit 0
fi
n=$(cat 题目.txt)
printf '%s\\n%s\\n' $((n+1)) $(ls | wc -l) > 连通检测.txt
"""

FAKE_AGY = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "1.5.14"
  exit 0
fi
n=$(cat 题目.txt)
printf '%s\\n%s\\n' $((n+1)) $(ls | wc -l) > 连通检测.txt
"""


class UpgradeRulesTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则检查")
    def test_node_upgrade_recheck_rules(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "team_upgrade_recheck_check.js")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_static_markup_and_theme_variables(self):
        html = (STATIC / "team.html").read_text(encoding="utf-8")
        self.assertIn('id="upgradeBanner"', html)
        self.assertIn('id="recheckUpgraded"', html)
        self.assertNotIn('id="check"', html)

        css = (STATIC / "team.css").read_text(encoding="utf-8")
        upgrade_rules = [ln for ln in css.splitlines() if ".upgrade-" in ln]
        self.assertTrue(upgrade_rules)
        for ln in upgrade_rules:
            self.assertNotRegex(ln, r"#[0-9a-fA-F]{3,8}\b", f"不应写死色值：{ln}")


class VersionChangesDetailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab").mkdir()
        check.remember_versions(self.project, [{"tool": "codex", "ok": True, "version": "codex-cli 0.167.0"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_structured_items_and_mentions_tencent_when_used(self):
        with patch.object(check, "tool_version", lambda tool: "codex-cli 0.171.0"):
            details = check.version_changes_detail(
                self.project, {"tester": "agy", "developer": "qwen", "reviewer": "codex"}
            )
            self.assertEqual(len(details), 1)
            item = details[0]
            self.assertEqual(item["command"], "codex")
            self.assertEqual(item["old"], "codex-cli 0.167.0")
            self.assertEqual(item["now"], "codex-cli 0.171.0")
            self.assertEqual(set(item["tools"]), {"codex", "qwen", "tencent"})
            self.assertEqual(item["name"], "Codex（千问也用它的壳）")
            self.assertEqual(
                check.version_changes(self.project, {"tester": "agy", "developer": "qwen", "reviewer": "codex"}),
                [item["message"]],
            )

            both = check.version_changes_detail(
                self.project, {"tester": "tencent", "developer": "qwen", "reviewer": "codex"}
            )
            self.assertEqual(both[0]["name"], "Codex（千问、腾讯云也用它的壳）")

            only_tencent = check.version_changes_detail(
                self.project, {"tester": "agy", "developer": "tencent", "reviewer": "codex"}
            )
            self.assertEqual(only_tencent[0]["name"], "Codex（腾讯云也用它的壳）")


class UpgradeServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        from desktop.server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "项目"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        init_collab(self.project)
        save_team(self.project, {"tester": "agy", "developer": "qwen", "reviewer": "codex"})
        check.remember_versions(self.project, [
            {"tool": "agy", "ok": True, "version": "1.5.14"},
            {"tool": "codex", "ok": True, "version": "codex-cli 0.167.0"},
        ])

        self.qwen_key = root / "qwen.key"
        self.qwen_key.write_text("sk-fake-qwen\n", encoding="utf-8")
        self._old_qwen_env = os.environ.get("SUPERAGENT_QWEN_KEY_FILE")
        os.environ["SUPERAGENT_QWEN_KEY_FILE"] = str(self.qwen_key)

        self.bin = root / "bin"
        self.bin.mkdir()
        for name, script in (("codex", FAKE_CODEX), ("agy", FAKE_AGY)):
            p = self.bin / name
            p.write_text(script, encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"

        self.server = make_server(port=0, token="t1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        if self._old_qwen_env is None:
            os.environ.pop("SUPERAGENT_QWEN_KEY_FILE", None)
        else:
            os.environ["SUPERAGENT_QWEN_KEY_FILE"] = self._old_qwen_env
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, route, **q):
        q.setdefault("token", "t1")
        url = self.base + route + "?" + urllib.parse.urlencode(q)
        return json.loads(urllib.request.urlopen(url, timeout=10).read())

    def _post(self, route, body):
        req = urllib.request.Request(
            self.base + route,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-Chat-Token": "t1"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def test_recheck_affected_roles_clears_upgrade_notice(self):
        initial = self._get("/api/team", project=str(self.project))
        self.assertEqual(len(initial["upgraded"]), 1)
        self.assertEqual(len(initial["upgraded_items"]), 1)
        item = initial["upgraded_items"][0]
        self.assertEqual(item["command"], "codex")
        self.assertEqual(item["old"], "codex-cli 0.167.0")
        self.assertEqual(item["now"], "codex-cli 0.171.0")
        self.assertEqual(set(item["tools"]), {"codex", "qwen", "tencent"})

        run = self._post(
            "/api/team/check",
            {
                "project": str(self.project),
                "team": initial["team"],
                "models": initial["models"],
                "role_models": initial["role_models"],
                "roles": ["developer", "reviewer"],
            },
        )["run"]
        deadline = time.time() + 20
        status = {}
        while time.time() < deadline:
            status = self._get("/api/team/check", project=str(self.project), run=run)
            if status["done"]:
                break
            time.sleep(0.1)
        self.assertTrue(status.get("done"))
        by_role = {r["role"]: r for r in status["results"]}
        self.assertEqual(set(by_role), {"developer", "reviewer"})
        self.assertTrue(all(r["ok"] for r in by_role.values()), by_role)

        after = self._get("/api/team", project=str(self.project))
        self.assertEqual(after["upgraded"], [])
        self.assertEqual(after["upgraded_items"], [])


if __name__ == "__main__":
    unittest.main()
