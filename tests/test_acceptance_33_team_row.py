"""33 号单：设置页第一行是行政。流程要在真实窗口验收，这里只查约定。"""
import unittest

from acceptance_helpers import STATIC, SWIFT


class PageTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "team.html").read_text(encoding="utf-8")
        self.js = (STATIC / "team.js").read_text(encoding="utf-8")
        self.swift = SWIFT.read_text(encoding="utf-8")

    def test_clerk_is_the_first_row_and_not_optional(self):
        self.assertLess(self.html.index('data-role="clerk"'), self.html.index('data-role="dispatcher"'))
        self.assertNotIn("可选", self.html)

    def test_row_has_the_agreed_controls(self):
        for name in ('id="clerkProvider"', 'id="clerkModel"', 'id="clerkAddress"', 'id="clerkCheck"',
                     'id="clerkKey"', 'id="clerkClear"', 'id="clerkForget"'):
            self.assertIn(name, self.html)
        self.assertIn('<datalist id="clerkModels"', self.html, "模型能选也能手填")

    def test_page_never_touches_a_key(self):
        self.assertNotIn("api_key", self.js)
        self.assertNotIn('type="password"', self.html)

    def test_page_reads_the_list_and_the_setting(self):
        self.assertIn("/api/clerk/providers", self.js)
        self.assertIn("/api/clerk/setting", self.js)

    def test_window_handles_every_address(self):
        for host in ("clerk-apply", "clerk-check", "clerk-setup", "clerk-clear", "clerk-forget"):
            self.assertIn(f"superagent://{host}", self.js)
            self.assertIn(f'case "{host}"', self.swift)

    def test_result_comes_back_to_the_page(self):
        self.assertIn("window.SuperAgentTeam", self.js)
        self.assertIn("clerkResult", self.js)
        self.assertIn("window.SuperAgentTeam", self.swift)

    def test_only_saves_after_a_passing_test(self):
        apply = self.swift[self.swift.index("func applyClerkChoice"):]
        apply = apply[:apply.index("\n    }\n")]
        self.assertIn('"/api/clerk/test"', apply)
        self.assertLess(apply.index('"/api/clerk/test"'), apply.index('"/api/clerk/setting"'))

    def test_warns_once_before_sending_to_a_new_provider(self):
        self.assertIn("会发给", self.swift)
        self.assertIn("clerkWarned", self.swift)

    def test_other_warns_about_cost(self):
        self.assertIn("可能更费钱", self.html + self.js)
