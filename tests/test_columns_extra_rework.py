"""复查退回问题专项测试（CARD-026）：一条复查问题对应一条自动化测试。

1. test_issue_1_hit_test_reaches_right_toggle_and_right_column：
   用真实 ThreeColumnSplit 与按钮子视图实跑 hitTest，验证左栏展开/收起时点右栏收起按钮、右栏内容、左栏按钮均准确命中。
2. test_issue_2_both_maxed_columns_shrink_window_preserves_center_min_width：
   用真实 ThreeColumns 在宽窗口把左右两栏都拖到最宽（420/640）后缩窗到 1400 / 1100，验证保住中间 440 点且先缩右栏。
3. test_issue_3_walk_columns_reaches_500_right_width_after_left_360：
   从 main.swift 推算 --walk-columns 执行到“右线拖到离右边500”时的走查窗口宽度（因 main.swift 依赖 SwiftTerm 无法在单元测试里直接拉起完整窗口），
   再用该窗口宽度实跑 Columns.swift 依次拖左线到 360、右线到离右边 500，验证右栏真的能走到约 500 点。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent"
COLUMNS = SOURCES / "Columns.swift"
MAIN = SOURCES / "main.swift"


def _balanced_block(text: str, marker: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """从 marker 开始，返回紧随其后的完整配对括号块（支持跳过字符串字面量）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    start = text.find(open_ch, idx)
    if start < 0:
        return ""
    depth = 0
    in_str = False
    escaped = False
    for pos in range(start, len(text)):
        ch = text[pos]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    return ""


def _parse_numeric(expr: str, consts: dict) -> Optional[float]:
    expr = expr.strip()
    if expr in consts:
        return consts[expr]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", expr)
    if m:
        return float(m.group(1))
    return None


def _collect_consts(text: str) -> dict:
    consts = {}
    for m in re.finditer(r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*:\s*CGFloat)?\s*=\s*(\d+(?:\.\d+)?)\b", text):
        consts[m.group(1)] = float(m.group(2))
    return consts


def _extract_resize_widths(snippet: str, current_width: float, consts: dict) -> float:
    """从一段 Swift 代码里按顺序提取所有改窗口宽度的操作，返回最新宽度。"""
    width = current_width
    # 识别 resize(...) / setContentSize(NSSize(width: ...)) / size.width = ...
    patterns = [
        re.compile(r"\bresize\(\s*([^)]+)\)"),
        re.compile(r"\bsetContentSize\(\s*NSSize\(\s*width:\s*([^,)]+)"),
        re.compile(r"\.size\.width\s*=\s*([^\n;]+)"),
    ]
    events = []
    for pat in patterns:
        for m in pat.finditer(snippet):
            events.append((m.start(), m.group(1).strip()))
    events.sort(key=lambda item: item[0])

    for _, raw in events:
        # 兼容 max(window.frame.width, 1400) 或 max(1400, ...)
        max_m = re.match(r"max\(([^,]+),([^)]+)\)", raw)
        if max_m:
            vals = []
            for part in (max_m.group(1), max_m.group(2)):
                v = _parse_numeric(part, consts)
                if v is not None:
                    vals.append(v)
                elif "width" in part:
                    vals.append(width)
            if vals:
                width = max(vals)
            continue
        v = _parse_numeric(raw, consts)
        if v is not None:
            width = v
    return width


def _parse_walk_columns_plan(main_src: str):
    """解析 main.swift 里 --walk-columns 走查到拖右线为 500 时的窗口宽度、左目标和右目标。"""
    build_body = _balanced_block(main_src, "func buildWindow(")
    build_consts = _collect_consts(build_body)

    # 1. 解析 buildWindow 里的初始窗口宽度（含针对 --walk-columns 的条件表达式）
    win_call = _balanced_block(build_body, "NSWindow(", "(", ")")
    walk_width = 1280.0
    m_cond = re.search(
        r'CommandLine\.arguments\.contains\("--walk-columns"\)\s*\?\s*([A-Za-z0-9_.]+)\s*:\s*([A-Za-z0-9_.]+)',
        win_call,
    )
    if m_cond:
        v = _parse_numeric(m_cond.group(1), build_consts)
        if v is not None:
            walk_width = v
    else:
        m_w = re.search(r"\bwidth:\s*([A-Za-z0-9_.]+)", win_call)
        if m_w:
            v = _parse_numeric(m_w.group(1), build_consts)
            if v is not None:
                walk_width = v

    # 2. 检查 buildWindow / scheduleWalk 里是否有 if CommandLine.arguments.contains("--walk-columns") { ... }
    walk_body = _balanced_block(main_src, "func scheduleWalk(")
    walk_consts = {**build_consts, **_collect_consts(walk_body)}
    for scope in (build_body, walk_body):
        for m in re.finditer(r'if\s+CommandLine\.arguments\.contains\("--walk-columns"\)\s*\{', scope):
            block = _balanced_block(scope[m.start():], "if ")
            walk_width = _extract_resize_widths(block, walk_width, walk_consts)

    # 3. 逐条扫描 columnSteps 直到“右线拖到离右边 500”这一步（含该步，不含之后的缩窗步骤）
    cs_idx = walk_body.find("columnSteps")
    eq_idx = walk_body.find("=", cs_idx) if cs_idx >= 0 else -1
    steps_array = _balanced_block(walk_body[eq_idx:], "=", "[", "]") if eq_idx >= 0 else ""
    if not steps_array:
        raise AssertionError("main.swift 的 scheduleWalk 中缺少 columnSteps 定义")

    left_target = None
    right_target = None
    pos = 1  # 跳过开头的 '['
    while pos < len(steps_array):
        start = steps_array.find("(", pos)
        if start < 0:
            break
        tuple_str = _balanced_block(steps_array[start:], "(", "(", ")")
        if not tuple_str:
            break
        pos = start + len(tuple_str)

        # 提取本步骤里的调宽动作
        walk_width = _extract_resize_widths(tuple_str, walk_width, walk_consts)

        # 提取左线拖动目标
        m_left = re.search(r"dragDivider\(\s*navLine\(\)\s*,\s*([^)]+)\)", tuple_str)
        if m_left and left_target is None:
            val = _parse_numeric(m_left.group(1), walk_consts)
            if val is not None:
                left_target = val

        # 提取第一次右线拖动目标（收起左栏之前），读完该步就停，后面的窗口缩到 900 不影响本步
        m_right = re.search(r"dragDivider\(\s*rightLine\(\)\s*,\s*([^)]+)\)", tuple_str)
        if m_right and right_target is None:
            expr = m_right.group(1)
            m_sub = re.search(r"-\s*(\d+(?:\.\d+)?)\s*$", expr.strip())
            if m_sub:
                sub = float(m_sub.group(1))
                # main.swift 写法为 width - 501（含 1 点分隔线）表示右栏宽 500
                right_target = sub - 1.0 if abs(sub - 501.0) < 0.01 else sub
            break

    return walk_width, left_target, right_target


class ColumnsReviewReworkTests(unittest.TestCase):
    """CARD-026 复查退回的 3 条问题专项测试（每条问题对应一个测试方法）。"""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("swiftc"):
            raise unittest.SkipTest("本机没有 swiftc")
        if not COLUMNS.is_file():
            raise AssertionError("缺 desktop/mac/Sources/SuperAgent/Columns.swift")

        cls.main_src = MAIN.read_text(encoding="utf-8")
        cls.walk_width, cls.walk_left_target, cls.walk_right_target = _parse_walk_columns_plan(cls.main_src)
        sim_left = cls.walk_left_target if cls.walk_left_target is not None else 360.0
        sim_right = cls.walk_right_target if cls.walk_right_target is not None else 500.0

        with tempfile.TemporaryDirectory() as d:
            main_swift = Path(d) / "main.swift"
            main_swift.write_text((HERE / "columns_extra_rework.swift").read_text(encoding="utf-8"), encoding="utf-8")
            built = subprocess.run(
                ["swiftc", "-module-cache-path", d + "/cache", "-o", d + "/rework_bench", str(COLUMNS), str(main_swift)],
                capture_output=True, text=True, timeout=240,
            )
            if built.returncode != 0:
                raise AssertionError("Columns.swift 与复查专项实跑程序编译不过：\n" + built.stderr)
            out = subprocess.run(
                [d + "/rework_bench", str(cls.walk_width), str(sim_left), str(sim_right)],
                capture_output=True, text=True, timeout=60,
            )
        if out.stdout.strip() == "NOSCREEN":
            raise unittest.SkipTest("这个环境没有屏幕，跑不了窗口")
        if out.returncode != 0:
            raise AssertionError("复查专项实跑程序运行失败：\n" + out.stdout + out.stderr)

        cls.issue1 = {}
        cls.issue2 = {}
        cls.issue3 = {}
        for line in out.stdout.splitlines():
            parts = line.split("|")
            if not parts:
                continue
            if parts[0] == "ISSUE1" and len(parts) == 4:
                cls.issue1[parts[1]] = (parts[2], parts[3])
            elif parts[0] == "ISSUE2" and len(parts) == 9:
                cls.issue2[parts[1]] = [int(x) for x in parts[2:8]] + [parts[8]]
            elif parts[0] == "ISSUE3" and len(parts) == 9:
                cls.issue3[parts[1]] = [int(x) for x in parts[2:8]] + [parts[8]]

    def _assert_row(self, table, name, nav=None, center=None, right=None, kept_nav=None, kept_right=None, drags=None, hit=None):
        self.assertIn(name, table, f"缺少实跑输出步骤：{name}")
        row = table[name]
        for label, want, got in (
            ("左", nav, row[0]),
            ("中", center, row[1]),
            ("右", right, row[2]),
            ("记住的左", kept_nav, row[3]),
            ("记住的右", kept_right, row[4]),
        ):
            if want is not None:
                self.assertLessEqual(abs(want - got), 2, f"{name}：{label}应约 {want}，实际 {got}（整行 {row}）")
        if drags is not None:
            self.assertEqual(row[5], drags, f"{name}：拖完次数应为 {drags}，实际 {row[5]}（整行 {row}）")
        if hit is not None:
            self.assertEqual(row[6], hit, f"{name}：点没点到分隔线应为 {hit}，实际 {row[6]}（整行 {row}）")

    def test_issue_1_hit_test_reaches_right_toggle_and_right_column(self):
        """复查问题 1：左栏展开（及收起）时，点右栏收起按钮、右栏内容、中栏和左栏按钮都必须准确命中。"""
        expected_targets = [
            "三栏_右栏收起按钮",
            "三栏_右栏顶部按钮",
            "三栏_右栏中间内容",
            "三栏_中栏偏左内容",
            "三栏_左栏收起按钮",
            "三栏_左分隔线",
            "三栏_右分隔线",
            "收左_右栏收起按钮",
            "收左_右栏中间内容",
            "收左_右分隔线",
        ]
        failures = []
        for name in expected_targets:
            self.assertIn(name, self.issue1, f"缺少点击命中检查：{name}")
            status, detail = self.issue1[name]
            if status != "OK":
                failures.append(f"{name}: {detail}")
        self.assertEqual(failures, [], "三栏点击命中坐标有偏差，导致按钮没反应或落到别处：\n" + "\n".join(failures))

    def test_issue_2_both_maxed_columns_shrink_window_preserves_center_min_width(self):
        """复查问题 2：左右两栏都拉到最宽（420/640）后缩小窗口到 1400 / 1100，必须保住中间 440 点且先让右栏。"""
        self._assert_row(self.issue2, "双最宽1700", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=2, hit="是")
        # 缩到 1400：1400 - 2 - 420 - 440 = 538，先缩右栏，保住中间 440，记住的宽度不变
        self._assert_row(self.issue2, "双最宽缩到1400", nav=420, center=440, right=538, kept_nav=420, kept_right=640, drags=2)
        # 再缩到 1100：右栏先缩到下限 300，再缩左栏到 1100 - 2 - 300 - 440 = 358，保住中间 440
        self._assert_row(self.issue2, "双最宽缩到1100", nav=358, center=440, right=300, kept_nav=420, kept_right=640, drags=2)
        # 放宽回 1700：恢复到记住的 420 / 640
        self._assert_row(self.issue2, "双最宽放回1700", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=2)

    def test_issue_3_walk_columns_reaches_500_right_width_after_left_360(self):
        """复查问题 3：--walk-columns 走查依次将左线拖到 360、右线拖到离右边 500 时，右栏必须真实走到约 500 点。"""
        self.assertIsNotNone(self.walk_left_target, "columnSteps 缺少左线拖动步骤")
        self.assertIsNotNone(self.walk_right_target, "columnSteps 缺少右线拖动步骤")
        self.assertLessEqual(abs(self.walk_left_target - 360), 2, f"走查左线目标应约 360，实际解析为 {self.walk_left_target}")
        self.assertLessEqual(abs(self.walk_right_target - 500), 2, f"走查右线目标应约 500，实际解析为 {self.walk_right_target}")

        self._assert_row(self.issue3, "走查拖左线", nav=360, kept_nav=360, drags=1, hit="是")
        row = self.issue3["走查拖右线"]
        self.assertGreaterEqual(row[1], 438, f"走查窗口宽 {int(self.walk_width)} 拖右线后中间宽度被挤破 440：{row}")
        self._assert_row(
            self.issue3, "走查拖右线",
            nav=360, right=500, kept_nav=360, kept_right=500, drags=2, hit="是",
        )


if __name__ == "__main__":
    unittest.main()
