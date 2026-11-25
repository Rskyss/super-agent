import AppKit

/// 三栏宽度的范围和中间最窄宽度：用户 2026-11-25 拍板
let navWidthRange: ClosedRange<CGFloat> = 200...420
let rightWidthRange: ClosedRange<CGFloat> = 300...640
let centerMinWidth: CGFloat = 440
/// 拖动时系统会把夹在边界的分隔线挪半像素，这不算用户拖过
let dragCommitThreshold: CGFloat = 0.51

enum ThreeColumnDivider {
    case left
    case right
}

/// 三段之间的细线用统一配色；拖动时记下用户正在拖哪条线
final class ThreeColumnSplit: NSSplitView {
    weak var columns: ThreeColumns?
    var dragPick: ThreeColumnDivider?

    override var dividerColor: NSColor { NSColor.white.withAlphaComponent(0.07) }
    override var dividerThickness: CGFloat { 1 }

    /// 线本身只有 1 点，左右各放宽 4 点好抓；收起的那一边没有线
    private func dividerZones() -> [NSRect] {
        guard let columns = columns else { return [] }
        let half = dividerThickness / 2 + 4
        var zones: [NSRect] = []
        if !columns.nav.isHidden {
            let mid = columns.nav.frame.maxX + dividerThickness / 2
            zones.append(NSRect(x: mid - half, y: 0, width: half * 2, height: bounds.height))
        }
        if !columns.right.isHidden {
            let mid = columns.right.frame.minX - dividerThickness / 2
            zones.append(NSRect(x: mid - half, y: 0, width: half * 2, height: bounds.height))
        }
        return zones
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        guard !isHidden, alphaValue > 0 else { return nil }
        let local = convert(point, from: superview)
        if dividerZones().contains(where: { $0.contains(local) }) { return self }
        // 线以外的位置交给三栏页面自己接
        for view in arrangedSubviews.reversed() where !view.isHidden {
            if let hit = view.hitTest(local) { return hit }
        }
        return nil
    }

    override func mouseDown(with event: NSEvent) {
        guard let columns = columns,
              let pick = columns.dividerPick(at: convert(event.locationInWindow, from: nil)) else {
            super.mouseDown(with: event)
            return
        }
        dragPick = pick
        columns.beginDrag(pick)
        super.mouseDown(with: event)   // 一直转到松手才返回
        columns.finishDrag()
        dragPick = nil
    }
}

/// 三栏的摆放和拖动只放这一处；栏开没开只看两个视图的 isHidden
final class ThreeColumns: NSObject, NSSplitViewDelegate {
    let split: ThreeColumnSplit
    let nav: NSView
    let center: NSView
    let right: NSView
    private(set) var navWidth: CGFloat
    private(set) var rightWidth: CGFloat
    var onUserDrag: (() -> Void)?

    private var dragPick: ThreeColumnDivider?
    private var dragStartNav: CGFloat = 0
    private var dragStartRight: CGFloat = 0
    private var dragStartKeptNav: CGFloat = 0
    private var dragStartKeptRight: CGFloat = 0

    init(nav: NSView, center: NSView, right: NSView, navWidth: CGFloat, rightWidth: CGFloat) {
        self.nav = nav
        self.center = center
        self.right = right
        self.navWidth = min(max(navWidth, navWidthRange.lowerBound), navWidthRange.upperBound)
        self.rightWidth = min(max(rightWidth, rightWidthRange.lowerBound), rightWidthRange.upperBound)
        self.split = ThreeColumnSplit()
        super.init()
        split.columns = self
        split.isVertical = true
        split.dividerStyle = .thin
        split.delegate = self
        split.arrangesAllSubviews = false
        split.addArrangedSubview(nav)
        split.addArrangedSubview(center)
        split.addArrangedSubview(right)
    }

    /// 按记住的宽度摆三栏：没收起的栏才占宽度和分隔线，只有中间伸缩
    func layout() {
        place()
        split.needsLayout = true   // 分隔线是系统另画的一层：自己摆完要叫它跟着挪，不然线留在旧位置
    }

    private func place() {
        let line = split.dividerThickness
        let navShown = !nav.isHidden
        let rightShown = !right.isHidden
        let lines = CGFloat((navShown ? 1 : 0) + (rightShown ? 1 : 0))
        let total = max(0, split.bounds.width - line * lines)
        var navWidthNow = navShown ? clampNav(navWidth) : 0
        var rightWidthNow = rightShown ? clampRight(rightWidth) : 0

        // 守住中间最窄，放不下时先让右栏再让左栏；宽得下时上面的 min 不会改动用户定的宽度
        if rightShown {
            rightWidthNow = min(rightWidthNow, max(rightWidthRange.lowerBound, total - navWidthNow - centerMinWidth))
        }
        if navShown {
            navWidthNow = min(navWidthNow, max(navWidthRange.lowerBound, total - rightWidthNow - centerMinWidth))
        }

        let height = split.bounds.height
        let centerWidth = max(0, total - (navShown ? navWidthNow : 0) - (rightShown ? rightWidthNow : 0))
        nav.frame = NSRect(x: 0, y: 0, width: navShown ? navWidthNow : 0, height: height)
        let centerX = navShown ? navWidthNow + line : 0
        center.frame = NSRect(x: centerX, y: 0, width: centerWidth, height: height)
        let rightX = centerX + centerWidth + (rightShown ? line : 0)
        right.frame = NSRect(x: rightX, y: 0, width: rightShown ? rightWidthNow : 0, height: height)
    }

    /// 按下时先定好拖的是哪条线，拖的过程中只记这一栏
    func dividerPick(at point: NSPoint) -> ThreeColumnDivider? {
        let half = split.dividerThickness / 2 + 4
        var options: [(ThreeColumnDivider, CGFloat)] = []
        if !nav.isHidden {
            options.append((.left, nav.frame.maxX + split.dividerThickness / 2))
        }
        if !right.isHidden {
            options.append((.right, right.frame.minX - split.dividerThickness / 2))
        }
        guard let nearest = options.min(by: { abs($0.1 - point.x) < abs($1.1 - point.x) }) else { return nil }
        return abs(nearest.1 - point.x) <= half ? nearest.0 : nil
    }

    func beginDrag(_ pick: ThreeColumnDivider) {
        dragPick = pick
        dragStartNav = nav.frame.width
        dragStartRight = right.frame.width
        dragStartKeptNav = navWidth
        dragStartKeptRight = rightWidth
    }

    func finishDrag() {
        guard let pick = dragPick else { return }
        let moved = pick == .left ? abs(nav.frame.width - dragStartNav) : abs(right.frame.width - dragStartRight)
        if moved > dragCommitThreshold {
            switch pick {
            case .left: navWidth = clampNav(nav.frame.width)
            case .right: rightWidth = clampRight(right.frame.width)
            }
            dragPick = nil
            split.dragPick = nil
            layout()
            onUserDrag?()
        } else {
            navWidth = dragStartKeptNav
            rightWidth = dragStartKeptRight
            dragPick = nil
            split.dragPick = nil
            layout()
        }
    }

    private func clampNav(_ width: CGFloat) -> CGFloat {
        min(max(width, navWidthRange.lowerBound), navWidthRange.upperBound)
    }

    private func clampRight(_ width: CGFloat) -> CGFloat {
        min(max(width, rightWidthRange.lowerBound), rightWidthRange.upperBound)
    }

    // MARK: NSSplitViewDelegate

    func splitView(_ splitView: NSSplitView, resizeSubviewsWithOldSize oldSize: NSSize) {
        if dragPick == nil { place() }
    }

    func splitView(_ splitView: NSSplitView, constrainMinCoordinate proposedMinimumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat {
        guard let pick = dragPick else { return proposedMinimumPosition }
        switch pick {
        case .left: return leftBounds().min
        case .right: return rightBounds().min
        }
    }

    func splitView(_ splitView: NSSplitView, constrainMaxCoordinate proposedMaximumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat {
        guard let pick = dragPick else { return proposedMaximumPosition }
        switch pick {
        case .left: return leftBounds().max
        case .right: return rightBounds().max
        }
    }

    func splitView(_ splitView: NSSplitView, shouldHideDividerAt dividerIndex: Int) -> Bool {
        if dividerIndex == 0 { return nav.isHidden }
        return right.isHidden
    }

    func splitView(_ splitView: NSSplitView, effectiveRect proposedEffectiveRect: NSRect,
                   forDrawnRect drawnRect: NSRect, ofDividerAt dividerIndex: Int) -> NSRect {
        if dividerIndex == 0 && nav.isHidden { return .zero }
        if dividerIndex == 1 && right.isHidden { return .zero }
        return NSRect(x: drawnRect.minX - 4, y: drawnRect.minY, width: drawnRect.width + 8, height: drawnRect.height)
    }

    func splitViewDidResizeSubviews(_ notification: Notification) {
        guard let pick = dragPick else { return }
        switch pick {
        case .left:
            if abs(nav.frame.width - dragStartNav) > dragCommitThreshold { navWidth = clampNav(nav.frame.width) }
        case .right:
            if abs(right.frame.width - dragStartRight) > dragCommitThreshold { rightWidth = clampRight(right.frame.width) }
        }
    }

    private func leftBounds() -> (min: CGFloat, max: CGFloat) {
        let line = split.dividerThickness
        let rightWidthNow = right.isHidden ? 0 : right.frame.width
        let maxByCenter = split.bounds.width - line * (right.isHidden ? 1 : 2) - centerMinWidth - rightWidthNow
        // 中间已经被摆得比下限还窄时，不能再借拖动把它挤得更窄，也不能一按就往回跳
        let current = nav.isHidden ? navWidthRange.lowerBound : nav.frame.width
        let maxPos = max(navWidthRange.lowerBound, min(navWidthRange.upperBound, maxByCenter))
        return (navWidthRange.lowerBound, max(current, maxPos))
    }

    private func rightBounds() -> (min: CGFloat, max: CGFloat) {
        let line = split.dividerThickness
        let navWidthNow = nav.isHidden ? 0 : nav.frame.width
        let centerStart = navWidthNow + (nav.isHidden ? 0 : line)
        let minByCenter = centerStart + centerMinWidth
        let minByRight = split.bounds.width - line - rightWidthRange.upperBound
        let current = right.isHidden ? split.bounds.width : right.frame.minX - line / 2
        let minPos = min(current, max(minByCenter, minByRight))
        let maxPos = split.bounds.width - line - rightWidthRange.lowerBound
        return (minPos, max(maxPos, minPos))
    }
}
