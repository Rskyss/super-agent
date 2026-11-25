// 复查退回问题专项实跑小程序（CARD-026）：只和 desktop/mac/Sources/SuperAgent/Columns.swift 一起编译运行。
// 覆盖 3 条复查退回问题：
// 1. ISSUE1：左栏展开/收起时，点击中间顶部右栏收起按钮、右栏内容、左栏按钮都能精准命中，不落偏、不落空，分隔线仍可抓。
// 2. ISSUE2：宽窗口把左右两栏都拖到最宽（420/640）后，把窗口缩到 1400 / 1100，仍保住中间 440 点且先缩右栏，放宽后恢复。
// 3. ISSUE3：按 --walk-columns 走查窗口宽度，依次将左线拖到 360、右线拖到离右边 500，核对右栏能否真实走到约 500 点。
import AppKit
import WebKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
if NSScreen.screens.isEmpty { print("NOSCREEN"); exit(0) }

let args = CommandLine.arguments
let walkSimWidth: CGFloat = args.count > 1 ? CGFloat(Double(args[1]) ?? 1280) : 1280
let walkLeftTarget: CGFloat = args.count > 2 ? CGFloat(Double(args[2]) ?? 360) : 360
let walkRightTarget: CGFloat = args.count > 3 ? CGFloat(Double(args[3]) ?? 500) : 500

// MARK: - 问题 1：点击命中坐标换算（ThreeColumnSplit.hitTest）
do {
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .resizable], backing: .buffered, defer: false)
    let navBox = NSView()
    let centerBox = NSView()
    let rightBox = NSView()

    // 照 main.swift 摆放左栏顶部按钮、中间顶部右栏收起按钮、中栏内容、右栏顶部和主体内容
    let navTop = NSView()
    navTop.translatesAutoresizingMaskIntoConstraints = false
    navBox.addSubview(navTop)
    let navToggle = NSButton(title: "N", target: nil, action: nil)
    navToggle.translatesAutoresizingMaskIntoConstraints = false
    navTop.addSubview(navToggle)
    NSLayoutConstraint.activate([
        navTop.leadingAnchor.constraint(equalTo: navBox.leadingAnchor),
        navTop.trailingAnchor.constraint(equalTo: navBox.trailingAnchor),
        navTop.topAnchor.constraint(equalTo: navBox.topAnchor),
        navTop.heightAnchor.constraint(equalToConstant: 38),
        navToggle.trailingAnchor.constraint(equalTo: navTop.trailingAnchor, constant: -8),
        navToggle.centerYAnchor.constraint(equalTo: navTop.centerYAnchor),
        navToggle.widthAnchor.constraint(equalToConstant: 28),
        navToggle.heightAnchor.constraint(equalToConstant: 26),
    ])

    let centerTop = NSView()
    centerTop.translatesAutoresizingMaskIntoConstraints = false
    centerBox.addSubview(centerTop)
    let rightToggle = NSButton(title: "R", target: nil, action: nil)
    rightToggle.translatesAutoresizingMaskIntoConstraints = false
    centerTop.addSubview(rightToggle)
    let centerLeftBtn = NSButton(title: "CL", target: nil, action: nil)
    centerLeftBtn.translatesAutoresizingMaskIntoConstraints = false
    centerBox.addSubview(centerLeftBtn)
    NSLayoutConstraint.activate([
        centerTop.leadingAnchor.constraint(equalTo: centerBox.leadingAnchor),
        centerTop.trailingAnchor.constraint(equalTo: centerBox.trailingAnchor),
        centerTop.topAnchor.constraint(equalTo: centerBox.topAnchor),
        centerTop.heightAnchor.constraint(equalToConstant: 38),
        rightToggle.trailingAnchor.constraint(equalTo: centerTop.trailingAnchor, constant: -10),
        rightToggle.centerYAnchor.constraint(equalTo: centerTop.centerYAnchor),
        rightToggle.widthAnchor.constraint(equalToConstant: 28),
        rightToggle.heightAnchor.constraint(equalToConstant: 26),
        centerLeftBtn.leadingAnchor.constraint(equalTo: centerBox.leadingAnchor, constant: 24),
        centerLeftBtn.topAnchor.constraint(equalTo: centerTop.bottomAnchor, constant: 80),
        centerLeftBtn.widthAnchor.constraint(equalToConstant: 80),
        centerLeftBtn.heightAnchor.constraint(equalToConstant: 32),
    ])

    let rightTopBtn = NSButton(title: "RT", target: nil, action: nil)
    rightTopBtn.translatesAutoresizingMaskIntoConstraints = false
    rightBox.addSubview(rightTopBtn)
    let rightCenterBtn = NSButton(title: "RC", target: nil, action: nil)
    rightCenterBtn.translatesAutoresizingMaskIntoConstraints = false
    rightBox.addSubview(rightCenterBtn)
    NSLayoutConstraint.activate([
        rightTopBtn.leadingAnchor.constraint(equalTo: rightBox.leadingAnchor, constant: 20),
        rightTopBtn.topAnchor.constraint(equalTo: rightBox.topAnchor, constant: 8),
        rightTopBtn.widthAnchor.constraint(equalToConstant: 60),
        rightTopBtn.heightAnchor.constraint(equalToConstant: 24),
        rightCenterBtn.centerXAnchor.constraint(equalTo: rightBox.centerXAnchor),
        rightCenterBtn.centerYAnchor.constraint(equalTo: rightBox.centerYAnchor),
        rightCenterBtn.widthAnchor.constraint(equalToConstant: 120),
        rightCenterBtn.heightAnchor.constraint(equalToConstant: 40),
    ])

    let columns = ThreeColumns(nav: navBox, center: centerBox, right: rightBox, navWidth: 264, rightWidth: 372)
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
    root.layoutSubtreeIfNeeded()

    func describeHit(_ hit: NSView?, expected: NSView, expectedName: String) -> String {
        guard let h = hit else { return "nil" }
        if h === expected || h.isDescendant(of: expected) { return expectedName }
        if h === columns.split { return "split" }
        if h.isDescendant(of: navBox) { return "navBox:\(type(of: h))" }
        if h.isDescendant(of: centerBox) { return "centerBox:\(type(of: h))" }
        if h.isDescendant(of: rightBox) { return "rightBox:\(type(of: h))" }
        return "\(type(of: h))"
    }

    func checkHit(_ name: String, target: NSView) {
        let mid = NSPoint(x: target.bounds.midX, y: target.bounds.midY)
        let ptInSplitSuper = target.convert(mid, to: columns.split.superview)
        let hit = columns.split.hitTest(ptInSplitSuper)
        let ok = hit != nil && (hit === target || hit!.isDescendant(of: target))
        let got = describeHit(hit, expected: target, expectedName: name)
        print("ISSUE1|\(name)|\(ok ? "OK" : "FAIL")|pt=(\(Int(ptInSplitSuper.x.rounded())),\(Int(ptInSplitSuper.y.rounded()))) got=\(got)")
    }

    func checkDivider(_ name: String, x: CGFloat) {
        let hit = columns.split.hitTest(NSPoint(x: x, y: 400))
        let ok = hit === columns.split
        let got = hit.map { String(describing: type(of: $0)) } ?? "nil"
        print("ISSUE1|\(name)|\(ok ? "OK" : "FAIL")|x=\(Int(x.rounded())) got=\(got)")
    }

    // 三栏全开（左栏展开）
    checkHit("三栏_右栏收起按钮", target: rightToggle)
    checkHit("三栏_右栏顶部按钮", target: rightTopBtn)
    checkHit("三栏_右栏中间内容", target: rightCenterBtn)
    checkHit("三栏_中栏偏左内容", target: centerLeftBtn)
    checkHit("三栏_左栏收起按钮", target: navToggle)
    checkDivider("三栏_左分隔线", x: navBox.frame.maxX + 0.5)
    checkDivider("三栏_右分隔线", x: rightBox.frame.minX - 0.5)

    // 收起左栏后（中右两栏）
    navBox.isHidden = true
    columns.layout()
    root.layoutSubtreeIfNeeded()
    checkHit("收左_右栏收起按钮", target: rightToggle)
    checkHit("收左_右栏中间内容", target: rightCenterBtn)
    checkDivider("收左_右分隔线", x: rightBox.frame.minX - 0.5)
}

// MARK: - 通用拖放测试台（用于问题 2 和问题 3）
final class ReworkBench {
    let window: NSWindow
    let columns: ThreeColumns
    var drags = 0
    var hitDivider = "-"

    init(width: CGFloat, nav: CGFloat = 264, right: CGFloat = 372, showRight: Bool = true) {
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

    func resizeWindow(_ value: CGFloat) {
        window.setContentSize(NSSize(width: value, height: 800))
        window.contentView!.layoutSubtreeIfNeeded()
        columns.layout()
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

    func log(tag: String, _ name: String) {
        let c = columns
        let n = c.nav.isHidden ? 0 : Int(c.nav.frame.width.rounded())
        let r = c.right.isHidden ? 0 : Int(c.right.frame.width.rounded())
        print([tag, name, "\(n)", "\(Int(c.center.frame.width.rounded()))", "\(r)",
               "\(Int(c.navWidth.rounded()))", "\(Int(c.rightWidth.rounded()))",
               "\(drags)", hitDivider].joined(separator: "|"))
        hitDivider = "-"
    }
}

// MARK: - 问题 2：宽窗口双栏拉到最宽后缩窗，仍须保住中间 440 点且先让右栏
do {
    let b = ReworkBench(width: 1700, nav: 264, right: 372, showRight: true)
    b.drag(b.navLine, 700)
    b.drag(b.rightLine, 100)
    b.log(tag: "ISSUE2", "双最宽1700")

    b.resizeWindow(1400)
    b.log(tag: "ISSUE2", "双最宽缩到1400")

    b.resizeWindow(1100)
    b.log(tag: "ISSUE2", "双最宽缩到1100")

    b.resizeWindow(1700)
    b.log(tag: "ISSUE2", "双最宽放回1700")
}

// MARK: - 问题 3：按 --walk-columns 的走查窗口宽度依次拖左线和右线
do {
    let b = ReworkBench(width: walkSimWidth, nav: 264, right: 372, showRight: false)
    b.show(right: true)
    b.drag(b.navLine, walkLeftTarget)
    b.log(tag: "ISSUE3", "走查拖左线")

    // 与 main.swift 中 ("右线拖到离右边500", { dragDivider(rightLine(), self.split.bounds.width - 501) }) 保持一致
    b.drag(b.rightLine, b.columns.split.bounds.width - (walkRightTarget + 1))
    b.log(tag: "ISSUE3", "走查拖右线")
}
