// 分隔线画在哪：和 desktop/mac/Sources/SuperAgent/Columns.swift 一起编译。
// 系统把分隔线画成分隔视图上单独的一层，程序自己摆完三栏要叫它跟着挪（CARD-026 装好后在真实窗口发现线留在旧位置）。
// 已知不足：左栏收起时系统仍在窗口最左边画 1 点极淡的线，和窗口边框重合、看不出来，这里不算。
// 输出：步骤|左线该在|右线该在|看得见的线实际在（逗号隔开）；这一栏没开，该在写 -
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
if NSScreen.screens.isEmpty { print("NOSCREEN"); exit(0) }

func pane() -> NSView { let v = NSView(); v.wantsLayer = true; return v }
let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 600), styleMask: [.titled, .resizable], backing: .buffered, defer: false)
let nav = NSVisualEffectView()
nav.blendingMode = .behindWindow
let c = ThreeColumns(nav: nav, center: pane(), right: pane(), navWidth: 264, rightWidth: 372)
c.right.isHidden = true
let root = NSView()
window.contentView = root
c.split.translatesAutoresizingMaskIntoConstraints = false
root.addSubview(c.split)
NSLayoutConstraint.activate([
    c.split.topAnchor.constraint(equalTo: root.topAnchor), c.split.bottomAnchor.constraint(equalTo: root.bottomAnchor),
    c.split.leadingAnchor.constraint(equalTo: root.leadingAnchor), c.split.trailingAnchor.constraint(equalTo: root.trailingAnchor)])
root.layoutSubtreeIfNeeded()
c.layout()

func log(_ name: String) {
    root.layoutSubtreeIfNeeded()
    window.displayIfNeeded()
    let own = c.split.subviews.compactMap { $0.layer }
    let lines = (c.split.layer?.sublayers ?? []).filter { layer in
        !own.contains { $0 === layer } && !layer.isHidden && layer.opacity > 0 && layer.frame.minX < c.split.bounds.width && !(c.nav.isHidden && layer.frame.minX == 0) && layer.frame.width > 0
    }
    print([name, c.nav.isHidden ? "-" : "\(Int(c.nav.frame.maxX.rounded()))", c.right.isHidden ? "-" : "\(Int((c.right.frame.minX - 1).rounded()))",
           lines.map { "\(Int($0.frame.minX.rounded()))" }.joined(separator: ",")].joined(separator: "|"))
}
log("打开")
c.right.isHidden = false; c.layout(); log("右栏出现挤窄左栏")
c.nav.isHidden = true; c.layout(); log("收起左栏")
c.nav.isHidden = false; c.layout(); log("展开左栏")
c.right.isHidden = true; c.layout(); log("右栏没了")
window.setContentSize(NSSize(width: 1400, height: 600)); root.layoutSubtreeIfNeeded(); c.layout(); log("窗口放宽")
c.right.isHidden = false; c.layout(); log("放宽后右栏出现")
window.setContentSize(NSSize(width: 1000, height: 600)); root.layoutSubtreeIfNeeded(); c.layout(); log("窗口缩窄")
