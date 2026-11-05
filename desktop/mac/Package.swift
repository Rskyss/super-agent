// swift-tools-version:5.9
// 超级助手桌面窗口：左边真终端（运行调度员），右边流转面板（本机页面）。
import PackageDescription

let package = Package(
    name: "SuperAgent",
    platforms: [.macOS(.v13)],
    dependencies: [
        // 终端组件：SwiftTerm（MIT 许可）。固定版本，v1.99.0 发布于 2026-10-08
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", exact: "1.99.0"),
    ],
    targets: [
        .executableTarget(
            name: "SuperAgent",
            dependencies: [.product(name: "SwiftTerm", package: "SwiftTerm")],
            path: "Sources/SuperAgent"
        ),
    ]
)
