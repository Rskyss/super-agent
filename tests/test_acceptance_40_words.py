"""40 号单：文字和状态。用户看的是大白话，同一张卡各处说法一致。"""
import json
import unittest

from acceptance_helpers import ROOT, STATIC, ProjectCase, ScriptedClient, needs_node, run_node
from collab.flow import record
from collab.phase import card_phase
from desktop.clerk.desk import ClerkDesk
from desktop.view import card_view


def panel(script):
    return run_node("const P = require(" + json.dumps(str(STATIC / "panel.js")) + ");" + script)


class Buttons(unittest.TestCase):
    @needs_node()
    def test_plain_words(self):
        done = panel("""
          const b = P.actionButtons({ actions: ['accept', 'revise', 'discard'], branch: 'main' }, true).map(x => x.label);
          const want = ['确认没问题，放进项目', '不满意，继续改', '放弃这个需求'];
          if (JSON.stringify(b) !== JSON.stringify(want)) throw new Error(JSON.stringify(b));
        """)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_giving_up_says_what_happens(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("这次的改动不会进入项目，你的项目不会变。", js)
        self.assertNotIn("这张卡会停下来", js)

    def test_no_inside_words_for_the_user(self):
        for path in (ROOT / "collab" / "merge.py", ROOT / "collab" / "phase.py", STATIC / "panel.js", STATIC / "chat.js"):
            source = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                               if "raise _Stop" in line or "summary" in line or "label" in line or "detail" in line)
            self.assertNotIn("施工副本", source, path.name)
            self.assertNotIn("合并到 ", source if path.suffix == ".js" else "", path.name)


class States(ProjectCase):
    def test_failure_label(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突")
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual((phase["code"], phase["label"]), ("merge_failed", "没能放进项目"))

    def test_flow_agrees_with_the_top(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突")
        step = next(s for s in card_view(self.project, "CARD-001")["stations"] if s["key"] == "accept")
        self.assertEqual(step["state"], "fail")
        self.assertIn("有冲突", step["detail"])
        self.assertNotIn("等你验收", step["detail"])

    def test_normal_review_still_waits(self):
        self.make_card(state="待验收", plan=True)
        step = next(s for s in card_view(self.project, "CARD-001")["stations"] if s["key"] == "accept")
        self.assertEqual(step["state"], "wait")
        self.assertIn("请你确认", step["detail"])

    def test_chat_says_the_same(self):
        from desktop.clerk.tasks import TaskWatcher

        class Said:
            def __init__(self):
                self.lines = []

            def notify(self, project, conversation, text):
                self.lines.append(text)
                return True

        cdir = self.make_card(state="待验收", source="c" * 32, reported="review", title="甲")
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突")
        said = Said()
        TaskWatcher(said).tick(self.project)
        self.assertEqual(said.lines, ["「甲」没能放进项目：有冲突。"])


class OneTitle(ProjectCase):
    def test_chat_title_matches_the_list(self):
        cdir = self.make_card(state="待验收", title="执行员务必先看：不要调用 Shell 工具")
        (cdir / "需求卡.md").write_text("# 执行员务必先看：不要调用 Shell 工具\n别调命令。\n\n# 三处显示问题修复\n\n## 要做什么\n改\n",
                                        encoding="utf-8")
        listed = card_view(self.project, "CARD-001")["card"]["title"]
        opened = ClerkDesk(lambda: ScriptedClient()).open_card(self.project, "CARD-001")["title"]
        self.assertEqual(listed, "三处显示问题修复")
        self.assertEqual(opened, listed)
