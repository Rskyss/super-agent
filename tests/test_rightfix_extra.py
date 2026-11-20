"""右栏显示小修的补充验收：命令记录、接手格和完成时间。"""
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "card019"


def station(view, key):
    return next((item for item in view["stations"] if item["key"] == key), None)


class Card019(unittest.TestCase):
    def setUp(self):
        from collab.workspace import init_collab

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "项目"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        init_collab(str(self.project))
        self.card = self.project / ".collab" / "cards" / "CARD-019"
        shutil.copytree(FIXTURE, self.card)

    def view(self):
        from desktop.view import card_view

        return card_view(str(self.project), "CARD-019")

    def takeover(self):
        from collab.cli import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                code = main([
                    "takeover", str(self.project), "CARD-019",
                    "--changed", "4", "--reviews", "2", "--later", "2",
                    "--note", "接手修好了停用行政和主对话草稿两处问题",
                ])
            except SystemExit as stop:
                code = stop.code
        self.assertIn(code, (0, None), output.getvalue())


class TakeoverRecords(Card019):
    def test_records_claude_event_and_keeps_existing_finish_time(self):
        from collab.flow import read_flow

        self.takeover()
        events = [event for event in read_flow(self.card) if event.get("kind") == "接手"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("src"), "Claude")
        self.assertEqual((events[0].get("changed"), events[0].get("reviews"), events[0].get("later")), (4, 2, 2))
        self.assertIn("接手修好", events[0].get("summary", ""))
        self.assertEqual(self.view().get("finished_at"), 1795150840.8,
                         "已合并的卡补记接手后，完成时间要保留")

    def test_unknown_card_is_refused_without_writing_flow(self):
        from collab.cli import main

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            try:
                code = main([
                    "takeover", str(self.project), "CARD-404",
                    "--changed", "1", "--reviews", "0", "--later", "0", "--note", "没有这张卡",
                ])
            except SystemExit as stop:
                code = stop.code
        self.assertNotIn(code, (0, None), stderr.getvalue())
        self.assertFalse((self.project / ".collab" / "cards" / "CARD-404" / "流转.jsonl").exists())


class TakeoverView(Card019):
    def test_station_and_review_cell_follow_latest_events(self):
        from collab.flow import record

        self.takeover()
        first = self.view()
        self.assertEqual([item["key"] for item in first["stations"]],
                         ["you", "disp", "test", "dev", "check", "rev", "takeover", "accept"])
        self.assertEqual(station(first, "takeover")["role"], "Claude 接手")
        self.assertEqual(station(first, "rev")["state"], "done")
        self.assertIn("退回 4 件", station(first, "rev")["detail"])
        self.assertIn("已由 Claude 接手修好", station(first, "rev")["detail"])

        record(self.card, "复查结论", src="程序", result="退回", summary="接手后又退回 1 件")
        second = self.view()
        self.assertEqual(station(second, "rev")["state"], "fail")
        self.assertIn("接手后又退回 1 件", station(second, "rev")["detail"])
        self.assertNotIn("已由 Claude 接手修好", station(second, "rev")["detail"])
        self.assertIsNotNone(station(second, "takeover"))


class PagePart(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_page_check(self):
        environment = dict(os.environ, TZ="Asia/Shanghai")
        process = subprocess.run(
            ["node", str(ROOT / "tests" / "rightfix_extra_page.js")],
            capture_output=True, text=True, timeout=120, env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("OK", process.stdout)


if __name__ == "__main__":
    unittest.main()


class NoTrailingLine(unittest.TestCase):
    """Claude 接手（Codex 复查）：没接手过的卡只有 7 格，第 8 格藏着；“确认”下面不能多画一截连线。
    连线是样式画的，页面检查不加载样式表，只能看样式规则（规则包《测试怎么写》）。"""

    def test_step_before_hidden_slot_has_no_line(self):
        import re
        css = (Path(__file__).resolve().parents[1] / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.steps li:has\(\+ li\[hidden\]\)::before\s*\{[^}]*display:\s*none")
