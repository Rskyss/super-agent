"""D2 第 3 轮复查（Claude 接手补）：只有完全有把握时才写“开工前没过的测试”名单；进度百分比没全过不显示 100%。"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import collab.dispatch as dispatch
from collab.judge import FAIL, Verdict, judge


class RedListOnlyWhenSure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cdir = Path(self.tmp.name) / "CARD-001"
        self.cdir.mkdir()
        self.wt = Path(self.tmp.name) / "wt"
        (self.wt / "tests").mkdir(parents=True)

    def write(self, name, body):
        (self.wt / "tests" / name).write_text(body, encoding="utf-8")

    def red(self, command, judge_fn=judge):
        return dispatch._red_check_one(self.cdir, "写测试", ["/bin/bash", "-o", "pipefail", "-c", command],
                                       judge_fn, self.wt)

    def listed(self):
        return (self.cdir / "开工前没过的测试.json").exists()

    def test_command_that_cannot_be_split_writes_no_list(self):
        self.write("test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.fail('x')\n")
        v = self.red('for f in test_a.py; do python3 -m unittest discover -s tests -p "$f" || exit 1; done')
        self.assertEqual(v.result, FAIL)
        self.assertFalse(self.listed(), "拆不开就退回整条跑，输出可能被截断，不写名单")

    def test_import_error_writes_no_list(self):
        self.write("test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.fail('x')\n")
        self.write("test_b.py", "import no_such_module_d2\n")
        v = self.red('python3 -m unittest discover -s tests -p "test_a.py" && '
                     'python3 -m unittest discover -s tests -p "test_b.py"')
        self.assertEqual(v.result, FAIL)
        self.assertFalse(self.listed(), "有一段一导入就报错，名单里会混进占位名字，不写")

    def test_fake_judge_in_tests_still_records(self):
        out = "FAIL: test_a (test_x.T)\nRan 1 test\n\nFAILED (failures=1)"
        self.red("whatever", judge_fn=lambda *a, **k: Verdict(FAIL, 1, out))
        self.assertEqual(json.loads((self.cdir / "开工前没过的测试.json").read_text(encoding="utf-8"))["tests"],
                         ["test_x.T.test_a"], "测试里注入的假判定照旧从它的输出认名字（验收测试靠这条）")


class PanelNeverRoundsUpToFull(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_268_of_269_is_not_100(self):
        root = Path(__file__).resolve().parents[1]
        script = ("const P=require('./desktop/static/panel.js');"
                  "const t=P.progressText({passed:268,total:269});"
                  "if(t.pct>=100)process.exit(1);"
                  "if(P.progressText({passed:5,total:5}).pct!==100)process.exit(2);")
        done = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
