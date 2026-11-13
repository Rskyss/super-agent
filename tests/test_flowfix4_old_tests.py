"""流程修理第四轮 任务 2（59 前半）：改了页面文件，也挑出相关老测试，并顺着辅助脚本找。

69b 只改了 desktop/static 下的页面文件，“相关老测试”只看 .py，一项都没挑；
68c 的页面测试是 test_acceptance_68c_additional.py 经 cli_models_68c_page_check.js 加载 abilities.js，绕了一层。
"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.dispatch import _related_old_tests


class Related(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wt = root / "wt"
        self.cdir = root / "card"
        self.cdir.mkdir()
        t = self.wt / "tests"
        t.mkdir(parents=True)
        (self.wt / "desktop" / "static").mkdir(parents=True)
        (t / "cli_models_68c_page_check.js").write_text(
            'vm.runInContext(fs.readFileSync(path.join(staticDir, "abilities.js")))\n', encoding="utf-8")
        (t / "test_acceptance_68c_additional.py").write_text(
            'script = Path(__file__).with_name("cli_models_68c_page_check.js")\n', encoding="utf-8")
        (t / "test_direct_page.py").write_text('read("desktop/static/abilities.js")\n', encoding="utf-8")
        (t / "test_core.py").write_text('read("abilities_core.js")\n', encoding="utf-8")
        (t / "test_unrelated.py").write_text('read("chat.js")\n', encoding="utf-8")
        (t / "test_mine.py").write_text('read("abilities.js")\n', encoding="utf-8")
        (self.cdir / "测试文件.json").write_text(json.dumps(["tests/test_mine.py"]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def pick(self, changed):
        return _related_old_tests(self.wt, self.cdir, lambda wt: changed)

    def test_page_file_finds_direct_and_helper_tests(self):
        found = self.pick(["desktop/static/abilities.js"])
        self.assertIn("tests/test_direct_page.py", found)
        self.assertIn("tests/test_acceptance_68c_additional.py", found, "顺着辅助脚本找")
        self.assertNotIn("tests/test_unrelated.py", found)
        self.assertNotIn("tests/test_mine.py", found, "本卡自己的测试不算老测试")

    def test_name_must_be_whole(self):
        found = self.pick(["desktop/static/abilities.js"])
        self.assertNotIn("tests/test_core.py", found, "abilities_core.js 不算提到 abilities.js")

    def test_python_change_still_works(self):
        (self.wt / "pkg").mkdir()
        (self.wt / "tests" / "test_old_money.py").write_text("from pkg import money\n", encoding="utf-8")
        self.assertEqual(self.pick(["pkg/money.py"]), ["tests/test_old_money.py"])

    def test_test_files_and_caches_ignored(self):
        self.assertEqual(self.pick(["tests/cli_models_68c_page_check.js", "desktop/__pycache__/x.pyc"]), [])


if __name__ == "__main__":
    unittest.main()
