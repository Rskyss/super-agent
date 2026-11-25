// 三栏验收用的小程序：和 desktop/mac/Sources/SuperAgent/Columns.swift 一起编译，
// 用真的分隔视图、真的网页视图摆三栏；拖动是把按下、拖、松手的事件发给这个小程序自己的窗口，不动用户的鼠标，窗口不显示出来。
// 每一步输出一行：步骤|左|中|右|记住的左|记住的右|onUserDrag 次数|点到的是不是分隔线
import AppKit
import WebKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
if NSScreen.screens.isEmpty { print("NOSCREEN"); exit(0) }

final class Bench {
    let window: NSWindow, columns: ThreeColumns
    var drags = 0, hitDivider = "-"
    init(width: CGFloat = 1400, nav: CGFloat = 264, right: CGFloat = 372) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 800),
                          styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        columns = ThreeColumns(nav: WKWebView(), center: WKWebView(), right: WKWebView(), navWidth: nav, rightWidth: right)
        columns.onUserDrag = { [unowned self] in self.drags += 1 }
        columns.right.isHidden = true
        let root = NSView()
        window.contentView = root
        columns.split.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(columns.split)
        NSLayoutConstraint.activate([
            columns.split.topAnchor.constraint(equalTo: root.topAnchor), columns.split.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            columns.split.leadingAnchor.constraint(equalTo: root.leadingAnchor), columns.split.trailingAnchor.constraint(equalTo: root.trailingAnchor)])
        root.layoutSubtreeIfNeeded()
        columns.layout()
    }
    func width(_ value: CGFloat) {
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
        NSEvent.mouseEvent(with: type, location: NSPoint(x: x, y: 400), modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                           windowNumber: window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
    }
    func drag(_ from: CGFloat, _ to: CGFloat) {
        guard window.contentView!.hitTest(NSPoint(x: from, y: 400)) === columns.split else { hitDivider = "否"; return }
        hitDivider = "是"
        for k in 1...5 { app.postEvent(event(.leftMouseDragged, from + (to - from) * CGFloat(k) / 5), atStart: false) }
        app.postEvent(event(.leftMouseUp, to), atStart: false)
        columns.split.mouseDown(with: event(.leftMouseDown, from))
    }
    func click(_ x: CGFloat) { drag(x, x) }
    var navLine: CGFloat { columns.nav.frame.maxX + 0.5 }
    var rightLine: CGFloat { columns.right.frame.minX - 0.5 }
    func log(_ name: String) {
        let c = columns
        let n = c.nav.isHidden ? 0 : Int(c.nav.frame.width.rounded()), r = c.right.isHidden ? 0 : Int(c.right.frame.width.rounded())
        print([name, "\(n)", "\(Int(c.center.frame.width.rounded()))", "\(r)", "\(Int(c.navWidth.rounded()))", "\(Int(c.rightWidth.rounded()))",
               "\(drags)", hitDivider].joined(separator: "|"))
        hitDivider = "-"
    }
}

var b = Bench()
b.log("打开"); b.show(right: true); b.log("右栏出现")
b.drag(b.navLine, 320); b.log("左拖到320")
b.drag(b.navLine + 3, 360); b.log("偏3点也能拖")
b.click(b.navLine); b.log("只点不拖")
b.drag(b.navLine, 1300); b.log("左拖过头")
b.drag(b.navLine, 5); b.log("左拖到底")
b.drag(b.rightLine, 1000); b.log("右拖到1000")
b.drag(b.rightLine, 10); b.log("右拖过头")
b.drag(b.rightLine, 1395); b.log("右拖到底")

b = Bench(nav: 300, right: 500)
b.show(right: true); b.log("B开始")
b.width(1100); b.log("B窗口1100")
b.drag(b.navLine, 250); b.log("B挤着时拖左")
b.width(1400); b.log("B窗口放回")
b.show(nav: false); b.log("B收起左")
b.drag(2, 300); b.log("B收起后拖左边缘")
b.width(1000); b.log("B收起窗口1000"); b.width(1400)
b.drag(b.rightLine, 850); b.log("B收起后拖右")
b.show(nav: true); b.log("B展开左")
b.show(right: false); b.log("B右栏没了")
b.drag(1398, 1000); b.log("B没右栏拖右边缘")
b.show(nav: false); b.log("B两边都没有")
b.show(nav: true, right: true); b.log("B都回来")

b = Bench(width: 760, nav: 420, right: 640)
b.log("C窄窗口"); b.width(1000); b.show(right: true); b.log("C1000三栏")
b.drag(b.navLine, 900); b.log("C拖左不挤中间")
b.width(1700); b.log("C放宽到1700")

b = Bench(nav: 9999, right: 1)
b.width(1700); b.show(right: true); b.log("D存的数离谱")
