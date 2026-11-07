"""31 号单：行政的服务商和模型每个项目各一份，项目文件里没有密钥。不花额度。"""
import json
import tempfile
from pathlib import Path

from acceptance_helpers import SWIFT, ServerCase
from collab.config import ConfigError, load_clerk, save_clerk, save_team
from fake_model_server import FakeModel


class SettingTest(ServerCase):
    def config(self):
        return json.loads((Path(self.project) / ".collab" / "配置.json").read_text(encoding="utf-8"))

    def test_nothing_saved_yet(self):
        self.assertIsNone(load_clerk(self.project))

    def test_saved_per_project(self):
        other = tempfile.mkdtemp()
        save_clerk(self.project, {"provider": "bailian", "model": "qwen-flash"})
        self.assertEqual(load_clerk(self.project)["provider"], "bailian")
        self.assertEqual(load_clerk(self.project)["model"], "qwen-flash")
        self.assertIsNone(load_clerk(other))

    def test_known_providers_always_use_their_own_address(self):
        save_clerk(self.project, {"provider": "bailian", "model": "m", "base_url": "https://别处.example.com/v1"})
        self.assertEqual(load_clerk(self.project)["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_other_needs_a_real_address(self):
        save_clerk(self.project, {"provider": "other", "model": "m", "base_url": "https://api.example.com/v1"})
        self.assertEqual(load_clerk(self.project)["base_url"], "https://api.example.com/v1")
        for bad in ("", "不是地址", "ftp://x"):
            with self.assertRaises(ConfigError):
                save_clerk(self.project, {"provider": "other", "model": "m", "base_url": bad})

    def test_bad_input_is_refused_and_keeps_the_old_setting(self):
        save_clerk(self.project, {"provider": "tencent", "model": "m"})
        for bad in ({"provider": "没有这家", "model": "m"}, {"provider": "tencent", "model": "  "}):
            with self.assertRaises(ConfigError):
                save_clerk(self.project, bad)
        self.assertEqual(load_clerk(self.project)["provider"], "tencent")

    def test_a_key_never_reaches_the_file(self):
        save_clerk(self.project, {"provider": "tencent", "model": "m", "api_key": "good-key", "key": "good-key"})
        self.assertNotIn("good-key", (Path(self.project) / ".collab" / "配置.json").read_text(encoding="utf-8"))

    def test_team_settings_survive(self):
        team = {"dispatcher": "codex", "tester": "codex", "developer": "cursor", "reviewer": "agy"}
        save_team(self.project, team)
        save_clerk(self.project, {"provider": "tencent", "model": "m"})
        self.assertEqual(self.config()["team"], team)
        save_team(self.project, team)
        self.assertEqual(load_clerk(self.project)["model"], "m", "存团队不能冲掉行政设置")

    def test_stopping_only_affects_this_project(self):
        save_clerk(self.project, {"provider": "tencent", "model": "m"})
        save_clerk(self.project, {"provider": "tencent", "model": "m", "disabled": True})
        self.assertTrue(load_clerk(self.project)["disabled"])

    def test_server_saves_and_reads(self):
        status, answer = self.post("/api/clerk/setting", {"project": self.project, "provider": "bailian", "model": "qwen-flash"})
        self.assertEqual((status, answer.get("ok")), (200, True))
        status, answer = self.get("/api/clerk/setting", project=self.project, token="口令")
        self.assertEqual(status, 200)
        self.assertEqual(answer["setting"]["provider"], "bailian")
        status, answer = self.post("/api/clerk/setting", {"project": self.project, "provider": "没有这家", "model": "m"})
        self.assertEqual(status, 400)
        self.assertTrue(answer["error"])
        status, _ = self.post("/api/clerk/setting", {"project": self.project, "provider": "tencent", "model": "m"}, token="错的")
        self.assertEqual(status, 403)


class WrongProjectTest(ServerCase):
    def test_words_of_another_project_never_reach_this_projects_provider(self):
        """用户选每个项目各一份，就是不想让 B 项目的内容发给 A 项目选的服务商。"""
        fake = FakeModel()
        self.addCleanup(fake.close)
        other = tempfile.mkdtemp()
        form = {"project": self.project, "provider": "tencent", "base_url": fake.base, "model": "m", "api_key": "good-key"}
        self.assertEqual(self.post("/api/clerk/config", form)[0], 200)
        status, answer = self.post("/api/clerk/send", {"project": other, "conversation": "", "text": "秘密"})
        self.assertEqual(status, 409)
        self.assertTrue(answer["error"])
        self.assertEqual(fake.requests, [])

    def test_clear_then_nothing_is_sent(self):
        fake = FakeModel()
        self.addCleanup(fake.close)
        form = {"project": self.project, "provider": "tencent", "base_url": fake.base, "model": "m", "api_key": "good-key"}
        self.post("/api/clerk/config", form)
        self.post("/api/clerk/clear", {})
        status, _ = self.post("/api/clerk/send", {"project": self.project, "conversation": "", "text": "你好"})
        self.assertNotEqual(status, 200)
        self.assertEqual(fake.requests, [])


class WindowTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.swift = SWIFT.read_text(encoding="utf-8")

    def test_switching_project_changes_the_clerk_before_opening_the_chat(self):
        body = self.swift[self.swift.index("func switchTo(_ url: URL)"):self.swift.index("func remember(_ url: URL)")]
        self.assertIn("applyClerkSetting", body)
        self.assertLess(body.index("project = url"), body.index("applyClerkSetting"))
        self.assertLess(body.index("applyClerkSetting"), body.index("showLeft(firstLeft()"))

    def test_config_tells_the_service_which_project_and_provider(self):
        form = self.swift[self.swift.index("func clerkForm("):]
        form = form[:form.index("\n    }\n")]
        for word in ('"project"', '"provider"', '"base_url"', '"model"', '"api_key"'):
            self.assertIn(word, form)

    def test_new_projects_use_the_last_setting(self):
        self.assertIn("clerkLastProvider", self.swift)
