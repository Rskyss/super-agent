// 三栏补充验收小程序：只和 desktop/mac/Sources/SuperAgent/Columns.swift 一起编译运行，
// 专门补测基础验收未覆盖的边界：结构契约、左右各 4 点抓取与界外防误触、微小抖动和贴边拖不动、
// 拖右线保护中间宽度与左宽、拖动中途 layout() 不跳回且松手后才回调、自动 resize 与边界摆放。
// 每一步输出一行：步骤|左|中|右|记住的左|记住的右|onUserDrag 次数|点到的是不是分隔线|附注
import AppKit
import WebKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
if NSScreen.screens.isEmpty { print("NOSCREEN"); exit(0) }

final class ExtraBench {
    let window: NSWindow
    let columns: ThreeColumns
    var drags = 0
    var hitDivider = "-"
    var note = ""

    init(width: CGFloat = 1400, nav: CGFloat = 264, right: CGFloat = 372, showRight: Bool = false) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 800),
                          styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        columns = ThreeColumns(nav: WKWebView(), center: WKWebView(), right: WKWebView(), navWidth: nav, rightWidth: right)
        columns.onUserDrag = { [unowned self] in self.drags += 1 }
        columns.right.isHidden = !showRight
        let root = NSView()
        window.contentView = root
        columns.split.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(columns.split)
        NSLayoutConstraint.activate([
            columns.split.topAnchor.constraint(equalTo: root.topAnchor),
            columns.split.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            columns.split.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            columns.split.trailingAnchor.constraint(equalTo: root.trailingAnchor),
        ])
        root.layoutSubtreeIfNeeded()
        columns.layout()
    }

    func resizeWindow(_ value: CGFloat, manualLayout: Bool = true) {
        window.setContentSize(NSSize(width: value, height: 800))
        window.contentView!.layoutSubtreeIfNeeded()
        if manualLayout { columns.layout() }
    }

    func show(nav: Bool? = nil, right: Bool? = nil) {
        if let nav = nav { columns.nav.isHidden = !nav }
        if let right = right { columns.right.isHidden = !right }
        columns.layout()
    }

    func event(_ type: NSEvent.EventType, _ x: CGFloat) -> NSEvent {
        NSEvent.mouseEvent(with: type, location: NSPoint(x: x, y: 400), modifierFlags: [],
                           timestamp: ProcessInfo.processInfo.systemUptime,
                           windowNumber: window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
    }

    func drag(_ from: CGFloat, _ to: CGFloat, steps: Int = 5) {
        guard window.contentView!.hitTest(NSPoint(x: from, y: 400)) === columns.split else { hitDivider = "否"; return }
        hitDivider = "是"
        for k in 1...steps {
            app.postEvent(event(.leftMouseDragged, from + (to - from) * CGFloat(k) / CGFloat(steps)), atStart: false)
        }
        app.postEvent(event(.leftMouseUp, to), atStart: false)
        columns.split.mouseDown(with: event(.leftMouseDown, from))
    }

    var navLine: CGFloat { columns.nav.frame.maxX + 0.5 }
    var rightLine: CGFloat { columns.right.frame.minX - 0.5 }

    func log(_ name: String) {
        let c = columns
        let n = c.nav.isHidden ? 0 : Int(c.nav.frame.width.rounded())
        let r = c.right.isHidden ? 0 : Int(c.right.frame.width.rounded())
        print([name, "\(n)", "\(Int(c.center.frame.width.rounded()))", "\(r)",
               "\(Int(c.navWidth.rounded()))", "\(Int(c.rightWidth.rounded()))",
               "\(drags)", hitDivider, note].joined(separator: "|"))
        hitDivider = "-"
        note = ""
    }
}

// 1. 结构契约与默认参数
do {
    let b = ExtraBench(showRight: true)
    let s = b.columns.split
    let arranged = s.arrangedSubviews
    let orderOK = arranged.count == 3 && arranged[0] === b.columns.nav
        && arranged[1] === b.columns.center && arranged[2] === b.columns.right
    let propsOK = s.isVertical && s.dividerStyle == .thin && abs(s.dividerThickness - 1) < 0.01
        && !s.arrangesAllSubviews && (s.delegate === b.columns)
    b.note = (orderOK && propsOK) ? "STRUCT_OK" : "STRUCT_BAD(\(orderOK),\(propsOK),count=\(arranged.count))"
    b.log("结构契约")
}

// 2. 抓取范围（左右各 4 点）与界外防误触（拖动终点补上按下时的 3 点偏移，使分隔线正好停在目标位置）
do {
    let b = ExtraBench(width: 1400, nav: 264, right: 372, showRight: true)
    b.drag(b.navLine - 3, 300 - 3); b.log("左线左偏3点可拖")
    b.drag(b.navLine + 3, 330 + 3); b.log("左线右偏3点可拖")
    b.drag(b.rightLine - 3, 980 - 3); b.log("右线左偏3点可拖")
    b.drag(b.rightLine + 3, 940 + 3); b.log("右线右偏3点可拖")

    b.drag(b.navLine - 10, 220); b.log("左线外10点不命中")
    b.drag(b.navLine + 10, 380); b.log("左线右外10点不命中")
    b.drag(b.rightLine - 10, 800); b.log("右线左外10点不命中")
    b.drag(b.rightLine + 10, 1100); b.log("右线右外10点不命中")

    b.show(nav: false, right: false)
    b.drag(2, 300); b.log("双隐藏左边缘不命中")
    b.drag(1398, 1000); b.log("双隐藏右边缘不命中")
}

// 3. 微小抖动(<0.5点)与贴边拖不动：不记、不叫 onUserDrag
do {
    let b = ExtraBench(width: 1400, nav: 300, right: 400, showRight: true)
    b.drag(b.navLine, b.navLine + 0.2, steps: 2); b.log("左线抖动0.2点不算拖")
    b.drag(b.rightLine, b.rightLine - 0.2, steps: 2); b.log("右线抖动0.2点不算拖")

    // 至少 1502 点（420+440+640+2）才摆得下双上限加中间下限，用 1700 测双上限往外拖不动
    let bMax = ExtraBench(width: 1700, nav: 420, right: 640, showRight: true)
    bMax.drag(bMax.navLine, 700); bMax.log("左栏已在上限往右拖不动")
    bMax.drag(bMax.rightLine, 100); bMax.log("右栏已在上限往左拖不动")

    // 1400 点摆不下 420+440+640+2，必须保住中间 440、先把右栏缩到 538，记住的右栏仍为 640 且拖不动
    let bMaxNarrow = ExtraBench(width: 1400, nav: 420, right: 640, showRight: true)
    bMaxNarrow.drag(bMaxNarrow.navLine, 700); bMaxNarrow.log("双上限1400左线往右拖不动")
    bMaxNarrow.drag(bMaxNarrow.rightLine, 100); bMaxNarrow.log("双上限1400右线往左拖不动")

    let bMin = ExtraBench(width: 1400, nav: 200, right: 300, showRight: true)
    bMin.drag(bMin.navLine, 50); bMin.log("左栏已在下限往左拖不动")
    bMin.drag(bMin.rightLine, 1390); bMin.log("右栏已在下限往右拖不动")

    // 窗口 1000 被挤窄时，拖右线（贴下限往右拖不动、顶着中间440往左拖不动）都不能覆写记住的 640
    let bSq = ExtraBench(width: 1000, nav: 420, right: 640, showRight: true)
    bSq.drag(bSq.rightLine, 990); bSq.log("挤窄时右线往右拖不动不覆写")
    bSq.drag(bSq.rightLine, 200); bSq.log("挤窄时右线往左拖不动不覆写")
}

// 4. 拖右线保护中间最小宽度 440 且不改记住的左宽；单边显示窄窗拖线保护中间
do {
    let b = ExtraBench(width: 1100, nav: 320, right: 300, showRight: true)
    b.drag(b.rightLine, 400); b.log("窄三栏拖右不挤破中间")

    let bNoNav = ExtraBench(width: 800, nav: 320, right: 320, showRight: true)
    bNoNav.show(nav: false)
    bNoNav.drag(bNoNav.rightLine, 200); bNoNav.log("收左窄窗拖右不挤破中间")

    let bNoRight = ExtraBench(width: 760, nav: 260, right: 500, showRight: false)
    bNoRight.drag(bNoRight.navLine, 600); bNoRight.log("无右窄窗拖左不挤破中间")
}

// 5. 拖动中途外部触发 layout() 不跳回，且松手前不触发 onUserDrag、松手后触发一次
// 监听 NSSplitView.didResizeSubviewsNotification（注册在 ThreeColumns.init 之后，确保在 delegate 回调之后运行）
do {
    let b = ExtraBench(width: 1400, nav: 264, right: 372, showRight: true)
    let navView = b.columns.nav
    let startNav = navView.frame.width
    var midFired = false
    var midDragsBeforeRelease = -1
    var midBeforeLayout: CGFloat = -1
    var midAfterLayout: CGFloat = -1
    var midKeptNav: CGFloat = -1
    var midKeptRight: CGFloat = -1
    let tok = NotificationCenter.default.addObserver(forName: NSSplitView.didResizeSubviewsNotification, object: b.columns.split, queue: nil) { _ in
        guard !midFired, abs(navView.frame.width - startNav) > 15 else { return }
        midFired = true
        midDragsBeforeRelease = b.drags
        midBeforeLayout = navView.frame.width
        b.columns.layout()
        midAfterLayout = navView.frame.width
        midKeptNav = b.columns.navWidth
        midKeptRight = b.columns.rightWidth
    }
    b.drag(b.navLine, 360, steps: 8)
    NotificationCenter.default.removeObserver(tok)
    let noJump = midFired && midDragsBeforeRelease == 0
        && abs(midAfterLayout - midBeforeLayout) <= 2
        && abs(midKeptNav - midBeforeLayout) <= 2
        && abs(midKeptRight - 372) <= 2
    b.note = noJump
        ? "MID_OK"
        : "MID_BAD(fired=\(midFired),drags=\(midDragsBeforeRelease),before=\(Int(midBeforeLayout)),after=\(Int(midAfterLayout)),keptN=\(Int(midKeptNav)),keptR=\(Int(midKeptRight)))"
    b.log("拖左中途layout不跳回")
}

do {
    let b = ExtraBench(width: 1400, nav: 264, right: 372, showRight: true)
    let rightView = b.columns.right
    let startRight = rightView.frame.width
    var midFired = false
    var midDragsBeforeRelease = -1
    var midBeforeLayout: CGFloat = -1
    var midAfterLayout: CGFloat = -1
    var midKeptNav: CGFloat = -1
    var midKeptRight: CGFloat = -1
    let tok = NotificationCenter.default.addObserver(forName: NSSplitView.didResizeSubviewsNotification, object: b.columns.split, queue: nil) { _ in
        guard !midFired, abs(rightView.frame.width - startRight) > 15 else { return }
        midFired = true
        midDragsBeforeRelease = b.drags
        midBeforeLayout = rightView.frame.width
        b.columns.layout()
        midAfterLayout = rightView.frame.width
        midKeptNav = b.columns.navWidth
        midKeptRight = b.columns.rightWidth
    }
    b.drag(b.rightLine, b.rightLine - 100, steps: 8)
    NotificationCenter.default.removeObserver(tok)
    let noJump = midFired && midDragsBeforeRelease == 0
        && abs(midAfterLayout - midBeforeLayout) <= 2
        && abs(midKeptRight - midBeforeLayout) <= 2
        && abs(midKeptNav - 264) <= 2
    b.note = noJump
        ? "MID_OK"
        : "MID_BAD(fired=\(midFired),drags=\(midDragsBeforeRelease),before=\(Int(midBeforeLayout)),after=\(Int(midAfterLayout)),keptN=\(Int(midKeptNav)),keptR=\(Int(midKeptRight)))"
    b.log("拖右中途layout不跳回")
}

// 6. 边界摆放、反向越界收口、窗口自动 resize（不手动调 layout）
do {
    let bClamp = ExtraBench(width: 1700, nav: -20, right: 9999, showRight: true)
    bClamp.log("反向越界收口")

    let bMinEdge = ExtraBench(width: 1400, nav: 200, right: 300, showRight: true)
    bMinEdge.log("合法下限原样保留")

    let bMaxEdge = ExtraBench(width: 1700, nav: 420, right: 640, showRight: true)
    bMaxEdge.log("合法上限原样保留")

    // 不手动调 columns.layout()，靠 splitView(_:resizeSubviewsWithOldSize:) 自动重排
    let bAuto = ExtraBench(width: 1400, nav: 300, right: 500, showRight: true)
    bAuto.resizeWindow(1100, manualLayout: false); bAuto.log("自动resize挤窄不改记忆")
    bAuto.resizeWindow(1400, manualLayout: false); bAuto.log("自动resize放宽恢复")

    // 收左 + 窄窗 800 + 右栏记住 640：右栏挤到 359、中间 440，放宽恢复 640
    let bHideNavNarrow = ExtraBench(width: 800, nav: 300, right: 640, showRight: true)
    bHideNavNarrow.show(nav: false); bHideNavNarrow.log("收左窄窗800挤右栏")
    bHideNavNarrow.resizeWindow(1200); bHideNavNarrow.log("收左放宽1200恢复右栏")

    // 无右 + 窄窗 760 + 左栏记住 260：左栏实际宽度不超过记住的 260
    let bKeepNavCap = ExtraBench(width: 760, nav: 260, right: 500, showRight: false)
    bKeepNavCap.log("窄窗左栏不超过记住宽度")

    // 极窄下限 942（200+440+300+2）三栏全开，放宽 1700 全部恢复
    let bExactMin = ExtraBench(width: 942, nav: 420, right: 640, showRight: true)
    bExactMin.log("三栏极限942压到下限")
    bExactMin.resizeWindow(1700); bExactMin.log("三栏极限后放宽1700恢复")
}
