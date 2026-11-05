// 画应用图标：深色圆角底，一条竖线串起三站——完成（绿）、正在干（琥珀，发光）、还没轮到（灰）。
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
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(size) / 1024  // 按 1024 的画布设计，再缩放

    // 底：圆角方块，上浅下深
    let base = NSBezierPath(roundedRect: NSRect(x: 100 * s, y: 100 * s, width: 824 * s, height: 824 * s),
                            xRadius: 185 * s, yRadius: 185 * s)
    NSGradient(starting: color(0x1b2c48), ending: color(0x0b1220))?.draw(in: base, angle: -90)

    // 竖线：上半段已走过（绿），下半段还没到（灰）
    let x = 512 * s
    color(0x26364f).setFill()
    NSBezierPath(roundedRect: NSRect(x: x - 14 * s, y: 250 * s, width: 28 * s, height: 524 * s),
                 xRadius: 14 * s, yRadius: 14 * s).fill()
    color(0x6fcf9e).setFill()
    NSBezierPath(roundedRect: NSRect(x: x - 14 * s, y: 512 * s, width: 28 * s, height: 262 * s),
                 xRadius: 14 * s, yRadius: 14 * s).fill()

    func dot(_ y: CGFloat, _ r: CGFloat, _ c: NSColor) {
        c.setFill()
        NSBezierPath(ovalIn: NSRect(x: x - r * s, y: y * s - r * s, width: 2 * r * s, height: 2 * r * s)).fill()
    }
    dot(774, 62, color(0x6fcf9e))                         // 上：完成
    for (r, a) in [(150.0, 0.10), (118.0, 0.18), (96.0, 0.30)] { dot(512, CGFloat(r), color(0xf5b451, CGFloat(a))) }
    dot(512, 78, color(0xf5b451))                         // 中：正在干，带光晕
    dot(250, 52, color(0x3a4a63))                         // 下：还没轮到

    NSGraphicsContext.restoreGraphicsState()
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
