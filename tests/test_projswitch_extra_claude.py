"""CARD-019 Claude 接手补的检查：真的执行聊天页脚本，守住主对话没发的字（复查第 3 轮第 1 条）。"""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class MainDraftSurvivesCardsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_main_draft_survives_opening_and_closing_a_card(self):
        proc = subprocess.run(["node", str(HERE / "projswitch_extra_claude_dom.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


ROOT = HERE.parent
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"


def block_after(text, start):
    i = text.find(start)
    j = text.find("{", i) if i >= 0 else -1
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(text)):
        depth += {"{": 1, "}": -1}.get(text[k], 0)
        if depth == 0:
            return text[j:k + 1]
    return ""


class ShellPlaceRulesTest(unittest.TestCase):
    """外壳的几条规矩（Swift 不能在这里实跑，按写法守住；真实效果由 Claude 在真实窗口走查）。"""

    def setUp(self):
        self.s = SWIFT.read_text(encoding="utf-8")

    def test_pending_card_is_what_gets_remembered(self):
        body = block_after(self.s, "func cardToRemember(")
        self.assertIn("if let card = selectedCard { return card }", body)
        self.assertRegex(body, r"if pendingPlaceProject == project\.path, let card = pendingPlaceCard, !card\.isEmpty \{ return card \}")

    def test_pending_card_is_set_as_soon_as_project_changes(self):
        body = block_after(self.s, "func switchTo(")
        at_project = body.find("self.project = url")
        at_pending = body.find("self.pendingPlaceCard = place")
        at_clerk = body.find("self.applyClerkSetting")
        self.assertTrue(0 <= at_project < at_pending < at_clerk, "换了项目马上记下待恢复的卡，不等行政配置回来")

    def test_card_answer_is_checked_against_its_project(self):
        body = block_after(self.s, "func openClerkCard(")
        self.assertIn("self.project.path == asked", body, "开卡回来要核对还是同一个项目")

    def test_draft_is_written_as_reported(self):
        body = block_after(self.s, "func saveChatPlace(")
        self.assertIn('record["draft"] = String((place["draft"] as? String ?? "").prefix(2000))', body)
        self.assertNotIn("if ", body, "聊天页报的就是主对话那份草稿，照写，不再按条件跳过")

    def test_quit_and_switch_pass_the_reported_place_unchanged(self):
        # 复查确认轮第 3 条：不光保存函数照写，调用时也要把聊天页报的原样交进去（看卡时它报的就是主对话的字）
        flush = block_after(self.s, "func flushChatPlace(")
        self.assertIn("self.saveChatPlace(place, for: project, card: card)", flush)
        self.assertNotRegex(flush, r'place\["draft"\]\s*=', "退出补存不能改写聊天页报的草稿")
        switch = block_after(self.s, "func switchTo(")
        self.assertIn("self.saveChatPlace(place, for: oldProject, card: oldCard)", switch)
        self.assertNotRegex(switch, r'place\["draft"\]\s*=', "切走时不能改写聊天页报的草稿")


if __name__ == "__main__":
    unittest.main()
