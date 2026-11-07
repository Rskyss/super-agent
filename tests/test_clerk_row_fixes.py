"""第三批验收时查出的问题：这些都是测试没覆盖、读代码才发现的，修好后不能退回去。"""
import json
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import STATIC, SWIFT, ServerCase


class Source(unittest.TestCase):
    def setUp(self):
        self.swift = SWIFT.read_text(encoding="utf-8")
        self.js = (STATIC / "team.js").read_text(encoding="utf-8")

    def section(self, start, end):
        return self.swift[self.swift.index(start):self.swift.index(end)]


class WindowRules(Source):
    def test_page_is_told_what_the_project_really_uses(self):
        """没设过的项目沿用最近一次，页面不能显示成腾讯云。"""
        load = self.section("func loadConfig", 'static/team.html?')
        for word in ("resolvedClerkChoice", "clerk_provider", "clerk_base", "clerk_model"):
            self.assertIn(word, load)
        for word in ("clerk_provider", "clerk_model", "沿用"):
            self.assertIn(word, self.js)

    def test_changing_a_key_takes_effect_without_touching_the_last_setting(self):
        body = self.section("case .setup:\n", "case .check:\n                // 填的正是")
        self.assertIn("reconnectClerkWhenIdle", body)
        self.assertNotIn("clerkPrefs", body)

    def test_a_stopped_project_can_be_started_again(self):
        self.assertIn('case "clerk-enable"', self.swift)
        self.assertIn("superagent://clerk-enable", self.js)
        self.assertIn('"启用"', self.js)

    def test_forgetting_a_key_really_stops_using_it(self):
        body = self.section("func confirmForgetClerk", "func reconnectClerkWhenIdle")
        self.assertIn("reconnectClerkWhenIdle", body)
        self.assertNotIn('"disabled"', body, "删密钥不等于停用，再填一把就该能用")

    def test_a_bad_address_never_borrows_another_providers_key(self):
        body = self.section("func clerkAccount", "func providerFor")
        self.assertNotIn('return "tencent"\n    }', body)
        self.assertIn("clerkAddressIsBad", self.section("func applyClerkChoice", "func warnNewProviderIfNeeded"))
        self.assertIn("clerkAddressIsBad", self.section("func checkClerkChoice", "func askClerk()"))

    def test_self_tests_never_touch_the_real_keychain(self):
        key = self.swift[self.swift.index("enum ClerkKey"):self.swift.index("static func query")]
        for word in ("--walk", "--snapshot", "--clerk-key-file", "--clerk-try", "--clerk-ui-test"):
            self.assertIn(word, key)

    def test_key_prompt_names_the_provider(self):
        body = self.section("func askClerkKey", "func confirmClearClerk")
        self.assertIn("providerName(provider)", body)
        self.assertNotIn("_ = (provider", body)

    def test_same_provider_does_not_warn(self):
        body = self.section("func warnNewProviderIfNeeded", "func checkClerkChoice")
        self.assertIn("resolvedClerkChoice", body)

    def test_old_settings_keep_their_provider(self):
        self.assertIn('string(forKey: "clerkLastProvider") ?? providerFor(base: clerkBase)', self.swift)

    def test_clerk_can_be_saved_before_the_team_is_ready(self):
        """行政是最核心的角色：没选调度员、团队没检测时，也要能存行政。"""
        self.assertIn('!TeamCore.canSave({ team: t, active: active, results: results, configured: configured, busy: busy }) && !clerkChanged()', self.js)
        result = self.js[self.js.index("clerkResult: function"):self.js.index("clerkCheckResult: function")]
        self.assertIn("canSaveTeam()", result)

    def test_other_does_not_start_with_another_providers_address(self):
        self.assertIn("base_url === $(\"clerkAddress\").value", self.js)


class WrongModelName(unittest.TestCase):
    def test_says_what_to_check_instead_of_try_later(self):
        from desktop.clerk.client import _http_failure
        words = str(_http_failure(404))
        self.assertIn("模型", words)
        self.assertNotIn("稍后", words)


class Review(ServerCase):
    """独立复查查出的问题。"""

    def setUp(self):
        super().setUp()
        from fake_model_server import FakeModel
        self.fake = FakeModel()
        self.addCleanup(self.fake.close)

    def config(self, project=None, provider="tencent", **more):
        form = {"project": project or self.project, "provider": provider, "base_url": self.fake.base, "model": "m", "api_key": "good-key"}
        form.update(more)
        return self.post("/api/clerk/config", form)

    def test_a_project_keeps_the_provider_it_first_talked_to(self):
        """换了 A，B 不变：B 说过话后就固定下来，不再跟着“最近一次”走。"""
        from collab.config import load_clerk
        self.config()
        self.assertIsNone(load_clerk(self.project), "只是打开，还没说话，不固定")
        status, _ = self.post("/api/clerk/send", {"project": self.project, "conversation": "", "text": "你好"})
        self.assertEqual(status, 200)
        self.assertEqual(load_clerk(self.project)["provider"], "tencent")

    def test_first_talk_never_overwrites_a_saved_setting(self):
        from collab.config import load_clerk, save_clerk
        save_clerk(self.project, {"provider": "other", "base_url": "https://a.example.com/v1", "model": "x"})
        self.config()
        self.post("/api/clerk/send", {"project": self.project, "conversation": "", "text": "你好"})
        self.assertEqual(load_clerk(self.project)["model"], "x")

    def test_other_with_no_address_is_refused_not_sent_to_the_default(self):
        for path in ("/api/clerk/test", "/api/clerk/config"):
            status, answer = self.post(path, {"project": self.project, "provider": "other", "base_url": "", "model": "m", "api_key": "第三方密钥"})
            self.assertEqual(status, 400, path)
            self.assertTrue(answer["error"])
        self.assertIsNone(self.server.clerk)

    def test_a_folder_with_only_conversations_is_not_treated_as_set_up(self):
        from collab.config import save_clerk
        save_clerk(self.project, {"provider": "tencent", "model": "m"})
        status, answer = self.get("/api/team", project=self.project, token="口令")
        self.assertEqual(status, 200)
        self.assertFalse(answer["installed"])


class ReviewWindow(Source):
    def test_a_request_from_another_projects_page_is_dropped(self):
        """实测：隐藏的设置页发出的保存可能在切项目之后才到，A 的选择写进了 B。"""
        self.assertIn('url.host?.hasPrefix("clerk-") == true, value("project") != project.path', self.swift)
        self.assertIn('"project=" + encodeURIComponent(project)', self.js)
        for host in ("clerk-clear",):
            self.assertIn(f'superagent://{host}?', self.js)

    def test_inherited_setting_also_uses_the_programs_address(self):
        """最近一次是百炼、旧地址是腾讯云时，不能把百炼密钥发到腾讯云。"""
        body = self.section("func resolvedClerkChoice", "func clerkAccount")
        self.assertIn("knownBase(clerkProvider) ?? clerkBase", body)

    def test_walks_keep_away_from_the_real_settings(self):
        self.assertIn('UserDefaults(suiteName: "local.superagent.desktop.walk")', self.swift)
        for word in ('UserDefaults.standard.set(provider, forKey: "clerkLastProvider")', 'UserDefaults.standard.stringArray(forKey: "clerkWarned")',
                     'UserDefaults.standard.string(forKey: "clerkLastProvider")'):
            self.assertNotIn(word, self.swift)

    def test_can_leave_settings_once_the_clerk_is_ready(self):
        """没选调度员、只配了行政时，按钮要能点，能回到对话。"""
        self.assertIn("clerkReady && !configured", self.js)
        self.assertIn("segment.setEnabled", self.section("func updateViewSwitch", "func showLeft"))
        self.assertIn("updateViewSwitch()", self.section("func afterClerkKeyChange", "@objc func clerkSaveClicked"))

    def test_clerk_does_not_take_over_while_the_dispatcher_works(self):
        """调度员干活时换连接，屏幕和实际会对不上，话可能发错。"""
        body = self.swift[self.swift.index('case "clerk-apply":'):self.swift.index('case "clerk-setup":')]
        self.assertIn("applyWhenIdle", body)
        self.assertIn("whenChatIdle", self.section("func reconnectClerkWhenIdle", "func afterClerkKeyChange"))

    def test_check_result_is_for_this_project(self):
        self.assertIn("project == place", self.section("func checkClerkChoice", "func askClerk()"))

    def test_key_prompt_remembers_its_project(self):
        self.assertIn("clerkPromptPlace", self.section("func askClerkKey", "func confirmClearClerk"))
        self.assertIn("self.project == place", self.swift[self.swift.index("@objc func clerkSaveClicked"):])

    def test_a_check_with_a_new_key_keeps_unsaved_choices(self):
        body = self.swift[self.swift.index("case .check:\n                // 填的正是"):]
        self.assertIn("reloadPage: false", body[:600])

    def test_every_page_result_clears_and_unlocks(self):
        body = self.js[self.js.index("clerkCheckResult: function"):]
        body = body[:body.index("}\n  };")]
        self.assertIn('clerkCheckFor = ""', body)
        self.assertIn("lockClerkRow(false)", body)
        self.assertIn("clerkSent ||", self.js)

    def test_callbacks_give_up_when_the_project_changed(self):
        for name, end in (("func applyClerkChoice", "func warnNewProviderIfNeeded"),
                          ("func confirmClearClerk", "func confirmForgetClerk"),
                          ("func confirmForgetClerk", "func reconnectClerkWhenIdle")):
            body = self.section(name, end)
            self.assertIn("let place = project", body, name)
            self.assertIn("self.project == place", body, name)

    def test_cancelling_during_the_test_saves_nothing(self):
        body = self.section("@objc func clerkSaveClicked", "// MARK: 右边") if "// MARK: 右边" in self.swift[self.swift.index("@objc func clerkSaveClicked"):] else self.swift[self.swift.index("@objc func clerkSaveClicked"):]
        self.assertIn("self.clerkFields?.alert === form.alert", body)

    def test_setup_checks_the_address_too(self):
        body = self.swift[self.swift.index('case "clerk-setup":'):self.swift.index('case "clerk-clear":')]
        self.assertIn("clerkAddressIsBad", body)

    def test_key_is_only_written_when_it_is_new(self):
        body = self.section("func applyClerkChoice", "func warnNewProviderIfNeeded")
        self.assertIn("if key != nil", body)

    def test_warned_per_account_and_names_the_host(self):
        body = self.section("func warnNewProviderIfNeeded", "func checkClerkChoice")
        self.assertIn("warned.append(account)", body)
        self.assertIn("URL(string: base)?.host", body)

    def test_known_providers_ignore_the_address_in_the_file(self):
        body = self.section("func resolvedClerkChoice", "func clerkAccount")
        self.assertIn("knownBase", body)

    def test_old_key_never_overwrites_a_newer_one(self):
        body = self.swift[self.swift.index("static func migrateOldKey"):self.swift.index("enum ChatMode")]
        self.assertIn("read(account: account) == nil", body)

    def test_row_is_locked_while_saving(self):
        self.assertIn("lockClerkRow(true)", self.js)
        self.assertIn("lockClerkRow(false)", self.js)

    def test_check_result_for_an_old_choice_is_dropped(self):
        self.assertIn("clerkCheckFor", self.js)


class BrokenFile(ServerCase):
    def test_broken_settings_file_gets_a_plain_answer(self):
        folder = Path(self.project) / ".collab"
        folder.mkdir(exist_ok=True)
        (folder / "配置.json").write_text("{坏的", encoding="utf-8")
        status, answer = self.get("/api/clerk/setting", project=self.project, token="口令")
        self.assertEqual(status, 400)
        self.assertTrue(answer["error"])


if __name__ == "__main__":
    unittest.main()
