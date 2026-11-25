"""验收：左栏能收起、三栏能拖宽窄（用户 2026-11-25）。

三栏的摆放和拖动用真的分隔视图实跑：把 Columns.swift 和 tests/columns_harness.swift 一起编译运行。
窗口外壳 main.swift 依赖终端组件、要开真窗口，测试里跑不起来，只能核对接线的写法；真窗口由应用自带的 --walk-columns 走查。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MAC = ROOT / "desktop" / "mac"
SOURCES = MAC / "Sources" / "SuperAgent"
COLUMNS = SOURCES / "Columns.swift"
MAIN = SOURCES / "main.swift"


class DragAndHide(unittest.TestCase):
    """每行：左、中、右、记住的左、记住的右、用户拖完的次数、点没点到分隔线。"""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("swiftc"):
            raise unittest.SkipTest("本机没有 swiftc")
        if not COLUMNS.is_file():
            raise AssertionError("缺 desktop/mac/Sources/SuperAgent/Columns.swift")
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main.swift"
            main.write_text((HERE / "columns_harness.swift").read_text(encoding="utf-8"), encoding="utf-8")
            built = subprocess.run(["swiftc", "-module-cache-path", d + "/cache", "-o", d + "/bench", str(COLUMNS), str(main)],
                                   capture_output=True, text=True, timeout=240)
            if built.returncode != 0:
                raise AssertionError("编译不过：\n" + built.stderr)
            out = subprocess.run([d + "/bench"], capture_output=True, text=True, timeout=60)
        if out.stdout.strip() == "NOSCREEN":
            raise unittest.SkipTest("这个环境没有屏幕，跑不了窗口")
        if out.returncode != 0:
            raise AssertionError("跑不起来：\n" + out.stdout + out.stderr)
        cls.rows = {}
        for line in out.stdout.splitlines():
            parts = line.split("|")
            if len(parts) == 8:
                cls.rows[parts[0]] = [int(x) for x in parts[1:7]] + [parts[7]]

    def near(self, name, nav=None, center=None, right=None, kept_nav=None, kept_right=None, drags=None, hit=None):
        row = self.rows[name]
        for label, want, got in (("左", nav, row[0]), ("中", center, row[1]), ("右", right, row[2]),
                                 ("记住的左", kept_nav, row[3]), ("记住的右", kept_right, row[4])):
            if want is not None:
                self.assertLessEqual(abs(want - got), 2, f"{name}：{label}应约 {want}，实际 {got}（整行 {row}）")
        if drags is not None:
            self.assertEqual(row[5], drags, f"{name}：拖完次数（整行 {row}）")
        if hit is not None:
            self.assertEqual(row[6], hit, f"{name}：点没点到分隔线（整行 {row}）")

    def test_open(self):
        self.near("打开", nav=264, center=1135, right=0, kept_nav=264, kept_right=372, drags=0)
        self.near("右栏出现", nav=264, center=762, right=372, kept_nav=264, kept_right=372, drags=0)

    def test_drag_left(self):
        self.near("左拖到320", nav=320, right=372, kept_nav=320, kept_right=372, drags=1, hit="是")
        self.near("偏3点也能拖", nav=358, kept_nav=358, drags=2, hit="是")
        self.near("只点不拖", nav=358, kept_nav=358, drags=2, hit="是")
        self.near("左拖过头", nav=420, right=372, kept_nav=420, drags=3)
        self.near("左拖到底", nav=200, right=372, kept_nav=200, drags=4)

    def test_drag_right(self):
        self.near("右拖到1000", nav=200, right=400, kept_nav=200, kept_right=400, drags=5, hit="是")
        self.near("右拖过头", nav=200, right=640, kept_right=640, drags=6)
        self.near("右拖到底", nav=200, right=300, kept_right=300, drags=7)

    def test_window_squeeze_does_not_change_what_the_user_set(self):
        self.near("B窗口1100", nav=300, center=440, right=358, kept_nav=300, kept_right=500, drags=0)
        self.near("B挤着时拖左", nav=250, kept_nav=250, kept_right=500, drags=1, hit="是")
        self.near("B窗口放回", nav=250, right=500, kept_nav=250, kept_right=500, drags=1)

    def test_hide_left(self):
        self.near("B收起左", nav=0, center=899, right=500, kept_nav=250, kept_right=500, drags=1)
        self.near("B收起后拖左边缘", nav=0, center=899, right=500, kept_nav=250, drags=1, hit="否")
        self.near("B收起窗口1000", nav=0, center=499, right=500, kept_nav=250, kept_right=500, drags=1)
        self.near("B收起后拖右", nav=0, center=850, right=549, kept_nav=250, kept_right=549, drags=2, hit="是")
        self.near("B展开左", nav=250, center=599, right=549, kept_nav=250, kept_right=549, drags=2)

    def test_no_right(self):
        self.near("B右栏没了", nav=250, center=1149, right=0, kept_nav=250, kept_right=549, drags=2)
        self.near("B没右栏拖右边缘", nav=250, center=1149, right=0, kept_right=549, drags=2, hit="否")
        self.near("B两边都没有", nav=0, center=1400, right=0, kept_nav=250, kept_right=549, drags=2)
        self.near("B都回来", nav=250, right=549, kept_nav=250, kept_right=549, drags=2)

    def test_narrow(self):
        self.near("C窄窗口", nav=319, center=440, right=0, kept_nav=420, kept_right=640, drags=0)
        self.near("C1000三栏", nav=258, center=440, right=300, kept_nav=420, kept_right=640, drags=0)
        # 没地方可拖、宽度没变：不算拖过，不能把挤窄后的宽度当成用户要的；拖左边更不能改掉右边记住的
        self.near("C拖左不挤中间", nav=258, center=440, right=300, kept_nav=420, kept_right=640, drags=0)
        self.near("C放宽到1700", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=0)

    def test_saved_numbers_out_of_range(self):
        self.near("D存的数离谱", nav=420, right=300, kept_nav=420, kept_right=300, drags=0)


class Wiring(unittest.TestCase):
    """main.swift 在测试里跑不起来（要终端组件、真窗口），只能核对写法。"""

    def setUp(self):
        self.src = MAIN.read_text(encoding="utf-8")

    def has(self, words):
        self.assertTrue(words in self.src, f"main.swift 里缺：{words}")

    def func(self, name):
        m = re.search(r"func " + name + r"\(.*?\n    \}\n", self.src, re.S)
        self.assertIsNotNone(m, f"缺 {name}")
        return m.group(0)

    def test_uses_columns(self):
        self.has("ThreeColumns(")
        for old in ("splitViewDidResizeSubviews", "constrainMinCoordinate", "class ThreeColumnSplit", "split.addSubview("):
            self.assertFalse(old in self.src, f"摆放和拖动只留 Columns.swift 一处，main.swift 里还有 {old}")

    def test_remembers_left_open_and_widths_but_not_in_selftest(self):
        self.has('let navOpenKey = "navOpen"')
        self.has("defaults.object(forKey: navOpenKey) as? Bool")
        body = self.func("setNavWanted")
        self.assertRegex(body, r"if !selfTest \{ UserDefaults\.standard\.set\(wanted, forKey: navOpenKey\) \}")
        self.assertIn("closeProjectMenu", body)   # 收起时顺手关掉左下角的项目浮层
        self.has("forKey: navWidthKey")
        self.has("forKey: rightWidthKey")

    def test_buttons_and_menu(self):
        self.has('"sidebar.left"')
        self.assertGreaterEqual(self.src.count("#selector(toggleNav(_:))"), 2)
        self.has('("收起或展开左栏", #selector(toggleNav(_:)), "b")')
        self.has("收起左栏")
        self.has("展开左栏")

    def test_walk(self):
        self.has("--walk-columns")


if __name__ == "__main__":
    unittest.main()
