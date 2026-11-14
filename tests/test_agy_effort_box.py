"""团队页 agy：名单有 Gemini 4 Hydrogen 四档，页面把模型和思考强度拆成两个框（11-14 用户要求）。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from collab.config import MODEL_OPTIONS, model_label

HERE = Path(__file__).resolve().parent


class AgyHydrogenList(unittest.TestCase):
    def test_hydrogen_four_levels_in_builtin_list(self):
        values = [o["value"] for o in MODEL_OPTIONS["agy"]]
        for level in ("low", "medium", "high", "xhigh"):
            self.assertIn(f"gemini-4-hydrogen-{level}", values)

    def test_label_on_cards(self):
        self.assertEqual(model_label("agy", {"agy": "gemini-4-hydrogen-xhigh"}), "Gemini 4 Hydrogen（极高）")
        self.assertEqual(model_label("agy", {"agy": "gemini-4-hydrogen-low"}), "Gemini 4 Hydrogen（低）")


@unittest.skipUnless(shutil.which("node"), "没装 node")
class AgyEffortBoxRules(unittest.TestCase):
    def test_split_rules(self):
        out = subprocess.run(
            ["node", str(HERE / "agy_effort_check.js"), json.dumps(MODEL_OPTIONS["agy"])],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("OK", out.stdout)

    def test_clicking_keeps_effort_and_custom_name(self):
        out = subprocess.run(
            ["node", str(HERE / "agy_effort_box_events.js"), json.dumps(MODEL_OPTIONS["agy"])],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("OK", out.stdout)

    def test_page_uses_effort_box_for_agy(self):
        js = (HERE.parent / "desktop" / "static" / "team.js").read_text(encoding="utf-8")
        self.assertIn('tool === "agy"', js[js.index("function renderRoleModelBox"):])
        self.assertIn("splitEfforts", js[js.index("function renderRoleModelBox"):])


if __name__ == "__main__":
    unittest.main()
