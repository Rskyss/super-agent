"""分隔线要画在两栏交界处，不能留在旧位置（CARD-026 装好后真实窗口走查发现：窗口 1000 宽时左栏 258、线还在 264）。"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
COLUMNS = HERE.parent / "desktop" / "mac" / "Sources" / "SuperAgent" / "Columns.swift"


class LinesFollowTheColumns(unittest.TestCase):
    def test_lines_sit_on_the_edges(self):
        if not shutil.which("swiftc"):
            self.skipTest("本机没有 swiftc")
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main.swift"
            main.write_text((HERE / "columns_lines.swift").read_text(encoding="utf-8"), encoding="utf-8")
            built = subprocess.run(["swiftc", "-module-cache-path", d + "/cache", "-o", d + "/lines", str(COLUMNS), str(main)],
                                   capture_output=True, text=True, timeout=240)
            self.assertEqual(built.returncode, 0, built.stderr)
            out = subprocess.run([d + "/lines"], capture_output=True, text=True, timeout=60)
        if out.stdout.strip() == "NOSCREEN":
            self.skipTest("这个环境没有屏幕，跑不了窗口")
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = [line.split("|") for line in out.stdout.splitlines() if line.count("|") == 3]
        self.assertEqual(len(rows), 8, out.stdout)
        for name, left, right, drawn in rows:
            want = [int(x) for x in (left, right) if x != "-"]
            got = sorted(int(x) for x in drawn.split(",") if x)
            self.assertEqual(want, got, f"{name}：线应在 {want}，实际画在 {got}")


if __name__ == "__main__":
    unittest.main()
