// 画应用图标：深青色圆角底，斜放的银色羊角锤与暖橙色手柄。
// 用法：swift make_icon.swift <输出的 .iconset 文件夹>
import AppKit

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

func draw(size: Int) -> Data? {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
    NSGraphicsContext.saveGraphicsState()
    defer { NSGraphicsContext.restoreGraphicsState() }
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(size) / 1024  // 按 1024 的画布设计，再缩放
    NSGraphicsContext.current?.cgContext.scaleBy(x: s, y: s)
    NSGraphicsContext.current?.shouldAntialias = true

    // 底：圆角方块，上浅下深
    let base = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824),
                            xRadius: 185, yRadius: 185)
    NSGradient(starting: color(0x28545a), ending: color(0x102e38))?.draw(in: base, angle: -90)
    if size >= 64 {
        color(0xffffff, 0.08).setStroke()
        base.lineWidth = 2
        base.stroke()
    }

    // 整把锤子共用一个坐标系，手柄从左下指向右上。
    NSGraphicsContext.current?.cgContext.translateBy(x: 512, y: 512)
    NSGraphicsContext.current?.cgContext.rotate(by: -.pi / 5)

    let handle = NSBezierPath(roundedRect: NSRect(x: -49, y: -302, width: 98, height: 466),
                              xRadius: 30, yRadius: 30)
    let head = NSBezierPath()
    head.move(to: NSPoint(x: -198, y: 111))
    head.curve(to: NSPoint(x: -212, y: 125), controlPoint1: NSPoint(x: -208, y: 111), controlPoint2: NSPoint(x: -212, y: 115))
    head.line(to: NSPoint(x: -212, y: 249))
    head.curve(to: NSPoint(x: -198, y: 263), controlPoint1: NSPoint(x: -212, y: 259), controlPoint2: NSPoint(x: -208, y: 263))
    head.line(to: NSPoint(x: -130, y: 263))
    head.curve(to: NSPoint(x: -116, y: 249), controlPoint1: NSPoint(x: -120, y: 263), controlPoint2: NSPoint(x: -116, y: 259))
    head.line(to: NSPoint(x: -116, y: 236))
    head.line(to: NSPoint(x: -25, y: 236))
    head.curve(to: NSPoint(x: 207, y: 119), controlPoint1: NSPoint(x: 84, y: 236), controlPoint2: NSPoint(x: 157, y: 194))
    head.line(to: NSPoint(x: 225, y: 88))
    head.line(to: NSPoint(x: 157, y: 111))
    head.line(to: NSPoint(x: 102, y: 167))
    head.line(to: NSPoint(x: 135, y: 96))
    head.line(to: NSPoint(x: 81, y: 119))
    head.curve(to: NSPoint(x: -38, y: 151), controlPoint1: NSPoint(x: 43, y: 144), controlPoint2: NSPoint(x: 1, y: 151))
    head.line(to: NSPoint(x: -116, y: 151))
    head.line(to: NSPoint(x: -116, y: 125))
    head.curve(to: NSPoint(x: -130, y: 111), controlPoint1: NSPoint(x: -116, y: 115), controlPoint2: NSPoint(x: -120, y: 111))
    head.close()

    // 轻微投影只增加分层感；小尺寸直接保留最清楚的色块。
    if size >= 64 {
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = color(0x031b24, 0.30)
        shadow.shadowBlurRadius = 14
        shadow.shadowOffset = NSSize(width: 0, height: -12)
        shadow.set()
        color(0x17343b).setFill()
        handle.fill()
        head.fill()
        NSGraphicsContext.restoreGraphicsState()
    }

    NSGradient(starting: color(0xffbf73), ending: color(0xe88c48))?.draw(in: handle, angle: 0)
    if size >= 64 {
        color(0xffe0ad, 0.55).setFill()
        NSBezierPath(roundedRect: NSRect(x: -31, y: -267, width: 12, height: 332),
                     xRadius: 6, yRadius: 6).fill()
    }
    // 深色套环让手柄和锤头的连接在缩小后仍然清晰。
    color(0x527075).setFill()
    NSBezierPath(roundedRect: NSRect(x: -49, y: 64, width: 98, height: 102),
                 xRadius: 9, yRadius: 9).fill()
    NSGradient(starting: color(0xc0d2d4), ending: color(0xf6faf5))?.draw(in: head, angle: 90)
    if size >= 64 {
        color(0xffffff, 0.45).setFill()
        NSBezierPath(roundedRect: NSRect(x: -198, y: 129, width: 18, height: 116),
                     xRadius: 6, yRadius: 6).fill()
    }

    return rep.representation(using: .png, properties: [:])
}

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset")
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        if let data = draw(size: base * scale) { try? data.write(to: out.appendingPathComponent(name)) }
    }
}
print("图标已生成：\(out.path)")
