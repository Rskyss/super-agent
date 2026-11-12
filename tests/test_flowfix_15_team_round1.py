"""流程修理：Codex 独立复查第 1 轮（团队配置）查出的问题，逐条复现后补的测试。"""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TEAM = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "codex"}


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class DraftRules(unittest.TestCase):
    def test_node(self):
        out = subprocess.run(["node", str(ROOT / "tests" / "flowfix_team_draft_check.js")], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_page_pieces(self):
        js = (ROOT / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        self.assertIn("/api/team/draft", js, "暂存放在项目里，重开应用换了端口也在")
        self.assertIn("上次改过还没检测", js, "恢复的行要重新检测才能保存")
        self.assertIn("var started = Date.now()", js.split("function check(")[1].split("function reset")[0],
                      "从点“检测”那一刻就开始计时，启动请求卡住也会给结果")


class Server(unittest.TestCase):
    def setUp(self):
        from desktop.server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "项目"
        (self.project / ".collab" / "cards").mkdir(parents=True)
        (self.project / ".git").mkdir()
        (self.project / ".collab" / "配置.json").write_text(json.dumps({"team": TEAM}), encoding="utf-8")
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
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_draft_kept_in_project(self):
        draft = {"base": {"team": TEAM}, "now": {"team": dict(TEAM, developer="agy")}}
        self.assertEqual(self.post("/api/team/draft", {"project": str(self.project), "draft": draft})[0], 200)
        self.assertEqual(self.get("/api/team/draft")["draft"], draft)
        self.assertEqual(self.post("/api/team/draft", {"project": str(self.project), "draft": None})[0], 200)
        self.assertIsNone(self.get("/api/team/draft")["draft"])

    def test_failed_save_changes_nothing(self):
        """7：保存失败（强度写错）时，团队也不能被改掉一半"""
        bad = {"tester": {"tool": "codex", "model": "m", "effort": "very-high"}}
        status, _ = self.post("/api/team/save", {"project": str(self.project), "team": dict(TEAM, developer="agy"),
                                                 "role_models": bad})
        self.assertEqual(status, 400)
        data = json.loads((self.project / ".collab" / "配置.json").read_text(encoding="utf-8"))
        self.assertEqual(data["team"]["developer"], "cursor")


class CheckUsesGivenModels(unittest.TestCase):
    def test_explicit_models_not_overridden_by_saved(self):
        """6：只传整家模型（旧接口），不能被自动迁移出来的岗位设置盖掉"""
        from collab import check
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / ".collab").mkdir()
            (p / ".collab" / "配置.json").write_text(json.dumps(
                {"team": TEAM, "codex": "saved-model", "codex_effort": "high"}), encoding="utf-8")
            seen = []

            def fake(tool, models, timeout=None):
                seen.append((models.get("codex"), models.get("codex_effort")))
                return {"ok": True, "status": "ok", "reason": "通了", "how": "", "seconds": 0}
            with mock.patch.object(check, "check_tool", side_effect=fake):
                check.check_team(p, team=TEAM, roles=["tester"], models={"codex": "unsaved-model", "codex_effort": "low"})
        self.assertEqual(seen, [("unsaved-model", "low")])


if __name__ == "__main__":
    unittest.main()
