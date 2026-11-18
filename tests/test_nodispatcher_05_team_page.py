"""去掉调度员 D5：团队页只有行政、写测试、写代码、复查四行，页面和脚本里都不再有调度员。"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "desktop" / "static"


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageRules(unittest.TestCase):
    def test_node_checks(self):
        out = subprocess.run(["node", str(ROOT / "tests" / "nodispatcher_05_team_check.js")], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class PageHasNoDispatcher(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "team.html").read_text(encoding="utf-8")
        self.js = (STATIC / "team.js").read_text(encoding="utf-8")

    def test_html_has_four_rows_and_no_dispatcher_words(self):
        for banned in ("调度员", 'data-role="dispatcher"', "dispNote", 'class="custom"'):
            self.assertNotIn(banned, self.html)
        roles = re.findall(r'data-role="(\w+)"', self.html)
        self.assertEqual(roles, ["clerk", "tester", "developer", "reviewer"], "行政在最上面，其余三位照旧")

    def test_js_has_no_dispatcher_left(self):
        for banned in ("dispatcher", "调度员", "savedDispatcher", "dispNote"):
            self.assertNotIn(banned, self.js)

    def test_saving_does_not_pass_a_dispatcher_to_the_window(self):
        self.assertIn('"superagent://saved"', self.js)
        self.assertNotIn("saved?dispatcher", self.js)

    def test_page_uses_the_tested_rules_after_clerk_saved_and_for_the_need_clerk_notice(self):
        self.assertIn("TeamCore.afterClerkSaved(", self.js)
        # 页面自己不拦着不放人：行政有没有真接通只有外壳知道，由外壳把关（复查第 2 轮）；页面只在外壳送回来时提示
        done = self.js[self.js.index("function done()"):]
        done = done[:done.index("\n  }\n")]
        self.assertNotIn("leaveBlockedReason", done)
        self.assertIn('params.get("need_clerk")', self.js)
        self.assertIn("TeamCore.leaveBlockedReason(", self.js)
        # need_clerk=1 是保存团队后回对话被拦（可以说“团队已保存”）；=2 是没保存、只是再点“团队”想关掉
        self.assertIn('needClerk === "1" ? "团队已保存。" : ""', self.js)

    def test_the_need_clerk_notice_goes_away_once_the_clerk_is_connected(self):
        # 复查第 3 轮轻微：行政接通后，顶部“还差行政”不能还挂着
        self.assertIn("var needClerkNotice = false", self.js)
        team = self.js[self.js.index("window.SuperAgentTeam = {"):]
        for name in ("clerkConnected: function", "clerkResult: function"):
            fn = team[team.index(name):]
            fn = fn[:fn.index("\n    },")]
            self.assertIn("hideNeedClerkNotice()", fn, name)
        notice = self.js[self.js.index("function hideNeedClerkNotice()"):]
        notice = notice[:notice.index("\n  }\n")]
        self.assertIn("needClerkNotice", notice)
        self.assertIn('$("problem").hidden = true', notice)

    def test_page_learns_when_the_window_connects_the_clerk(self):
        # 填密钥并检测通过后，外壳接通了行政：页面要跟着知道，不然保存按钮的状态是错的
        team = self.js[self.js.index("window.SuperAgentTeam = {"):]
        self.assertIn("clerkConnected: function", team)
        fn = team[team.index("clerkConnected: function"):]
        fn = fn[:fn.index("},")]
        self.assertIn("clerkReady = true", fn)

    def test_no_git_note_does_not_promise_a_dispatcher_chat(self):
        note = re.search(r'<p class="note warn" id="noGit"[^>]*>(.*?)</p>', self.html, re.S).group(1)
        self.assertIn("git", note)
        self.assertIn("行政", note)
        self.assertNotIn("调度", note)


if __name__ == "__main__":
    unittest.main()
