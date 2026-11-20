"""团队页 agy 名单补上 Gemini Ultra 账号新开的模型（11-20 用户开通 Ultra）：Neon 四档、Carbon 三档、Orchestrator 三个。
名单以本机 `agy models` 的输出为准；默认仍是 Gemini 4 Argon（高），不跟着换。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from collab.config import DEFAULT_MODELS, MODEL_OPTIONS, model_label

HERE = Path(__file__).resolve().parent
NEW = [
    "gemini-4-neon-low", "gemini-4-neon-medium", "gemini-4-neon-high", "gemini-4-neon-xhigh",
    "gemini-4-carbon-low", "gemini-4-carbon-medium", "gemini-4-carbon-high",
    "gemini-pro-orchestrator-neon", "gemini-pro-orchestrator", "gemini-pro-orchestrator-argon",
]


class AgyUltraList(unittest.TestCase):
    def values(self):
        return [o["value"] for o in MODEL_OPTIONS["agy"]]

    def test_new_models_in_builtin_list(self):
        for v in NEW:
            self.assertIn(v, self.values())

    def test_no_duplicates_and_old_ones_kept(self):
        vals = self.values()
        self.assertEqual(len(vals), len(set(vals)))
        for v in ("gemini-4-argon-high", "gemini-4-hydrogen-xhigh", "gemini-3.8-flash-low",
                  "claude-opus-5-5-high", "gpt-oss-120b-medium"):
            self.assertIn(v, vals)

    def test_default_unchanged(self):
        self.assertEqual(DEFAULT_MODELS["agy"], "gemini-4-argon-high")

    def test_label_on_cards(self):
        self.assertEqual(model_label("agy", {"agy": "gemini-4-neon-xhigh"}), "Gemini 4 Neon（极高）")
        self.assertEqual(model_label("agy", {"agy": "gemini-4-carbon-low"}), "Gemini 4 Carbon（低）")
        self.assertEqual(model_label("agy", {"agy": "gemini-pro-orchestrator"}), "Orchestrator Carbon")


@unittest.skipUnless(shutil.which("node"), "没装 node")
class AgyUltraEffortBox(unittest.TestCase):
    def test_grouping(self):
        script = r"""
var T = require(process.argv[1] + "/../desktop/static/team.js");
var g = T.splitEfforts(JSON.parse(process.argv[2]));
function find(b) { return g.find(function (x) { return x.base === b; }); }
var out = {
  neon: find("gemini-4-neon") && find("gemini-4-neon").efforts.map(function (e) { return e.effort; }),
  neonLabel: find("gemini-4-neon") && find("gemini-4-neon").label,
  carbon: find("gemini-4-carbon") && find("gemini-4-carbon").efforts.map(function (e) { return e.effort; }),
  orch: ["gemini-pro-orchestrator-neon", "gemini-pro-orchestrator", "gemini-pro-orchestrator-argon"].map(function (b) {
    var x = find(b); return x ? x.efforts.length : -1; })
};
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "-e", script, str(HERE), json.dumps(MODEL_OPTIONS["agy"])],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["neon"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(out["neonLabel"], "Gemini 4 Neon")
        self.assertEqual(out["carbon"], ["low", "medium", "high"])
        self.assertEqual(out["orch"], [0, 0, 0], "Orchestrator 三个各占一项，没有强度框")


if __name__ == "__main__":
    unittest.main()
