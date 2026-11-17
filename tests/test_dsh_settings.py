"""设置整页（第六批）：能力层、积木库从新版自己的积木库资料夹读真实数据。

用户 11-16 晚：积木库和能力层资料清空，由用户自己添加——所以列表时**不许偷偷补预置能力卡**，
也不许读旧版积木库。"""
import json, os, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock

from collab import abilities, blocks
from desktop import dsh_settings
from desktop.chat import ChatHub
from desktop import server as web


def text_card(**over):
    card = {
        "id": "write-demo", "title": "写短文", "description": "按主题写一篇短文",
        "block": "bailian", "model": "qwen-plus",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "title": "主题", "maxLength": 200},
            "words": {"type": "integer", "title": "字数", "minimum": 10, "maximum": 5000, "default": 100}},
            "required": ["topic"]},
        "how": {"kind": "llm_api", "system": "只写正文。", "prompt": "写{topic}，约{words}字。"},
        "output": {"type": "text", "check": {"min_ratio": 0.5, "max_ratio": 2.0, "of": "words"}},
    }
    card.update(over)
    return card


class RowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "blocks"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_home_lists_nothing_and_does_not_seed_builtin(self):
        self.assertEqual(dsh_settings.ability_rows(self.home), [])
        self.assertFalse((self.home / abilities.ABILITY_DIR).exists(), "列表时不能偷偷补预置能力卡")
        self.assertEqual(dsh_settings.block_rows(self.home), [])

    def test_ability_row_is_plain_language(self):
        abilities.save_ability(text_card(), self.home)
        rows = dsh_settings.ability_rows(self.home)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "写短文")
        self.assertEqual(row["inputs"], "主题 + 字数")
        self.assertEqual(row["output"], "一段文字")
        self.assertIn("qwen-plus", row["who"])
        self.assertEqual(row["check"], "字数在要求的 0.5–2 倍之间")
        self.assertEqual(row["runs"], 0)
        self.assertTrue(row["draft"], "没跑过的卡是草稿")

    def test_runs_counted(self):
        abilities.save_ability(text_card(), self.home)
        abilities.add_run("write-demo", {"type": "run", "run": abilities.new_run_id(), "ok": True}, self.home)
        row = dsh_settings.ability_rows(self.home)[0]
        self.assertEqual(row["runs"], 1)
        self.assertFalse(row["draft"])

    def test_broken_card_is_reported_not_hidden(self):
        folder = self.home / abilities.ABILITY_DIR
        folder.mkdir()
        (folder / "bad.json").write_text("{坏", encoding="utf-8")
        rows = dsh_settings.ability_rows(self.home)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["problem"])

    def test_check_text_variants(self):
        f = dsh_settings.check_text
        self.assertEqual(f({"type": "text", "check": {"min_chars": 100, "max_chars": 300}}), "字数 100–300")
        self.assertEqual(f({"type": "text", "check": {"max_chars": 300}}), "字数不超过 300")
        self.assertEqual(f({"type": "list", "check": {"min_items": 3}}), "至少 3 条")
        self.assertEqual(f({"type": "text", "check": {}}), "—")


class TeamTest(unittest.TestCase):
    def test_team_rows_read_real_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / ".collab").mkdir()
            (project / ".collab" / "配置.json").write_text(json.dumps(
                {"team": {"dispatcher": "codex", "tester": "codex", "developer": "agy", "reviewer": "codex"}}),
                encoding="utf-8")
            info = dsh_settings.team_rows(project)
            self.assertEqual(info["dispatcher"], "Codex")
            self.assertTrue(info["roles"]["tester"].startswith("Codex · "))
            self.assertTrue(info["roles"]["developer"].startswith("agy · "))
            self.assertEqual(info["problem"], "")


class TeamPickTest(unittest.TestCase):
    """用户 11-16 晚：业务流程每步“谁来跑”要像旧版团队设置一样能选（过渡用）。"""

    def _project(self, folder, team=None):
        project = Path(folder)
        (project / ".collab" / "cards").mkdir(parents=True)
        if team:
            (project / ".collab" / "配置.json").write_text(json.dumps({"team": team}), encoding="utf-8")
        return project

    def test_rows_carry_current_picks_and_options(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self._project(folder, {"tester": "codex", "developer": "agy", "reviewer": "none"})
            info = dsh_settings.team_rows(project)
            self.assertEqual(info["picks"]["tester"]["tool"], "codex")
            self.assertIn(info["picks"]["tester"]["effort"], ("low", "medium", "high", "xhigh", "max", "ultra"))
            self.assertEqual(info["picks"]["developer"]["tool"], "agy")
            self.assertEqual(info["picks"]["reviewer"]["tool"], "none")
            self.assertIn("none", [o["value"] for o in info["options"]["reviewer"]])
            self.assertNotIn("none", [o["value"] for o in info["options"]["tester"]])
            self.assertTrue(info["models"]["codex"])
            self.assertTrue(info["efforts"])

    def test_save_one_role_keeps_others(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self._project(folder, {"dispatcher": "codex", "tester": "codex", "developer": "agy", "reviewer": "codex"})
            dsh_settings.save_role(project, "developer", "codex", "gpt-6.1-sol", "high")
            info = dsh_settings.team_rows(project)
            self.assertEqual(info["picks"]["developer"], {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"})
            self.assertEqual(info["picks"]["tester"]["tool"], "codex")
            self.assertEqual(info["dispatcher"], "Codex")

    def test_save_works_without_dispatcher(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self._project(folder)
            dsh_settings.save_role(project, "reviewer", "none", "", "")
            self.assertEqual(dsh_settings.team_rows(project)["picks"]["reviewer"]["tool"], "none")

    def test_bad_input_saves_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self._project(folder, {"tester": "codex"})
            before = (project / ".collab" / "配置.json").read_text(encoding="utf-8")
            for args in (("dispatcher", "codex", "m", ""), ("tester", "none", "", ""), ("tester", "codex", "gpt", "bogus"),
                         ("tester", "codex", "", "high"), ("tester", "rm -rf", "x", "")):
                with self.assertRaises(ValueError):
                    dsh_settings.save_role(project, *args)
            self.assertEqual((project / ".collab" / "配置.json").read_text(encoding="utf-8"), before)


class HttpTest(unittest.TestCase):
    def _serve(self):
        srv = web.make_server(port=0, token="test", hub=ChatHub(factories={}))
        srv.terminal_path = lambda: ""
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv

    def _call(self, srv, path, body=None):
        url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Chat-Token": "test"},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_lists_from_isolated_home_only(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "blocks"
            home.mkdir()
            abilities.save_ability(text_card(), home)
            srv = self._serve()
            with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(home)}):
                code, body = self._call(srv, "/api/dsh/settings/abilities")
                self.assertEqual(code, 200)
                self.assertEqual([r["title"] for r in body["abilities"]], ["写短文"])
                code, body = self._call(srv, "/api/dsh/settings/blocks")
                self.assertEqual(code, 200)
                self.assertEqual(body["blocks"], [])

    def test_refuses_old_default_home(self):
        srv = self._serve()
        with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(blocks.DEFAULT_ROOT)}):
            code, body = self._call(srv, "/api/dsh/settings/abilities")
        self.assertEqual(code, 400)
        self.assertIn("原有", body["error"])

    def test_save_role_refused_while_card_running(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "blocks"
            home.mkdir()
            project = Path(folder) / "project"
            (project / ".collab" / "cards").mkdir(parents=True)
            (project / ".git").mkdir()
            srv = self._serve()
            body = {"project": str(project), "role": "tester", "tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}
            with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(home)}), \
                    mock.patch.object(web.clerk_tasks, "running", return_value={"card": "CARD-001"}):
                code, reply = self._call(srv, "/api/dsh/settings/team/save", body)
            self.assertEqual(code, 409)
            self.assertIn("正在跑", reply["error"])
            with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(home)}), \
                    mock.patch.object(web.clerk_tasks, "running", return_value=None):
                code, reply = self._call(srv, "/api/dsh/settings/team/save", body)
            self.assertEqual(code, 200, reply)
            self.assertEqual(dsh_settings.team_rows(project)["picks"]["tester"]["effort"], "high")

    def test_save_role_on_fresh_git_project_without_cards(self):
        """用户刚清空数据、还没建过任务卡：项目里没有 .collab，也要能先选好谁来跑。"""
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "blocks"
            home.mkdir()
            project = Path(folder) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            plain = Path(folder) / "plain"
            plain.mkdir()
            srv = self._serve()
            body = {"role": "developer", "tool": "agy", "model": "gemini-4-hydrogen-high", "effort": ""}
            with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(home)}), \
                    mock.patch.object(web.clerk_tasks, "running", return_value=None):
                code, reply = self._call(srv, "/api/dsh/settings/team/save", {"project": str(project), **body})
                self.assertEqual(code, 200, reply)
                self.assertEqual(dsh_settings.team_rows(project)["picks"]["developer"]["model"], "gemini-4-hydrogen-high")
                code, reply = self._call(srv, "/api/dsh/settings/team/save", {"project": str(plain), **body})
            self.assertEqual(code, 400)
            self.assertIn("git", reply["error"])
            self.assertFalse((plain / ".collab").exists())

    def test_recheck_dispatcher_forces_real_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "blocks"
            home.mkdir()
            project = Path(folder) / "project"
            project.mkdir()
            srv = self._serve()
            seen = {}

            def detect(p, h, force=False):
                seen.update(project=p, force=force)
                return {"status": "ready", "tool": "codex", "model": "m", "reason": "", "verified": True}

            with mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(home)}), \
                    mock.patch.object(srv.dispatcher_detector, "detect", side_effect=detect):
                code, body = self._call(srv, "/api/dsh/settings/recheck-dispatcher", {"project": str(project)})
            self.assertEqual(code, 200)
            self.assertEqual(body["status"], "ready")
            self.assertTrue(seen["force"])
            self.assertEqual(seen["project"], str(project))


if __name__ == "__main__":
    unittest.main()


class ReviewFixTest(unittest.TestCase):
    """复查第 1 轮中等 8、轻微 19。"""

    def test_two_roles_saved_at_once_both_stick(self):
        import threading
        from collab import config as cfg
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / ".collab").mkdir()
            real = cfg._read
            gate = threading.Barrier(2, timeout=2)

            def slow_read(p):
                data = real(p)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                return data

            with mock.patch.object(cfg, "_read", side_effect=slow_read):
                a = threading.Thread(target=dsh_settings.save_role, args=(project, "tester", "codex", "gpt-6.1-sol", "high"))
                b = threading.Thread(target=dsh_settings.save_role, args=(project, "developer", "agy", "gemini-4-hydrogen-high", ""))
                a.start(); b.start(); a.join(10); b.join(10)
            picks = dsh_settings.team_rows(project)["picks"]
            self.assertEqual(picks["tester"]["effort"], "high")
            self.assertEqual(picks["developer"]["model"], "gemini-4-hydrogen-high")

    def test_symlink_inside_new_library_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "old"
            (old / abilities.ABILITY_DIR).mkdir(parents=True)
            abilities.save_ability(text_card(), old)
            home = Path(folder) / "new"
            home.mkdir()
            (home / abilities.ABILITY_DIR).symlink_to(old / abilities.ABILITY_DIR)
            with self.assertRaises(ValueError):
                dsh_settings.ability_rows(home)
            (home / blocks.CARD_DIR).symlink_to(old)
            with self.assertRaises(ValueError):
                dsh_settings.block_rows(home)


class SymlinkRound2Test(unittest.TestCase):
    """复查第 2 轮轻微 5：单张卡软链、记录目录软链也不读库外资料。"""

    def _old_and_new(self, folder):
        old = Path(folder) / "old"
        (old / abilities.ABILITY_DIR).mkdir(parents=True)
        abilities.save_ability(text_card(), old)
        new = Path(folder) / "new"
        (new / abilities.ABILITY_DIR).mkdir(parents=True)
        (new / blocks.CARD_DIR).mkdir(parents=True)
        return old, new

    def test_single_card_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = self._old_and_new(folder)
            (new / abilities.ABILITY_DIR / "write-demo.json").symlink_to(old / abilities.ABILITY_DIR / "write-demo.json")
            with self.assertRaises(ValueError):
                dsh_settings.ability_rows(new)
            with self.assertRaises(ValueError):
                dsh_settings.block_rows(new)

    def test_records_dir_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = self._old_and_new(folder)
            (old / "记录").mkdir()
            (new / "记录").symlink_to(old / "记录")
            with self.assertRaises(ValueError):
                dsh_settings.ability_rows(new)
