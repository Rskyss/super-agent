"""右栏显示小修复查退回四条的验收测试。"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "card019"
MERGED_AT = 1795150840.8


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

    def view(self, state="已合并"):
        from collab.state import Card
        from desktop.view import card_view

        Card("CARD-019", state=state).save(self.card / "状态.json")
        return card_view(str(self.project), "CARD-019")

    def takeover(self):
        from collab.cli import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "takeover", str(self.project), "CARD-019",
                "--changed", "4", "--reviews", "2", "--later", "2",
                "--note", "复查退回的四件已经修好",
            ])
        self.assertIn(code, (0, None), output.getvalue())


class BlockedReasonTest(Card019):
    def test_middle_card_shows_the_whole_blocked_reason(self):
        view = self.view("需Claude介入")
        environment = dict(
            os.environ,
            TZ="Asia/Shanghai",
            CARD020_REWORK_VIEW=json.dumps(view, ensure_ascii=False),
        )
        process = subprocess.run(
            ["node", str(ROOT / "tests" / "rightfix_rework_page.js")],
            capture_output=True, text=True, timeout=120, env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("OK", process.stdout)


class FinishTimeTest(Card019):
    def test_takeover_after_merge_keeps_merge_time(self):
        self.takeover()
        self.assertEqual(self.view().get("finished_at"), MERGED_AT)


class PageParts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_expand_keeps_collapse_button_and_large_usage_words(self):
        environment = dict(os.environ, TZ="Asia/Shanghai")
        environment.pop("CARD020_REWORK_VIEW", None)
        process = subprocess.run(
            ["node", str(ROOT / "tests" / "rightfix_rework_page.js")],
            capture_output=True, text=True, timeout=120, env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("OK", process.stdout)


if __name__ == "__main__":
    unittest.main()
