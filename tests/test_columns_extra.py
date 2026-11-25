"""补充验收：左栏能收起、三栏能拖宽窄（CARD-026 补测）。

1. 三栏的摆放、抓取范围、防抖、防越界、拖动中途 layout() 不跳回与自动 resize：
   通过把 Columns.swift 与 tests/columns_extra.swift 一起编译运行，在真实 NSSplitView + WKWebView 里验证。
2. 窗口外壳 main.swift 的接线、按钮位置、菜单顺序、自测防写入与 --walk-columns 走查：
   因 main.swift 依赖 SwiftTerm 并需启动真实 Mac 桌面窗口，单元测试里无法直接实例化 AppDelegate 运行，
   故此部分采用结构化源码核对，真实窗口交互由 --walk-columns 走查验证。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent"
COLUMNS = SOURCES / "Columns.swift"
MAIN = SOURCES / "main.swift"


def _block_after(text: str, marker: str) -> str:
    """从 marker 开始，返回紧随其后的完整花括号块（含嵌套）。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    open_idx = text.find("{", idx)
    if open_idx < 0:
        return ""
    depth = 0
    for pos in range(open_idx, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx:pos + 1]
    return ""


class ColumnsRuntimeExtraTests(unittest.TestCase):
    """基于真实 AppKit NSSplitView 实跑 Columns.swift 的补充边界验收。"""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("swiftc"):
            raise unittest.SkipTest("本机没有 swiftc")
        if not COLUMNS.is_file():
            raise AssertionError("缺 desktop/mac/Sources/SuperAgent/Columns.swift")
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main.swift"
            main.write_text((HERE / "columns_extra.swift").read_text(encoding="utf-8"), encoding="utf-8")
            built = subprocess.run(
                ["swiftc", "-module-cache-path", d + "/cache", "-o", d + "/extra_bench", str(COLUMNS), str(main)],
                capture_output=True, text=True, timeout=240,
            )
            if built.returncode != 0:
                raise AssertionError("Columns.swift 与补充实跑程序编译不过：\n" + built.stderr)
            out = subprocess.run([d + "/extra_bench"], capture_output=True, text=True, timeout=60)
        if out.stdout.strip() == "NOSCREEN":
            raise unittest.SkipTest("这个环境没有屏幕，跑不了窗口")
        if out.returncode != 0:
            raise AssertionError("补充实跑程序运行失败：\n" + out.stdout + out.stderr)
        cls.rows = {}
        for line in out.stdout.splitlines():
            parts = line.split("|")
            if len(parts) == 9:
                cls.rows[parts[0]] = [int(x) for x in parts[1:7]] + [parts[7], parts[8]]

    def near(self, name, nav=None, center=None, right=None, kept_nav=None, kept_right=None,
             drags=None, hit=None, note=None):
        self.assertIn(name, self.rows, f"缺少实跑输出步骤：{name}")
        row = self.rows[name]
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
        if note is not None:
            self.assertEqual(row[7], note, f"{name}：附注校验失败（整行 {row}）")

    def test_columns_swift_only_imports_appkit_and_meets_structure_contract(self):
        src = COLUMNS.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*import\s+([A-Za-z0-9_]+)", src, re.M)
        self.assertEqual(imports, ["AppKit"], f"Columns.swift 只能 import AppKit，实际导入：{imports}")
        self.near("结构契约", nav=264, center=762, right=372, kept_nav=264, kept_right=372, drags=0, note="STRUCT_OK")

    def test_divider_hit_area_both_sides_and_outside_misses(self):
        self.near("左线左偏3点可拖", nav=300, right=372, kept_nav=300, kept_right=372, drags=1, hit="是")
        self.near("左线右偏3点可拖", nav=330, right=372, kept_nav=330, kept_right=372, drags=2, hit="是")
        self.near("右线左偏3点可拖", nav=330, right=420, kept_nav=330, kept_right=420, drags=3, hit="是")
        self.near("右线右偏3点可拖", nav=330, right=460, kept_nav=330, kept_right=460, drags=4, hit="是")

        self.near("左线外10点不命中", nav=330, right=460, kept_nav=330, kept_right=460, drags=4, hit="否")
        self.near("左线右外10点不命中", nav=330, right=460, kept_nav=330, kept_right=460, drags=4, hit="否")
        self.near("右线左外10点不命中", nav=330, right=460, kept_nav=330, kept_right=460, drags=4, hit="否")
        self.near("右线右外10点不命中", nav=330, right=460, kept_nav=330, kept_right=460, drags=4, hit="否")

        self.near("双隐藏左边缘不命中", nav=0, center=1400, right=0, kept_nav=330, kept_right=460, drags=4, hit="否")
        self.near("双隐藏右边缘不命中", nav=0, center=1400, right=0, kept_nav=330, kept_right=460, drags=4, hit="否")

    def test_tiny_jitter_and_clamped_no_op_drags_do_not_commit(self):
        self.near("左线抖动0.2点不算拖", nav=300, right=400, kept_nav=300, kept_right=400, drags=0, hit="是")
        self.near("右线抖动0.2点不算拖", nav=300, right=400, kept_nav=300, kept_right=400, drags=0, hit="是")

        self.near("左栏已在上限往右拖不动", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=0, hit="是")
        self.near("右栏已在上限往左拖不动", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=0, hit="是")
        self.near("双上限1400左线往右拖不动", nav=420, center=440, right=538, kept_nav=420, kept_right=640, drags=0, hit="是")
        self.near("双上限1400右线往左拖不动", nav=420, center=440, right=538, kept_nav=420, kept_right=640, drags=0, hit="是")
        self.near("左栏已在下限往左拖不动", nav=200, right=300, kept_nav=200, kept_right=300, drags=0, hit="是")
        self.near("右栏已在下限往右拖不动", nav=200, right=300, kept_nav=200, kept_right=300, drags=0, hit="是")

        self.near("挤窄时右线往右拖不动不覆写", nav=258, center=440, right=300, kept_nav=420, kept_right=640, drags=0, hit="是")
        self.near("挤窄时右线往左拖不动不覆写", nav=258, center=440, right=300, kept_nav=420, kept_right=640, drags=0, hit="是")

    def test_drag_respects_center_min_width_without_touching_other_column(self):
        # 1100 - 2(分隔线) - 320(左) - 440(中间下限) = 338
        self.near("窄三栏拖右不挤破中间", nav=320, center=440, right=338, kept_nav=320, kept_right=338, drags=1, hit="是")
        # 收左后 800 - 1(分隔线) - 440(中间下限) = 359
        self.near("收左窄窗拖右不挤破中间", nav=0, center=440, right=359, kept_nav=320, kept_right=359, drags=1, hit="是")
        # 无右后 760 - 1(分隔线) - 440(中间下限) = 319
        self.near("无右窄窗拖左不挤破中间", nav=319, center=440, right=0, kept_nav=319, kept_right=500, drags=1, hit="是")

    def test_mid_drag_layout_does_not_snap_back_and_callback_fires_on_release(self):
        self.near("拖左中途layout不跳回", nav=360, right=372, kept_nav=360, kept_right=372, drags=1, hit="是", note="MID_OK")
        self.near("拖右中途layout不跳回", nav=264, right=472, kept_nav=264, kept_right=472, drags=1, hit="是", note="MID_OK")

    def test_clamping_auto_resize_and_narrow_layout_rules(self):
        self.near("反向越界收口", nav=200, center=858, right=640, kept_nav=200, kept_right=640, drags=0)
        self.near("合法下限原样保留", nav=200, center=898, right=300, kept_nav=200, kept_right=300, drags=0)
        self.near("合法上限原样保留", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=0)

        self.near("自动resize挤窄不改记忆", nav=300, center=440, right=358, kept_nav=300, kept_right=500, drags=0)
        self.near("自动resize放宽恢复", nav=300, center=598, right=500, kept_nav=300, kept_right=500, drags=0)

        self.near("收左窄窗800挤右栏", nav=0, center=440, right=359, kept_nav=300, kept_right=640, drags=0)
        self.near("收左放宽1200恢复右栏", nav=0, center=559, right=640, kept_nav=300, kept_right=640, drags=0)

        self.near("窄窗左栏不超过记住宽度", nav=260, center=499, right=0, kept_nav=260, kept_right=500, drags=0)

        self.near("三栏极限942压到下限", nav=200, center=440, right=300, kept_nav=420, kept_right=640, drags=0)
        self.near("三栏极限后放宽1700恢复", nav=420, center=638, right=640, kept_nav=420, kept_right=640, drags=0)


class MainSwiftWiringExtraTests(unittest.TestCase):
    """核对 main.swift 接线、状态持久化保护、按钮/菜单/走查与“明确不做什么”底线。

    main.swift 依赖 SwiftTerm 且需要真实 Mac 窗口，单元测试里跑不起来，只能做结构化源码核对。
    """

    def setUp(self):
        self.src = MAIN.read_text(encoding="utf-8")

    def _has(self, needle: str, haystack: str, msg: str):
        self.assertTrue(needle in haystack, msg)

    def _lacks(self, needle: str, haystack: str, msg: str):
        self.assertFalse(needle in haystack, msg)

    def _matches(self, pattern: str, haystack: str, msg: str):
        self.assertIsNotNone(re.search(pattern, haystack, re.M), msg)

    def _not_matches(self, pattern: str, haystack: str, msg: str):
        self.assertIsNone(re.search(pattern, haystack, re.M), msg)

    def test_old_split_logic_completely_moved_out(self):
        for removed in (
            "NSSplitViewDelegate",
            "split.delegate = self",
            "let navWidthRange",
            "let rightWidthRange",
            "let centerMinWidth",
            "constrainMaxCoordinate",
            "resizeSubviewsWithOldSize",
        ):
            self._lacks(removed, self.src, f"已挪进 Columns.swift 的旧代码不应留在 main.swift：{removed}")

        for kept in ("threeColumnWidth", "navWidthKey", "rightWidthKey", "rightOpenKey"):
            self._has(kept, self.src, f"应留在 main.swift 的常量缺失：{kept}")

        layout_body = _block_after(self.src, "func layoutColumns(")
        self.assertTrue(layout_body, "main.swift 应保留 func layoutColumns()")
        self._has("columns.layout()", layout_body, "layoutColumns() 应只委托给 columns.layout()")
        self._lacks("navBox.frame =", layout_body, "layoutColumns() 不应再手算 navBox.frame")
        self._lacks("leftBox.frame =", layout_body, "layoutColumns() 不应再手算 leftBox.frame")
        self._lacks("rightBox.frame =", layout_body, "layoutColumns() 不应再手算 rightBox.frame")

    def test_columns_ownership_defaults_and_selftest_guard_on_drag_save(self):
        self._has("var columns: ThreeColumns!", self.src, "AppDelegate 应新增 var columns: ThreeColumns!")
        self._not_matches(
            r"^\s*var navWidth\s*:\s*CGFloat\s*=", self.src,
            "navWidth 应从 columns 取，不再在 AppDelegate 里另存一份",
        )
        self._not_matches(
            r"^\s*var rightWidth\s*:\s*CGFloat\s*=", self.src,
            "rightWidth 应从 columns 取，不再在 AppDelegate 里另存一份",
        )

        build_body = _block_after(self.src, "func buildWindow(")
        self.assertTrue(build_body, "缺少 func buildWindow()")
        self._has("264", self.src, "从没存过宽度的老用户默认左宽应仍是 264")
        self._has("372", self.src, "从没存过宽度的老用户默认右宽应仍是 372")
        self._has("navWidthKey", build_body, "buildWindow 应读取保存的左栏宽度")
        self._has("rightWidthKey", build_body, "buildWindow 应读取保存的右栏宽度")
        self._has("ThreeColumns(", build_body, "buildWindow 里应创建 ThreeColumns")
        self._has("window.delegate = self", build_body, "buildWindow 应设置 window.delegate = self")
        self.assertLess(
            build_body.index("ThreeColumns("),
            build_body.index("window.delegate = self"),
            "ThreeColumns 必须在 window.delegate = self 之前建好",
        )

        drag_cb = _block_after(build_body, "onUserDrag")
        self.assertTrue(drag_cb, "buildWindow 里应挂上 columns.onUserDrag")
        self._has("navWidthKey", drag_cb, "onUserDrag 应保存左栏宽度")
        self._has("rightWidthKey", drag_cb, "onUserDrag 应保存右栏宽度")
        self._matches(
            r"!(?:self\.)?selfTest", drag_cb,
            "onUserDrag 保存宽度前必须检查 !selfTest，避免自测污染用户设置",
        )

    def test_left_toggle_state_buttons_and_back_to_chat_offset(self):
        self._matches(r"\bvar navWanted\s*=\s*true\b", self.src, "默认左栏应展开（var navWanted = true）")

        set_body = _block_after(self.src, "func setNavWanted(")
        self.assertTrue(set_body, "缺少 func setNavWanted(_ wanted: Bool)")
        self._has("syncNavColumn()", set_body, "setNavWanted 应调用 syncNavColumn()")
        self._has("closeProjectMenu", set_body, "收起左栏时应关掉项目浮层")
        self._matches(
            r"if\s+(?:!(?:wanted|navWanted)|(?:wanted|navWanted)\s*==\s*false)\b[\s\S]*closeProjectMenu",
            set_body,
            "应仅在收起左栏时调用 closeProjectMenu()",
        )

        sync_body = _block_after(self.src, "func syncNavColumn(")
        self.assertTrue(sync_body, "缺少 func syncNavColumn()")
        self._has("navBox.isHidden", sync_body, "syncNavColumn 应切换 navBox.isHidden")
        self._has("navShow.isHidden", sync_body, "syncNavColumn 应控制中间顶部展开按钮显示隐藏")
        self._has("114", sync_body, "左栏收起时返回聊天应往右让到 114")
        self._has("16", sync_body, "左栏展开时返回聊天应回到 16")
        self._has("layoutColumns()", sync_body, "syncNavColumn 应调用 layoutColumns()")

        toggle_body = _block_after(self.src, "func toggleNav(")
        self.assertTrue(toggle_body, "缺少 @objc func toggleNav(_ sender: Any?)")
        self._has("setNavWanted(!navWanted)", toggle_body, "toggleNav 应切换 navWanted 状态")

        build_body = _block_after(self.src, "func buildWindow(")
        self._has("syncNavColumn()", build_body, "建窗口时要调用 syncNavColumn() 以恢复上次收起状态")
        self.assertLess(
            build_body.index("syncNavColumn()"),
            build_body.index("window.delegate = self"),
            "syncNavColumn() 应在 window.delegate = self 之前调用",
        )
        self._has("navTop.addSubview(navToggle)", build_body, "navToggle 应放在左栏顶部细栏 navTop 里")
        self._matches(
            r"(?:navToggle\.trailingAnchor\.constraint\(equalTo:\s*navTop\.trailingAnchor,\s*constant:\s*-8\)"
            r"|navTop\.trailingAnchor\.constraint\(equalTo:\s*navToggle\.trailingAnchor,\s*constant:\s*8\))",
            build_body,
            "navToggle 应离 navTop 右边 8 点",
        )

        top_bar = _block_after(self.src, "func buildTopBar(")
        self.assertTrue(top_bar, "缺少 func buildTopBar()")
        self._has("bar.addSubview(navShow)", top_bar, "navShow 应放在中间顶部细栏 bar 里")
        self._matches(
            r"(?:navShow\.leadingAnchor\.constraint\(equalTo:\s*bar\.leadingAnchor,\s*constant:\s*78\)"
            r"|bar\.leadingAnchor\.constraint\(equalTo:\s*navShow\.leadingAnchor,\s*constant:\s*-78\))",
            top_bar,
            "navShow 应离中间顶部左边 78 点（让开红黄绿圆点）",
        )

    def test_panel_menu_order_and_existing_shortcuts(self):
        left_item = '("收起或展开左栏", #selector(toggleNav(_:)), "b")'
        right_item = '("收起或展开右栏", #selector(toggleRight(_:)), "\\\\")'
        self._has(left_item, self.src, "菜单“面板”缺少“收起或展开左栏”（⌘B）")
        self._has(right_item, self.src, "菜单“面板”原有“收起或展开右栏”（⌘\\）不能删改")
        self.assertLess(
            self.src.index(left_item),
            self.src.index(right_item),
            "“收起或展开左栏”应放在“收起或展开右栏”上面",
        )

    def test_walk_columns_steps_report_and_no_cursor_warp(self):
        header = "\n".join(self.src.splitlines()[:60])
        self._has("--walk-columns", header, "main.swift 顶部参数说明应补上 --walk-columns")

        walk_body = _block_after(self.src, "func scheduleWalk(")
        self.assertTrue(walk_body, "缺少 func scheduleWalk(into:)")
        self._has("--walk-columns", walk_body, "scheduleWalk 应接入 --walk-columns 分支")

        report_body = _block_after(walk_body, "func report(")
        self.assertTrue(report_body, "scheduleWalk 内缺少 func report(_ name: String)")
        self.assertTrue(
            "navBox.isHidden" in report_body or "navWanted" in report_body,
            "walk 的 report 应记录左栏开没开（navBox.isHidden 或 navWanted）",
        )
        for field in ("navToggle", "navShow", "backToChat", "navWidth", "rightWidth"):
            self._has(field, report_body, f"walk 的 report 应记录 {field}")

        for action in ("navToggle.performClick", "navShow.performClick", "toggleNav(nil)", "postEvent", "mouseDown"):
            self._has(action, walk_body, f"--walk-columns 走查缺少关键动作：{action}")

        for forbidden in ("CGWarpMouseCursorPosition", "CGDisplayMoveCursorToPoint"):
            self._lacks(forbidden, self.src, f"走查拖动不应移动用户真实鼠标：{forbidden}")

    def test_out_of_scope_and_existing_behaviors_preserved(self):
        # 1. 不改窗口最小尺寸
        self._has("window.minSize = NSSize(width: 760, height: 480)", self.src, "不应修改窗口最小尺寸")
        # 2. 不改右栏显示时机
        sync_right = _block_after(self.src, "func syncRight(")
        self._has("selectedCard != nil && rightWanted && leftView != .config && window.frame.width >= threeColumnWidth", sync_right, "右栏显示条件不应改变")
        # 3. 进设置页不改左栏开关状态（只收右栏，左栏保持原样）
        show_left = _block_after(self.src, "func showLeft(")
        self._lacks("setNavWanted", show_left, "切换设置页/对话视图不应改动左栏收起状态")
        self._lacks("navWanted =", show_left, "切换设置页/对话视图不应改动 navWanted")
        # 4. 不加动画（Columns.swift 和左栏收起切换代码里不加动画）
        for label, snippet in (
            ("setNavWanted", _block_after(self.src, "func setNavWanted(")),
            ("syncNavColumn", _block_after(self.src, "func syncNavColumn(")),
        ):
            self._lacks(".animator()", snippet, f"{label} 不应加动画")
            self._lacks("NSAnimationContext", snippet, f"{label} 不应加动画")
        if COLUMNS.is_file():
            col_src = COLUMNS.read_text(encoding="utf-8")
            self._lacks(".animator()", col_src, "Columns.swift 不应加动画")
            self._lacks("NSAnimationContext", col_src, "Columns.swift 不应加动画")


if __name__ == "__main__":
    unittest.main()
