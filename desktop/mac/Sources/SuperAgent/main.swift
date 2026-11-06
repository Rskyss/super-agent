// 超级助手桌面窗口
//
// 打开一个项目文件夹：
//   - 没接入过的 git 项目：自动接入（项目里只多一个 .collab/，并加进不提交名单），不复制协作包；
//   - 旧方式装过协作包的项目：问用户要不要删掉项目里的旧协作包，再迁移（记录一条不动）；
//   - 不是 git 项目：不接入，左边照常能聊。
// 窗口分三段：
//   左栏：搜索、主对话、卡片列表、项目名和设置（页面 nav，点击用 superagent:// 地址告诉窗口）。
//   中间：和这个项目的调度员对话（用户亲手选，每个项目分别记住；没选不启动任何 AI）。
//         顶部细栏「聊天 | 终端」切换：Codex 默认聊天视图（一问一答、图片直接显示、允许/拒绝按钮），
//         其他调度员先用终端视图。切换时前一个视图先让出对话，后一个接着同一个对话，不重复开场交代。
//         设置页也放在中间，打开时右栏临时收起。
//   右栏：一张卡的流程和交接（只读项目的 .collab/），可以收起。
// 页面都由本机数据服务提供；聊天地址要带窗口启动时生成的一次性口令。
// 协作包与面板优先用源目录（编译时所在的仓库）里的那份，源目录不在时才用应用里自带的。
//
// 自测用参数（正常双击打开时用不到；都只对这一次有效，不改用户的任何设置）：
//   --project <文件夹>          指定项目，不弹选择框
//   --dispatcher <codex|claude> 这一次用哪位调度员
//   --command <命令>            左边改跑这条命令（终端视图）
//   --left <chat|terminal>      这一次中间先显示哪种视图
//   --card <卡片编号>           这一次打开后选中这张卡
//   --old-package <keep|remove> 自动回答“要不要删旧协作包”
//   --snapshot <文件夹>         启动后把三段各截一张图存进去，然后退出
//   --snapshot-delay <秒>       截图前等多久，默认 8 秒
//   --say <话>                  截图前在聊天里说这句话
//   --walk <文件>               按顺序点左栏、切换、收起、改窗口大小，把每一步的结果写进文件，然后退出
//   --walk-clerk --clerk-key-file <文件>  走一遍行政聊天，不碰用户的钥匙串
//   --walk-work                 只走“调度员干活时切终端、去设置再回来”这几步
//   --clerk-try <文件> [--clerk-base 地址]  用文件里的密钥走一次第一屏的测试和保存，结果写到“文件.result”
//   --interrupt-answer <stay|leave>  调度员干活时切终端，代替用户回答提示
import AppKit
import Security
import SwiftTerm
import WebKit

let lastProjectKey = "lastProject"
let recentProjectsKey = "recentProjects"
let panelBackground = NSColor(srgbRed: 0x13 / 255.0, green: 0x14 / 255.0, blue: 0x17 / 255.0, alpha: 1)
let chatBackground = NSColor(srgbRed: 0x17 / 255.0, green: 0x18 / 255.0, blue: 0x1b / 255.0, alpha: 1)
let terminalBackground = NSColor(srgbRed: 0x17 / 255.0, green: 0x18 / 255.0, blue: 0x1b / 255.0, alpha: 1)
let surface2Color = NSColor(srgbRed: 0x2b / 255.0, green: 0x2d / 255.0, blue: 0x33 / 255.0, alpha: 1)
let textColor = NSColor(srgbRed: 0xec / 255.0, green: 0xed / 255.0, blue: 0xef / 255.0, alpha: 1)
let dimColor = NSColor(srgbRed: 0xa0 / 255.0, green: 0xa4 / 255.0, blue: 0xad / 255.0, alpha: 1)

let topBarHeight: CGFloat = 38
let navWidthRange: ClosedRange<CGFloat> = 220...340
let rightWidthRange: ClosedRange<CGFloat> = 320...520
let centerMinWidth: CGFloat = 440
let threeColumnWidth: CGFloat = 1000  // 窗口比这窄时右栏自动收起
let navWidthKey = "navWidth"
let rightWidthKey = "rightWidth"
let rightOpenKey = "rightOpen"

/// 三段之间的细线用统一配色
final class ThreeColumnSplit: NSSplitView {
    override var dividerColor: NSColor { NSColor.white.withAlphaComponent(0.07) }
    override var dividerThickness: CGFloat { 1 }
}

/// 顶部细栏：拖它就是拖窗口
final class TopBar: NSView {
    override var mouseDownCanMoveWindow: Bool { true }
}

/// 「聊天 | 终端」切换。selectedSegment 为 -1 表示两个都不选（打开设置时）
final class ViewSwitch: NSView {
    var onChange: (() -> Void)?
    var selectedSegment = 0 { didSet { refresh() } }
    private var buttons: [NSButton] = []

    init(labels: [String]) {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.cornerRadius = 8
        layer?.backgroundColor = NSColor(srgbRed: 0x21 / 255.0, green: 0x23 / 255.0, blue: 0x27 / 255.0, alpha: 1).cgColor
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.spacing = 0
        stack.edgeInsets = NSEdgeInsets(top: 2, left: 2, bottom: 2, right: 2)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor), stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor), stack.trailingAnchor.constraint(equalTo: trailingAnchor),
        ])
        for (index, label) in labels.enumerated() {
            let button = NSButton(title: label, target: self, action: #selector(tapped(_:)))
            button.tag = index
            button.isBordered = false
            button.wantsLayer = true
            button.layer?.cornerRadius = 6
            button.translatesAutoresizingMaskIntoConstraints = false
            NSLayoutConstraint.activate([button.widthAnchor.constraint(equalToConstant: 48),
                                         button.heightAnchor.constraint(equalToConstant: 22)])
            buttons.append(button)
            stack.addArrangedSubview(button)
        }
        refresh()
    }

    required init?(coder: NSCoder) { nil }

    func setEnabled(_ enabled: Bool, forSegment index: Int) { buttons[index].isEnabled = enabled; refresh() }
    func setToolTip(_ text: String?, forSegment index: Int) { buttons[index].toolTip = text }

    @objc private func tapped(_ sender: NSButton) {
        guard sender.tag != selectedSegment else { return }
        selectedSegment = sender.tag
        onChange?()
    }

    private func refresh() {
        for button in buttons {
            let on = button.tag == selectedSegment
            let color = on ? textColor : dimColor.withAlphaComponent(button.isEnabled ? 1 : 0.45)
            button.attributedTitle = NSAttributedString(string: button.title, attributes: [
                .foregroundColor: color, .font: NSFont.systemFont(ofSize: 12, weight: on ? .medium : .regular),
            ])
            button.layer?.backgroundColor = on ? surface2Color.cgColor : NSColor.clear.cgColor
        }
    }
}

/// 行政人员的密钥：只放本机钥匙串，不写进任何文件
enum ClerkKey {
    static var service: String {
        "local.superagent.clerk" + (argValue("--clerk-try") == nil && argValue("--clerk-ui-test") == nil ? "" : ".selftest")
    }

    static func query() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: "api-key"]
    }

    static func read() -> String? {
        var request = query()
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &found) == errSecSuccess, let data = found as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult static func save(_ key: String) -> Bool {
        SecItemDelete(query() as CFDictionary)
        var item = query()
        item[kSecValueData as String] = Data(key.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    static func delete() { SecItemDelete(query() as CFDictionary) }
}

enum ChatMode { case continueLast, fresh }
enum LeftView { case chat, terminal, config }

func argValue(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

/// 把程序输出当纯文字放进页面（报错里常有 < > 符号）
func htmlEscape(_ s: String) -> String {
    s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

/// 单引号包起来给 shell 用（路径里有空格、中文都不怕）
func shellQuote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

func urlEncode(_ s: String) -> String {
    var allowed = CharacterSet.alphanumerics
    allowed.insert(charactersIn: "-._~/")
    return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
}

/// 某个项目的调度员：用户在团队配置里亲手选的。
/// 接入了的项目存在项目的 .collab/配置.json（team.dispatcher，协作包和面板也读这份）；
/// 没接入的文件夹（不是 git）存在窗口自己的设置里。Claude 暂不作为调度员。
struct Dispatcher {
    let saved: String  // "codex"、"cursor"、"agy"，或 "custom:<命令>"
    var codexModel: String? = nil
    var codexEffort: String? = nil
    var cursorModel: String? = nil
    var agyModel: String? = nil

    static func key(for project: URL) -> String { "dispatcher:" + project.path }

    static func forProject(_ project: URL) -> Dispatcher? {
        if let once = argValue("--dispatcher") { return Dispatcher(saved: once) }  // 自测：只这一次
        let config = project.appendingPathComponent(".collab/配置.json")
        if let data = try? Data(contentsOf: config),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let team = json["team"] as? [String: Any], let who = team["dispatcher"] as? String, !who.isEmpty {
            return Dispatcher(
                saved: who,
                codexModel: json["codex"] as? String,
                codexEffort: json["codex_effort"] as? String,
                cursorModel: json["cursor"] as? String,
                agyModel: json["agy"] as? String
            )
        }
        guard let s = UserDefaults.standard.string(forKey: key(for: project)), !s.isEmpty else { return nil }
        return Dispatcher(saved: s)
    }

    var name: String {
        switch saved {
        case "codex": return "Codex"
        case "cursor": return "Cursor"
        case "agy": return "agy"
        default: return String(saved.dropFirst("custom:".count)).split(separator: " ").first.map(String.init) ?? "自定义"
        }
    }

    /// 能用聊天视图的调度员（其余先用终端视图）
    var hasChat: Bool { saved == "codex" }

    /// 自定义命令不知道怎么接开场话，就不交代
    var takesOpening: Bool { ["codex", "cursor", "agy"].contains(saved) }

    var modelFlags: String {
        switch saved {
        case "codex":
            var parts: [String] = []
            if let m = codexModel, !m.isEmpty { parts.append("-m " + shellQuote(m)) }
            if let e = codexEffort, !e.isEmpty { parts.append("-c " + shellQuote("model_reasoning_effort=\"\(e)\"")) }
            return parts.isEmpty ? "" : " " + parts.joined(separator: " ")
        case "cursor":
            if let m = cursorModel, !m.isEmpty { return " --model " + shellQuote(m) }
            return ""
        case "agy":
            if let m = agyModel, !m.isEmpty { return " --model " + shellQuote(m) }
            return ""
        default:
            return ""
        }
    }

    /// Codex 的调度员角色走启动设置，不用发一句话，也就不花额度
    func roleFlag(_ role: String?) -> String {
        guard saved == "codex", let role = role, !role.isEmpty else { return "" }
        let toml = role.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
        return " -c " + shellQuote("developer_instructions=\"\(toml)\"")
    }

    func freshCommand(_ opening: String?, role: String? = nil) -> String {
        let say = opening.map { " " + shellQuote($0) } ?? ""
        switch saved {
        case "codex": return "codex --no-daemon" + modelFlags + roleFlag(role) + say
        case "cursor": return "agent" + modelFlags + say                       // Cursor 的命令行叫 agent
        case "agy": return opening == nil ? "agy" + modelFlags : "agy" + modelFlags + " -i" + say  // -i：带着开场话进入对话
        default: return String(saved.dropFirst("custom:".count))
        }
    }

    /// 接着这个项目里最近一次的对话；还没聊过（几秒内就退出）时，开一个新的
    func continueCommand(_ opening: String?, role: String? = nil) -> String {
        let say = opening.map { " " + shellQuote($0) } ?? ""
        let resume: String
        switch saved {
        case "codex": resume = "codex resume --no-daemon --last" + modelFlags + roleFlag(role) + say  // 只找当前文件夹里的对话；不走后台服务，关掉立刻让出
        case "cursor": resume = "agent --continue" + modelFlags + say
        case "agy": resume = "agy --continue" + modelFlags + (opening == nil ? "" : " -i" + say)
        default: return freshCommand(nil)
        }
        return "s=$(date +%s); \(resume); c=$?; "
            + "if [ $c -ne 0 ] && [ $(( $(date +%s) - s )) -lt 5 ]; then "
            + "echo '（这个项目还没有可以接着的对话，开一个新的）'; \(freshCommand(opening, role: role)); fi"
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSSplitViewDelegate, NSMenuItemValidation, NSWindowDelegate,
    WKNavigationDelegate, LocalProcessTerminalViewDelegate {
    var window: NSWindow!
    var split: NSSplitView!
    var navWeb: WKWebView!               // 左栏页面
    var navBox: NSVisualEffectView!      // 左栏：系统半透明材质，从窗口顶部贯通到底
    var leftBox: NSView!                 // 中间：顶部细栏 + 下面的内容
    var leftContent: NSView!             // 聊天、终端或设置
    var rightBox: NSView!                // 右栏：顶部留给拖动，下面是页面
    var segment: ViewSwitch!
    var rightToggle: NSButton!
    var term: LocalProcessTerminalView?  // 终端视图时才有
    var chatWeb: WKWebView!              // 聊天视图
    var configWeb: WKWebView!            // 团队配置
    var web: WKWebView!                  // 右栏
    var selectedCard: String?            // 左栏选中的卡；nil 表示主对话，右栏跟随最新
    var rightWanted = true               // 点开卡片时右栏开不开；没选卡片时右栏根本不存在
    var navWidth: CGFloat = 264
    var rightWidth: CGFloat = 372
    var chatKept = false  // 去设置时聊天没断开
    var leaveConfirmed = false  // 这次去终端已经问过了
    var selfTest: Bool { argValue("--snapshot") != nil || argValue("--walk") != nil }  // 自测不改用户的记忆
    var leftView: LeftView = .terminal
    var recentMenu = NSMenu(title: "最近打开")
    var server: Process?
    var port: Int?
    var project: URL!
    var firstTime = false   // 这个项目还没有项目说明 → 开场话用“第一次”的版本
    var pendingOpening = true
    var chatLoadedKey: String?  // 已经加载好的聊天页（项目+调度员），从终端切回来时直接保留画面不重刷
    var serverOutput = ""
    var quitting = false    // 退出时是我们主动停掉数据服务，不算出错
    let chatToken = (0..<4).map { _ in String(format: "%08x", UInt32.random(in: .min ... .max)) }.joined()

    // MARK: 启动

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        guard let chosen = initialProject() else { NSApp.terminate(nil); return }
        project = chosen
        buildWindow()
        prepareProject()
        remember(project)
        startServer()
        let withOpening = argValue("--switch-test") == nil
        selectedCard = argValue("--card")
        clerkReady = savedClerkKey() != nil  // 先定下来：不然终端型调度员会先启动并发话
        showLeft(firstLeft(), opening: withOpening)
        if let dir = argValue("--snapshot") { scheduleSnapshot(into: dir) }
        if let file = argValue("--walk") { scheduleWalk(into: file) }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        stopTerminal()
        server?.terminate()
    }

    func initialProject() -> URL? {
        if let p = argValue("--project") { return URL(fileURLWithPath: p) }
        if let saved = UserDefaults.standard.string(forKey: lastProjectKey), isFolder(saved) {
            return URL(fileURLWithPath: saved)
        }
        return askForProject()
    }

    func isFolder(_ path: String) -> Bool {
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDir) && isDir.boolValue
    }

    func askForProject() -> URL? {
        let panel = NSOpenPanel()
        panel.title = "选择一个项目文件夹"
        panel.prompt = "打开项目"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK else { return nil }
        return panel.url
    }

    // MARK: 打开项目：接入 / 迁移

    /// 协作包和面板用哪一份：源目录优先（改了不用重新生成应用），源目录不在时用应用里自带的
    func superAgentRoot() -> URL {
        let source = URL(fileURLWithPath: #filePath)  // desktop/mac/Sources/SuperAgent/main.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let fm = FileManager.default
        if fm.fileExists(atPath: source.appendingPathComponent("collab/规则.md").path),
           fm.fileExists(atPath: source.appendingPathComponent("desktop/server.py").path) {
            return source
        }
        return Bundle.main.resourceURL?.appendingPathComponent("super-agent") ?? source
    }

    /// 运行一条协作包命令（接入、迁移），等它做完
    @discardableResult
    func runCollab(_ args: [String]) -> String {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = ["-l", "-c", "exec python3 -B \"$0\" \"$@\"", superAgentRoot().appendingPathComponent("collab").path] + args
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        do { try proc.run() } catch { return "运行失败：\(error.localizedDescription)" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }

    func prepareProject() {
        let fm = FileManager.default
        let p = project.path
        let isGit = fm.fileExists(atPath: p + "/.git")
        let oldPackage = URL(fileURLWithPath: p + "/collab")
        let isOldPackage = fm.fileExists(atPath: oldPackage.appendingPathComponent("规则.md").path)
            && fm.fileExists(atPath: oldPackage.appendingPathComponent("cli.py").path)
            && oldPackage.standardizedFileURL.path != superAgentRoot().appendingPathComponent("collab").standardizedFileURL.path

        if isOldPackage {
            runCollab(["migrate", p] + (askRemoveOldPackage() ? ["--remove-old-package"] : []))
        } else if isGit && !fm.fileExists(atPath: p + "/.collab") {
            runCollab(["init", p])
        }
        firstTime = !(fm.fileExists(atPath: p + "/docs/项目说明.md") || fm.fileExists(atPath: p + "/.collab/项目档案.md"))
    }

    func askRemoveOldPackage() -> Bool {
        if let answer = argValue("--old-package") { return answer == "remove" }  // 自测
        let alert = NSAlert()
        alert.messageText = "这个项目里有一份旧的协作包"
        alert.informativeText = "以后改用超级助手自带的那一份。要删掉项目里的旧协作包（collab/ 文件夹）吗？\n\n"
            + "协作记录一条都不会动。不删也不影响使用。"
        alert.addButton(withTitle: "删掉旧协作包")
        alert.addButton(withTitle: "先留着")
        return alert.runModal() == .alertFirstButtonReturn
    }

    /// 打开另一个项目（菜单“打开项目…”或“最近打开”）
    func switchTo(_ url: URL) {
        closeChatOnServer(clearView: true)
        project = url
        selectedCard = nil
        prepareProject()
        remember(url)
        updateTitle()
        showLeft(firstLeft(), opening: true)
        loadPanel()
    }

    func remember(_ url: URL) {
        guard argValue("--project") == nil else { return }  // 自测打开的项目不记进列表
        UserDefaults.standard.set(url.path, forKey: lastProjectKey)
        var recent = UserDefaults.standard.stringArray(forKey: recentProjectsKey) ?? []
        recent.removeAll { $0 == url.path }
        recent.insert(url.path, at: 0)
        UserDefaults.standard.set(Array(recent.prefix(10)), forKey: recentProjectsKey)
        rebuildRecentMenu()
    }

    func rebuildRecentMenu() {
        recentMenu.removeAllItems()
        let recent = (UserDefaults.standard.stringArray(forKey: recentProjectsKey) ?? []).filter { isFolder($0) }
        if recent.isEmpty {
            recentMenu.addItem(withTitle: "（还没有）", action: nil, keyEquivalent: "")
        }
        for path in recent {
            let url = URL(fileURLWithPath: path)
            let item = NSMenuItem(title: url.lastPathComponent + "　" + url.deletingLastPathComponent().path,
                                  action: #selector(openRecent(_:)), keyEquivalent: "")
            item.representedObject = path
            recentMenu.addItem(item)
        }
    }

    // MARK: 开场话（与聊天视图共用 desktop/opening.json）

    func openingPart(_ pick: (([String: Any], (String) -> String) -> String)) -> String {
        let root = superAgentRoot()
        let file = root.appendingPathComponent("desktop/opening.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        let fm = FileManager.default
        let profile = fm.fileExists(atPath: project.path + "/docs/项目说明.md")
            || !fm.fileExists(atPath: project.path + "/.collab/项目档案.md") ? "docs/项目说明.md" : ".collab/项目档案.md"
        func fill(_ key: String) -> String {
            var text = json[key] as? String ?? ""
            for (name, value) in [("manual", root.appendingPathComponent("collab/README.md").path),
                                  ("cli", root.appendingPathComponent("collab").path),
                                  ("project", project.path), ("profile", profile)] {
                text = text.replacingOccurrences(of: "{\(name)}", with: value)
            }
            return text
        }
        return pick(json, fill)
    }

    /// Cursor、agy 的终端只有一句话能交代：标记 + 这次的任务 + 调度员守则
    func openingWords() -> String {
        openingPart { json, fill in
            (json["marker"] as? String ?? "") + fill(firstTime ? "first" : "every") + "\n\n" + fill("background")
        }
    }

    // MARK: 窗口

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.minSize = NSSize(width: 760, height: 480)
        window.backgroundColor = panelBackground
        window.appearance = NSAppearance(named: .darkAqua)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        updateTitle()
        window.center()
        window.setFrameAutosaveName(selfTest ? "SuperAgentSelfTest" : "SuperAgentMainWindow")

        let defaults = UserDefaults.standard
        if let saved = defaults.object(forKey: navWidthKey) as? Double { navWidth = CGFloat(saved) }
        if let saved = defaults.object(forKey: rightWidthKey) as? Double { rightWidth = CGFloat(saved) }
        if let saved = defaults.object(forKey: rightOpenKey) as? Bool { rightWanted = saved }

        split = ThreeColumnSplit()
        split.isVertical = true
        split.dividerStyle = .thin
        split.delegate = self
        split.translatesAutoresizingMaskIntoConstraints = false

        navWeb = makeWeb(message: "", background: "transparent")
        navWeb.navigationDelegate = self
        navBox = NSVisualEffectView()
        navBox.material = .sidebar
        navBox.blendingMode = .behindWindow
        navBox.state = .active
        pin(navWeb, in: navBox, top: topBarHeight)
        let navTop = dragStrip()   // 顶部和页面同一层深色
        navTop.wantsLayer = true
        navTop.layer?.backgroundColor = NSColor(srgbRed: 0x13 / 255.0, green: 0x14 / 255.0, blue: 0x17 / 255.0, alpha: 0.72).cgColor
        pin(navTop, in: navBox, height: topBarHeight)

        buildLeft()
        web = makeWeb(message: "正在启动…")
        rightBox = NSView()
        rightBox.wantsLayer = true
        rightBox.layer?.backgroundColor = panelBackground.cgColor
        pin(web, in: rightBox)   // 右栏的“流程 / 交接”和中间的切换同一高度

        // 三段的位置由 layoutColumns 算，不交给系统按比例分
        split.arrangesAllSubviews = false
        split.addSubview(navBox)
        split.addSubview(leftBox)
        split.addSubview(rightBox)

        let root = NSView()
        root.addSubview(split)
        NSLayoutConstraint.activate([
            split.topAnchor.constraint(equalTo: root.topAnchor),
            split.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            split.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            split.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        window.contentView = root
        window.makeKeyAndOrderFront(nil)
        root.layoutSubtreeIfNeeded()

        rightBox.isHidden = true
        syncRight()
        window.delegate = self   // 界面建好才开始听：恢复窗口大小时系统会立刻发通知
    }

    func dragStrip() -> NSView { TopBar() }

    /// 把子视图铺进容器。height 给了就只占顶部这么高，否则从 top 往下铺满
    func pin(_ view: NSView, in box: NSView, top: CGFloat = 0, height: CGFloat? = nil) {
        view.translatesAutoresizingMaskIntoConstraints = false
        box.addSubview(view)
        var rules = [view.leadingAnchor.constraint(equalTo: box.leadingAnchor),
                     view.trailingAnchor.constraint(equalTo: box.trailingAnchor),
                     view.topAnchor.constraint(equalTo: box.topAnchor, constant: top)]
        rules.append(height.map { view.heightAnchor.constraint(equalToConstant: $0) }
                     ?? view.bottomAnchor.constraint(equalTo: box.bottomAnchor))
        NSLayoutConstraint.activate(rules)
    }

    /// 中间顶部细栏：左边切换聊天和终端，右边收起右栏。拖它就是拖窗口
    func buildTopBar() -> NSView {
        let bar = TopBar()
        segment = ViewSwitch(labels: ["聊天", "终端"])
        segment.translatesAutoresizingMaskIntoConstraints = false
        segment.onChange = { [weak self] in self?.segmentChanged() }
        bar.addSubview(segment)

        rightToggle = NSButton(image: NSImage(systemSymbolName: "sidebar.right", accessibilityDescription: "收起或展开右栏")
                               ?? NSImage(), target: self, action: #selector(toggleRight(_:)))
        rightToggle.translatesAutoresizingMaskIntoConstraints = false
        rightToggle.isBordered = false
        rightToggle.wantsLayer = true
        rightToggle.layer?.cornerRadius = 6
        rightToggle.toolTip = "收起或展开右栏"
        bar.addSubview(rightToggle)

        NSLayoutConstraint.activate([
            segment.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 16),
            segment.centerYAnchor.constraint(equalTo: bar.centerYAnchor, constant: 2),
            rightToggle.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -10),
            rightToggle.centerYAnchor.constraint(equalTo: bar.centerYAnchor, constant: 2),
            rightToggle.widthAnchor.constraint(equalToConstant: 28),
            rightToggle.heightAnchor.constraint(equalToConstant: 26),
        ])
        return bar
    }

    /// 右栏只属于任务卡片：选了卡、用户没收起、窗口够宽、不在设置页才显示。
    /// 主对话里还不确定是不是任务，这里不显示任何流程
    func syncRight() {
        let show = selectedCard != nil && rightWanted && leftView != .config && window.frame.width >= threeColumnWidth
        rightToggle.isHidden = selectedCard == nil
        rightToggle.isEnabled = leftView != .config
        rightToggle.contentTintColor = show ? textColor : dimColor
        rightToggle.layer?.backgroundColor = NSColor.clear.cgColor
        rightBox.isHidden = !show
        layoutColumns()
    }

    /// 按记住的宽度摆三段，只有中间伸缩。放不下时先让右栏、再让左栏，都不低于下限
    func layoutColumns() {
        let line = split.dividerThickness, height = split.bounds.height
        let show = !rightBox.isHidden
        let total = split.bounds.width - line * (show ? 2 : 1)
        var nav = min(navWidth, total - centerMinWidth), right = rightWidth
        if show, nav + right + centerMinWidth > total {
            right = max(rightWidthRange.lowerBound, total - nav - centerMinWidth)
            nav = max(navWidthRange.lowerBound, total - right - centerMinWidth)
        }
        if !show { right = 0 }
        navBox.frame = NSRect(x: 0, y: 0, width: nav, height: height)
        leftBox.frame = NSRect(x: nav + line, y: 0, width: total - nav - right, height: height)
        if show { rightBox.frame = NSRect(x: split.bounds.width - right, y: 0, width: right, height: height) }
    }

    func splitView(_ splitView: NSSplitView, resizeSubviewsWithOldSize oldSize: NSSize) { layoutColumns() }

    func setRightWanted(_ wanted: Bool) {
        rightWanted = wanted
        if !selfTest { UserDefaults.standard.set(wanted, forKey: rightOpenKey) }
        if wanted { widenForRight() }
        syncRight()
    }

    /// 窗口太窄放不下右栏：屏幕放得下就加宽，放不下就保持收起
    func widenForRight() {
        guard window.frame.width < threeColumnWidth, let screen = window.screen?.visibleFrame,
              screen.width >= threeColumnWidth else { return }
        var frame = window.frame
        frame.size.width = threeColumnWidth
        frame.origin.x = max(screen.minX, min(frame.origin.x, screen.maxX - threeColumnWidth))
        window.setFrame(frame, display: true, animate: true)
    }

    @objc func toggleRight(_ sender: Any?) { setRightWanted(rightBox.isHidden) }

    func windowDidResize(_ notification: Notification) {
        window.contentView?.layoutSubtreeIfNeeded()   // 先让分隔视图拿到新宽度
        syncRight()
    }

    /// 左栏点了一张卡（nil 是主对话）。右栏换卡，中间不变
    func selectCard(_ id: String?) {
        selectedCard = id
        if leftView == .config, Dispatcher.forProject(project) != nil { showLeft(preferredLeft(), opening: false) }
        if id != nil { setRightWanted(true) } else { syncRight() }
        web.evaluateJavaScript("window.SuperAgentPanel && window.SuperAgentPanel.show(\(jsValue(id)))", completionHandler: nil)
        syncNav()
    }

    /// 左栏点了一段历史对话：中间换到这段对话
    func openThread(_ id: String) {
        selectCard(nil)
        let wasChat = leftView == .chat
        if !wasChat { showLeft(.chat, opening: false) }
        let open = { [weak self] in
            guard let self = self, self.leftView == .chat else { return }
            self.chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.openThread(\(self.jsValue(id)))", completionHandler: nil)
            self.refreshNavThreads()
        }
        if wasChat { open() } else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: open) }
    }

    /// 对话换了，左栏的“当前”标记也要跟上（连接要一小会）
    func refreshNavThreads() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            self?.navWeb.evaluateJavaScript("window.SuperAgentNav && window.SuperAgentNav.refreshThreads()", completionHandler: nil)
        }
    }

    func navMode() -> String { leftView == .config ? "settings" : (selectedCard == nil ? "main" : "card") }

    func syncNav() {
        navWeb?.evaluateJavaScript("window.SuperAgentNav && window.SuperAgentNav.select(\(jsValue(selectedCard)), \"\(navMode())\")",
                                   completionHandler: nil)
    }

    func jsValue(_ text: String?) -> String {
        guard let text = text, let data = try? JSONSerialization.data(withJSONObject: text, options: .fragmentsAllowed),
              let quoted = String(data: data, encoding: .utf8) else { return "null" }
        return quoted
    }

    /// 中间：顶部细栏 + 下面放聊天、终端或设置
    func buildLeft() {
        leftBox = NSView(frame: NSRect(x: 0, y: 0, width: 760, height: 800))
        leftBox.wantsLayer = true
        leftBox.layer?.backgroundColor = chatBackground.cgColor
        leftContent = NSView()
        pin(leftContent, in: leftBox, top: topBarHeight + 4)
        pin(buildTopBar(), in: leftBox, height: topBarHeight + 4)
        chatWeb = makeWeb(message: "正在连接调度员…", background: "#17181b")
        configWeb = makeWeb(message: "正在打开团队配置…", background: "#17181b")
        configWeb.navigationDelegate = self
    }

    func updateTitle() {
        let who = Dispatcher.forProject(project).map { "调度员：\($0.name)" } ?? "还没选调度员"
        window?.title = "超级助手 · " + project.lastPathComponent + " · " + who
    }

    func makeTerminal() -> LocalProcessTerminalView {
        let view = LocalProcessTerminalView(frame: leftContent.bounds)
        view.processDelegate = self
        view.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        view.nativeBackgroundColor = terminalBackground
        view.nativeForegroundColor = NSColor(srgbRed: 0.92, green: 0.93, blue: 0.94, alpha: 1)
        return view
    }

    func makeWeb(message: String, background: String = "#131417") -> WKWebView {
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 520, height: 800), configuration: WKWebViewConfiguration())
        view.setValue(false, forKey: "drawsBackground")  // 加载前不闪白
        view.loadHTMLString(Self.message(message, background: background), baseURL: nil)
        return view
    }

    static func message(_ text: String, background: String = "#131417") -> String {
        "<!DOCTYPE html><html><body style=\"margin:0;height:100vh;display:grid;place-items:center;"
            + "background:\(background);color:#a0a4ad;font:14px -apple-system,'PingFang SC',sans-serif;padding:24px;"
            + "box-sizing:border-box;text-align:center\">\(text)</body></html>"
    }

    /// 把一个视图铺满左边的内容区
    func place(_ view: NSView) {
        leftContent.subviews.forEach { $0.removeFromSuperview() }
        view.translatesAutoresizingMaskIntoConstraints = false
        leftContent.addSubview(view)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: leftContent.topAnchor),
            view.bottomAnchor.constraint(equalTo: leftContent.bottomAnchor),
            view.leadingAnchor.constraint(equalTo: leftContent.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: leftContent.trailingAnchor),
        ])
    }

    // 三段都有上下限，谁都不会被拖没
    func splitView(_ splitView: NSSplitView, constrainMinCoordinate proposedMinimumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat {
        if dividerIndex == 0 { return navWidthRange.lowerBound }
        let widest = splitView.bounds.width - rightWidthRange.upperBound - splitView.dividerThickness
        return max(navBox.frame.maxX + splitView.dividerThickness + centerMinWidth, widest)
    }

    func splitView(_ splitView: NSSplitView, constrainMaxCoordinate proposedMaximumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat {
        if dividerIndex == 0 { return navWidthRange.upperBound }
        return splitView.bounds.width - rightWidthRange.lowerBound - splitView.dividerThickness
    }

    /// 记住用户拖出来的宽度。只有拖分隔线时才带这个键，窗口自己排版时不算
    func splitViewDidResizeSubviews(_ notification: Notification) {
        guard navBox != nil, notification.userInfo?["NSSplitViewDividerIndex"] != nil else { return }
        navWidth = navBox.frame.width
        if !rightBox.isHidden { rightWidth = rightBox.frame.width }
        guard !selfTest else { return }
        UserDefaults.standard.set(Double(navWidth), forKey: navWidthKey)
        UserDefaults.standard.set(Double(rightWidth), forKey: rightWidthKey)
    }

    // MARK: 左边：聊天 / 终端

    /// 这个项目默认用哪种视图：能聊天的调度员用聊天，其余用终端
    func preferredLeft() -> LeftView {
        if let forced = argValue("--left") {
            return forced == "chat" ? .chat : (forced == "config" ? .config : .terminal)
        }
        if argValue("--command") != nil { return .terminal }
        return Dispatcher.forProject(project)?.hasChat == true || clerkReady ? .chat : .terminal
    }

    /// 打开项目时左边先显示什么：还没选调度员 → 先出团队配置
    func firstLeft() -> LeftView {
        if argValue("--left") != nil || argValue("--command") != nil { return preferredLeft() }
        return Dispatcher.forProject(project) == nil && !clerkReady ? .config : preferredLeft()
    }

    /// 切换左边。opening：要不要发开场交代（打开项目时要，来回切换时不要）
    func showLeft(_ view: LeftView, opening: Bool, chatMode: String = "continue") {
        defer { syncRight(); syncNav() }
        let who = Dispatcher.forProject(project)
        let canChat = who?.hasChat == true || clerkReady
        segment.setEnabled(canChat, forSegment: 0)
        segment.setToolTip(canChat ? nil : "这位调度员暂时只能用终端视图", forSegment: 0)
        segment.setEnabled(who != nil, forSegment: 1)

        if view == .config {
            if leftView == .chat, chatLoadedKey != nil { chatKept = true }  // 聊天不断开：看设置不用让出对话，调度员继续干活
            stopTerminal()
            place(configWeb)
            leftView = .config
            loadConfig()
            segment.selectedSegment = -1
            window.makeFirstResponder(configWeb)
            return
        }
        let target: LeftView = (view == .chat && canChat) ? .chat : .terminal
        // 所有去终端的路都经过这里：聊天页在时先确认调度员是不是在干活
        if target == .terminal, leftView != .terminal, chatLoadedKey != nil, !leaveConfirmed {
            switchToTerminal(opening: opening, chatMode: chatMode)
            return
        }
        leaveConfirmed = false

        if target == .chat {
            place(chatWeb)
            leftView = .chat
            segment.selectedSegment = 0
            window.makeFirstResponder(chatWeb)
            stopTerminal()
            if chatKept, chatMode == "continue", !opening {
                chatKept = false  // 连接和页面都一直在，重连只会把消息再放一遍
            } else {
                loadChat(mode: chatMode, opening: opening)
            }
        } else {
            let chatOpen = leftView != .terminal  // 从设置过来时聊天也还连着
            leftView = .terminal
            segment.selectedSegment = 1
            stopTerminal()
            let fresh = makeTerminal()
            term = fresh
            place(fresh)
            window.makeFirstResponder(fresh)
            let tMode: ChatMode = chatMode == "fresh" ? .fresh : .continueLast
            if chatOpen {
                closeChatOnServer(clearView: false) { [weak self] in
                    guard let self = self, self.leftView == .terminal, self.term === fresh else { return }
                    self.startTerminal(tMode, opening: opening)
                }
            } else {
                startTerminal(tMode, opening: opening)
            }
        }
    }

    func stopTerminal() {
        guard let old = term else { return }
        old.processDelegate = nil
        old.removeFromSuperview()
        let pid = old.process.shellPid
        if pid > 0 {
            kill(-pid, SIGHUP)
            kill(-pid, SIGTERM)
        }
        old.terminate()
        if pid > 0 {
            let deadline = Date().addingTimeInterval(0.15)
            while Date() < deadline && old.process.shellPid > 0 && kill(pid, 0) == 0 {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
            }
        }
        term = nil
    }

    func loadChat(mode: String = "continue", opening: Bool = true) {
        guard leftView == .chat else { return }
        pendingOpening = opening
        let who = Dispatcher.forProject(project)
        guard let port = port, who != nil || clerkReady else {
            if chatLoadedKey == nil {
                chatWeb.loadHTMLString(Self.message("正在连接调度员…", background: "#17181b"), baseURL: nil)
            }
            return  // 数据服务起来后（serverSaid）会再调一次
        }
        let saved = who?.saved ?? ""
        let key = "\(project.path)|\(saved)|\(clerkReady)"
        if mode != "fresh", !opening, chatLoadedKey == key {
            chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.resume()", completionHandler: nil)
            return
        }
        chatLoadedKey = key
        let query = "project=\(urlEncode(project.path))&dispatcher=\(urlEncode(saved))&token=\(chatToken)"
            + "&mode=\(mode)&opening=\(opening ? 1 : 0)&clerk=\(clerkReady ? 1 : 0)"
        if let url = URL(string: "http://127.0.0.1:\(port)/static/chat.html?\(query)") {
            chatWeb.load(URLRequest(url: url))
        }
    }

    func loadConfig() {
        guard leftView == .config else { return }
        guard let port = port else {
            configWeb.loadHTMLString(Self.message("正在打开团队配置…", background: "#17181b"), baseURL: nil)
            return  // 数据服务起来后（serverSaid）会再调一次
        }
        let local = UserDefaults.standard.string(forKey: Dispatcher.key(for: project)) ?? ""
        let query = "project=\(urlEncode(project.path))&token=\(chatToken)&dispatcher=\(urlEncode(local))"
            + "&clerk=\(clerkReady ? 1 : 0)"
        if let url = URL(string: "http://127.0.0.1:\(port)/static/team.html?\(query)") {
            configWeb.load(URLRequest(url: url))
        }
    }

    /// 配置页保存后会跳到 superagent://saved：在这里接住，切到和调度员的对话
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, url.scheme == "superagent" else {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { query.first(where: { $0.name == name })?.value }
        switch url.host {
        case "saved":
            // 没接入的文件夹：调度员存在窗口自己的设置里
            if let who = value("dispatcher"), !who.isEmpty {
                UserDefaults.standard.set(who, forKey: Dispatcher.key(for: project))
            }
            updateTitle()
            let firstConnect = (chatLoadedKey == nil)
            let place = project
            whenChatIdle { [weak self] idle in
                guard let self = self else { return }
                let back: @MainActor @Sendable () -> Void = { [weak self] in
                    // 关连接要几秒，这期间用户可能已经走开了
                    guard let self = self, self.leftView == .config, self.project == place else { return }
                    self.showLeft(self.preferredLeft(), opening: firstConnect)
                    self.loadPanel()
                }
                // 新设置要重连才生效；调度员在干活就不打断，下次连接时生效
                if idle {
                    self.closeChatOnServer(clearView: false, then: back)
                } else if self.preferredLeft() == .terminal {
                    self.switchToTerminal(opening: firstConnect)
                    self.loadPanel()
                } else {
                    back()
                }
            }
        case "card":
            if let id = value("id") { selectCard(id) }
        case "main":
            selectCard(nil)
        case "thread":
            if let id = value("id") { openThread(id) }
        case "new":
            selectCard(nil)
            newChat(nil)
            refreshNavThreads()
        case "settings":
            showLeft(.config, opening: false)
        case "clerk-setup":
            askClerk()
        case "clerk-clear":
            confirmClearClerk()
        default:
            break
        }
    }

    @objc func openTeamConfig(_ sender: Any?) { showLeft(.config, opening: false) }

    /// 告诉数据服务让出这个项目的对话（切到终端、换项目时）
    func closeChatOnServer(clearView: Bool = false, then: (@MainActor @Sendable () -> Void)? = nil) {
        chatKept = false
        if clearView {
            chatLoadedKey = nil
            chatWeb.stopLoading()
            chatWeb.loadHTMLString(Self.message("", background: "#17181b"), baseURL: nil)
        } else {
            chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.pause()", completionHandler: nil)
        }
        guard let port = port, let url = URL(string: "http://127.0.0.1:\(port)/api/chat/close") else {
            then?()
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(chatToken, forHTTPHeaderField: "X-Chat-Token")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["project": project.path])
        if let then = then {
            URLSession.shared.dataTask(with: req) { _, _, _ in
                DispatchQueue.main.async { then() }
            }.resume()
        } else {
            let done = DispatchSemaphore(value: 0)
            URLSession.shared.dataTask(with: req) { _, _, _ in done.signal() }.resume()
            _ = done.wait(timeout: .now() + 2)
        }
    }

    func segmentChanged() {
        if segment.selectedSegment == 0 {
            if leftView != .chat { showLeft(.chat, opening: false) }
        } else if leftView != .terminal {
            switchToTerminal()
        }
    }

    func whenChatIdle(_ then: @escaping @MainActor @Sendable (Bool) -> Void) {
        chatWeb.evaluateJavaScript("!!(window.SuperAgentChat && window.SuperAgentChat.isWorking && window.SuperAgentChat.isWorking())") { result, _ in
            MainActor.assumeIsolated { then((result as? Bool) != true) }
        }
    }

    /// 切到终端要让出对话，调度员还在干活时先问一声
    func switchToTerminal(opening: Bool = false, chatMode: String = "continue") {
        whenChatIdle { [weak self] idle in
            guard let self = self else { return }
            if !idle, self.leftView != .terminal {
                self.segment.selectedSegment = self.leftView == .chat ? 0 : -1  // 确认后才算切过去
                guard self.confirmInterrupt() else { return }
            }
            self.leaveConfirmed = true
            self.showLeft(.terminal, opening: opening, chatMode: chatMode)
        }
    }

    func confirmInterrupt() -> Bool {
        if let answer = argValue("--interrupt-answer") { return answer == "leave" }  // 自测
        let alert = NSAlert()
        alert.messageText = "调度员正在工作"
        alert.informativeText = "切到终端会中断这一轮，还没完成的内容可能丢失。"
        alert.addButton(withTitle: "留在聊天")
        alert.addButton(withTitle: "切到终端")
        return alert.runModal() == .alertSecondButtonReturn
    }

    // MARK: 右边：本机数据服务 + 面板页面

    func startServer() {
        let script = superAgentRoot().appendingPathComponent("desktop/server.py").path
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // 用登录环境找 python3（双击打开的应用拿不到终端里的 PATH）；脚本路径作为 $0 传入，免去转义
        // -B：不留缓存文件（留在应用包里会破坏签名）；口令走环境变量，不出现在进程列表里
        proc.arguments = ["-l", "-c", "exec python3 -B \"$0\" --port 0 --watch-parent", script]
        var env = ProcessInfo.processInfo.environment
        env["SUPERAGENT_CHAT_TOKEN"] = chatToken
        for key in env.keys where key.hasPrefix("CLAUDE") { env.removeValue(forKey: key) }
        proc.environment = env
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.serverSaid(text) }
        }
        proc.terminationHandler = { [weak self] p in
            let code = p.terminationStatus
            DispatchQueue.main.async {
                guard let self = self, self.server === p else { return }  // 旧的那个停了不用管
                self.serverStopped(code: code)
            }
        }
        do {
            try proc.run()
            server = proc
        } catch {
            web.loadHTMLString(Self.message("流转面板启动失败：\(htmlEscape(error.localizedDescription))"), baseURL: nil)
            return
        }
        // 15 秒还没起来，就把情况说出来，而不是一直显示“正在启动”
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            guard let self = self, self.port == nil, !self.quitting else { return }
            let detail = self.serverOutput.isEmpty ? "它没有输出任何内容。" : htmlEscape(String(self.serverOutput.suffix(300)))
            self.web.loadHTMLString(Self.message("流转面板 15 秒还没启动起来。<br><br><small>\(detail)</small>"
                                                 + "<br><br>可以用菜单「面板 → 刷新右边面板」再试一次。"), baseURL: nil)
        }
    }

    func serverSaid(_ text: String) {
        serverOutput += text
        guard port == nil, let line = serverOutput.split(separator: "\n").first(where: { $0.hasPrefix("PORT=") }),
              let value = Int(line.dropFirst(5)) else { return }
        port = value
        if let key = savedClerkKey() {  // 先把密钥交给本机服务，再打开聊天
            postLocal("/api/clerk/config", clerkForm(argValue("--clerk-base") ?? clerkBase, clerkModel, key)) { [weak self] _ in
                self?.openAfterServer()
            }
        } else {
            openAfterServer()
            DispatchQueue.main.async { [weak self] in self?.setupClerk() }
        }
    }

    func openAfterServer() {
        loadPanel()
        loadChat(opening: pendingOpening)
        loadConfig()
    }

    func savedClerkKey() -> String? {
        if argValue("--clerk-try") != nil || argValue("--clerk-ui-test") != nil { return nil }
        if let file = argValue("--clerk-key-file") {  // 自测：不碰用户的钥匙串
            return (try? String(contentsOfFile: file, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return selfTest ? nil : ClerkKey.read()
    }

    // MARK: 行政人员的第一屏

    var clerkBase: String { UserDefaults.standard.string(forKey: "clerkBaseURL") ?? "https://tokenhub.tencentmaas.com/v1" }
    var clerkModel: String { UserDefaults.standard.string(forKey: "clerkModel") ?? "deepseek/deepseek-flash" }

    func postLocal(_ path: String, _ body: [String: Any], done: @escaping @MainActor @Sendable ([String: Any]) -> Void) {
        guard let port = port, let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return done([:]) }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(chatToken, forHTTPHeaderField: "X-Chat-Token")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 40
        URLSession.shared.dataTask(with: request) { data, _, _ in
            let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            DispatchQueue.main.async {
                let parsed = text.data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                done(parsed ?? [:])
            }
        }.resume()
    }

    func clerkForm(_ base: String, _ model: String, _ key: String) -> [String: Any] {
        ["base_url": base, "model": model, "api_key": key]
    }

    /// 先测试，通过了才存进钥匙串并交给本机服务
    func tryClerk(base: String, model: String, key: String, done: @escaping @MainActor @Sendable (String?) -> Void) {
        postLocal("/api/clerk/test", clerkForm(base, model, key)) { [weak self] answer in
            guard let self = self else { return }
            guard answer["ok"] as? Bool == true else {
                return done(answer["error"] as? String ?? "没有测试成功，请再试一次。")
            }
            guard ClerkKey.save(key) else { return done("密钥没能存进钥匙串，请再试一次。") }
            UserDefaults.standard.set(base, forKey: "clerkBaseURL")
            UserDefaults.standard.set(model, forKey: "clerkModel")
            self.postLocal("/api/clerk/config", self.clerkForm(base, model, key)) { _ in done(nil) }
        }
    }

    func setupClerk() {
        if let file = argValue("--clerk-try") { return clerkSelfTest(file) }
        if let file = argValue("--clerk-ui-test") { return clerkFormSelfTest(file) }
    }

    /// 自测：走和第一屏一样的测试和保存，只用单独的钥匙串项，结束后删掉
    func clerkSelfTest(_ file: String) {
        let key = (try? String(contentsOfFile: file, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let base = argValue("--clerk-base") ?? clerkBase
        tryClerk(base: base, model: clerkModel, key: key) { failure in
            let saved = ClerkKey.read() == key
            let report = "失败=\(failure ?? "无") 钥匙串一致=\(saved)\n"
            try? report.write(toFile: file + ".result", atomically: true, encoding: .utf8)
            ClerkKey.delete()
        }
    }

    /// 自测：真的打开第一屏，填好后点按钮，看能不能走完。曾经因为界面卡住一直停在“测试中…”
    func clerkFormSelfTest(_ file: String) {
        let key = (try? String(contentsOfFile: file, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
            let state = self.clerkReady ? "已配好" : "没走完"
            try? "\(state) 表单=\(self.clerkFields == nil ? "已关" : "还开着")\n".write(toFile: file + ".result", atomically: true, encoding: .utf8)
            ClerkKey.delete()
            NSApp.terminate(nil)
        }
        Timer.scheduledTimer(withTimeInterval: 1, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let form = self?.clerkFields else { return }
                form.key.stringValue = key
                form.alert.buttons[0].performClick(nil)
            }
        }
        askClerk()
    }

    var clerkReady = false  // 配好了行政人员：聊天找行政，不连 Codex
    var clerkFields: (alert: NSAlert, base: NSTextField, model: NSTextField, key: NSSecureTextField)?

    func askClerk() {
        let alert = NSAlert()
        alert.messageText = "配置行政人员"
        alert.informativeText = "填了密钥后，聊天和查进度由行政负责。不填也能继续用 Codex 聊天。密钥只存在这台电脑的钥匙串里。"
        let base = NSTextField(string: clerkBase), model = NSTextField(string: clerkModel), key = NSSecureTextField()
        key.placeholderString = "API 密钥"
        let stack = NSStackView(views: [base, model, key])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.frame = NSRect(x: 0, y: 0, width: 340, height: 92)
        for field in [base, model, key] { field.widthAnchor.constraint(equalToConstant: 340).isActive = true }
        alert.accessoryView = stack
        let save = alert.addButton(withTitle: "测试并保存")
        alert.addButton(withTitle: "取消")
        save.target = self
        save.action = #selector(clerkSaveClicked(_:))
        clerkFields = (alert, base, model, key)
        // 用窗口上的弹层：阻塞式弹窗在这里会挡住主线程上的回调，一直停在“测试中…”
        alert.beginSheetModal(for: window) { [weak self] _ in
            MainActor.assumeIsolated { self?.clerkFields = nil }
        }
        alert.window.makeFirstResponder(key)
    }

    func confirmClearClerk() {
        let alert = NSAlert()
        alert.messageText = "停用行政人员？"
        alert.informativeText = "之后聊天会改回用 Codex。密钥会从这台电脑的钥匙串里删掉。"
        alert.addButton(withTitle: "停用")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { [weak self] response in
            MainActor.assumeIsolated {
                guard let self = self, response == .alertFirstButtonReturn else { return }
                ClerkKey.delete()
                self.clerkReady = false
                self.postLocal("/api/clerk/clear", [:]) { [weak self] _ in
                    guard let self = self else { return }
                    self.closeChatOnServer(clearView: true) {
                        self.loadPanel()
                        self.loadChat(opening: false)
                        if self.leftView == .config { self.loadConfig() }
                    }
                }
            }
        }
    }

    @objc func clerkSaveClicked(_ sender: NSButton) {
        guard let form = clerkFields else { return }
        sender.isEnabled = false
        sender.title = "测试中…"
        tryClerk(base: form.base.stringValue, model: form.model.stringValue, key: form.key.stringValue) { [self] failure in
            sender.isEnabled = true
            sender.title = "测试并保存"
            if let failure = failure {
                form.alert.informativeText = failure
            } else {
                self.window.endSheet(form.alert.window)
                self.clerkReady = true
                self.closeChatOnServer(clearView: true) { [weak self] in
                    guard let self = self else { return }
                    self.loadPanel()
                    if self.leftView == .config {
                        self.loadConfig()  // 设置页立刻变成“已配置”
                    } else {
                        self.showLeft(self.preferredLeft(), opening: false)
                    }
                }
            }
        }
    }

    func serverStopped(code: Int32) {
        guard !quitting else { return }
        server = nil
        port = nil  // 旧端口已经没有服务，留着会让聊天页连不回来
        let detail = serverOutput.isEmpty ? "" : "<br><br><small>\(htmlEscape(String(serverOutput.suffix(300))))</small>"
        web.loadHTMLString(Self.message("流转面板停止了（代码 \(code)）。请确认电脑上能用 python3。" + detail), baseURL: nil)
        chatKept = false
        chatLoadedKey = nil
        if leftView == .chat {
            chatWeb.loadHTMLString(Self.message("聊天连接停止了。可以切到终端视图继续。", background: "#17181b"), baseURL: nil)
        }
    }

    /// 加载左栏和右栏（两边用同一份项目和选中状态）
    func loadPanel() {
        guard let port = port, let project = project else { return }
        let who = urlEncode(Dispatcher.forProject(project)?.saved ?? "")
        let base = "project=\(urlEncode(project.path))&dispatcher=\(who)"
        let card = selectedCard.map(urlEncode) ?? ""
        if let url = URL(string: "http://127.0.0.1:\(port)/?\(base)&card=\(card)") {
            web.load(URLRequest(url: url))
        }
        if let url = URL(string: "http://127.0.0.1:\(port)/static/nav.html?\(base)&selected=\(card)&mode=\(navMode())&token=\(chatToken)&chat=\(Dispatcher.forProject(project)?.hasChat == true || clerkReady ? 1 : 0)&clerk=\(clerkReady ? 1 : 0)") {
            navWeb.load(URLRequest(url: url))
        }
    }

    // MARK: 终端视图里的调度员

    func startTerminal(_ mode: ChatMode, opening: Bool) {
        guard let term = term else { return }
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let command: String
        if let custom = argValue("--command") {
            command = custom  // 自测用：只这一次
        } else if let who = Dispatcher.forProject(project) {
            // 只有第一次接入才给 Codex 发话；Cursor、agy 没有别的办法交代角色，仍要发
            let say = opening && who.takesOpening && (firstTime || who.saved != "codex")
            let words = say ? openingWords() : nil
            let role = openingPart { _, fill in fill("background") }
            command = mode == .continueLast ? who.continueCommand(words, role: role) : who.freshCommand(words, role: role)
        } else {
            // 用户还没亲手选调度员：不启动任何 AI，只给一个普通终端和提示
            command = "echo '还没有给这个项目选调度员。请在顶部菜单「项目 → 团队配置…」里选（必须你亲手选）。'"
        }
        let after = "; echo; echo '调度员不在运行。这里现在是普通终端；菜单「对话」可以重新开始。'; exec \(shellQuote(shell)) -l -i"

        var env = ProcessInfo.processInfo.environment
        // 从另一个 Claude 会话里启动时，不把“已经在 Claude 里”的标记传给左边，否则调度员可能拒绝启动
        for key in env.keys where key.hasPrefix("CLAUDE") { env.removeValue(forKey: key) }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extraPaths = ["\(home)/.npm-global/bin", "\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]
        let currentPath = env["PATH"] ?? "/usr/bin:/bin"
        env["PATH"] = (extraPaths + [currentPath]).joined(separator: ":")
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["TERM_PROGRAM"] = "SuperAgent"
        if env["LANG"] == nil { env["LANG"] = "en_US.UTF-8" }  // 中文要靠 UTF-8 才显示得对
        let pairs = env.map { "\($0.key)=\($0.value)" }

        term.startProcess(executable: shell, args: ["-l", "+m", "-c", command + after],
                          environment: pairs, execName: "-" + (shell as NSString).lastPathComponent,
                          currentDirectory: project.path)
    }

    nonisolated func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    nonisolated func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    nonisolated func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
    nonisolated func processTerminated(source: TerminalView, exitCode: Int32?) {
        DispatchQueue.main.async { [weak self] in
            self?.term?.feed(text: "\r\n[终端已结束。菜单「对话」可以重新开始]\r\n")
        }
    }

    // MARK: 菜单

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于超级助手", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏超级助手", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出超级助手", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let fileItem = submenu("文件", [("打开项目…", #selector(openProject(_:)), "o")])
        let recentItem = NSMenuItem(title: "最近打开", action: nil, keyEquivalent: "")
        recentItem.submenu = recentMenu
        fileItem.submenu?.addItem(recentItem)
        rebuildRecentMenu()
        main.addItem(fileItem)

        main.addItem(submenu("编辑", [("拷贝", #selector(NSText.copy(_:)), "c"), ("粘贴", #selector(NSText.paste(_:)), "v"),
                                       ("全选", #selector(NSResponder.selectAll(_:)), "a")]))
        main.addItem(submenu("项目", [("团队配置…", #selector(openTeamConfig(_:)), ",")]))
        main.addItem(submenu("对话", [("接着上一次的对话", #selector(continueChat(_:)), "r"),
                                       ("开始新对话", #selector(newChat(_:)), "n"),
                                       ("聊天视图", #selector(showChatView(_:)), "1"),
                                       ("终端视图", #selector(showTerminalView(_:)), "2")]))
        main.addItem(submenu("面板", [("刷新右栏", #selector(reloadPanel(_:)), "R"),
                                       ("收起或展开右栏", #selector(toggleRight(_:)), "\\")]))
        main.addItem(submenu("窗口", [("最小化", #selector(NSWindow.performMiniaturize(_:)), "m")]))
        NSApp.mainMenu = main
    }

    func submenu(_ title: String, _ items: [(String, Selector, String)]) -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu(title: title)
        for (name, action, key) in items {
            menu.addItem(withTitle: name, action: action, keyEquivalent: key)
        }
        item.submenu = menu
        return item
    }

    @objc func openProject(_ sender: Any?) {
        guard let url = askForProject() else { return }
        switchTo(url)
    }

    @objc func openRecent(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String, isFolder(path) else { return }
        switchTo(URL(fileURLWithPath: path))
    }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        let who = project.flatMap { Dispatcher.forProject($0) }
        switch item.action {
        case #selector(openTeamConfig(_:)): item.state = leftView == .config ? .on : .off
        case #selector(continueChat(_:)), #selector(newChat(_:)):
            return who != nil  // 还没配调度员时，先去团队配置
        case #selector(openRecent(_:)): item.state = (item.representedObject as? String) == project?.path ? .on : .off
        case #selector(showChatView(_:)):
            item.state = leftView == .chat ? .on : .off
            return who?.hasChat == true || clerkReady
        case #selector(showTerminalView(_:)):
            item.state = leftView == .terminal ? .on : .off
            return who != nil
        default: break
        }
        return true
    }

    @objc func continueChat(_ sender: Any?) {
        if leftView == .chat { loadChat(mode: "continue", opening: false) } else { switchToTerminal(opening: true) }
    }

    @objc func newChat(_ sender: Any?) {
        if leftView == .chat {
            if chatLoadedKey != nil {
                chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.startFresh && window.SuperAgentChat.startFresh()", completionHandler: nil)
            } else {
                loadChat(mode: "fresh", opening: true)
            }
        } else {
            switchToTerminal(opening: true, chatMode: "fresh")
        }
    }

    @objc func showChatView(_ sender: Any?) { if leftView != .chat { showLeft(.chat, opening: false) } }
    @objc func showTerminalView(_ sender: Any?) { if leftView != .terminal { switchToTerminal() } }

    /// 刷新右边：数据服务在就重新载入页面；没起来或已停就重新启动它
    @objc func reloadPanel(_ sender: Any?) {
        if port != nil, server?.isRunning == true { loadPanel(); return }
        server?.terminate()
        port = nil
        serverOutput = ""
        web.loadHTMLString(Self.message("正在启动流转面板…"), baseURL: nil)
        startServer()
    }

    // MARK: 自测截图

    func scheduleSnapshot(into dir: String) {
        let delay = argValue("--snapshot-delay").flatMap(Double.init) ?? 8
        if let words = argValue("--say") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
                guard let self = self else { return }
                let script = "var i=document.getElementById('input');i.value=\(self.jsValue(words));document.getElementById('composer').requestSubmit()"
                self.chatWeb.evaluateJavaScript(script, completionHandler: nil)
            }
        }
        if argValue("--switch-test") != nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
                guard let self = self else { return }
                self.segment.selectedSegment = 1
                self.segmentChanged()
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self = self else { return }
                self.segment.selectedSegment = 0
                self.segmentChanged()
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self else { return }
            let folder = URL(fileURLWithPath: dir)
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let shot = Process()   // 整个窗口截一张，能看到半透明左栏和顶部细栏
            shot.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            shot.arguments = ["-x", "-o", "-l", String(self.window.windowNumber), folder.appendingPathComponent("window.png").path]
            try? shot.run()
            shot.waitUntilExit()
            if let term = self.term {
                term.selectAll(nil)  // 终端里的文字存一份，截图拍不准时用文字核对
                try? (term.getSelection() ?? "").write(to: folder.appendingPathComponent("left.txt"), atomically: true, encoding: .utf8)
            }
            let status = "port=\(self.port.map(String.init) ?? "无")\nproject=\(self.project.path)\n"
                + "firstTime=\(self.firstTime)\nleft=\(self.leftView == .chat ? "chat" : (self.leftView == .config ? "config" : "terminal"))\n"
                + "root=\(self.superAgentRoot().path)\n"
                + "window=\(self.window.windowNumber)\nright=\(!self.rightBox.isHidden)\n"
                + "widths=\(self.navBox.frame.width),\(self.leftBox.frame.width),\(self.rightBox.frame.width)\n"
                + "hit=\(self.hitReport())\n"
            try? status.write(to: folder.appendingPathComponent("status.txt"), atomically: true, encoding: .utf8)
            func save(_ image: NSImage?, _ name: String) {
                if let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) {
                    try? rep.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent(name))
                }
            }
            self.navWeb.takeSnapshot(with: nil) { nav, _ in
                save(nav, "nav.png")
                self.web.takeSnapshot(with: nil) { right, _ in
                    save(right, "right.png")
                    if self.leftView == .chat || self.leftView == .config {
                        let page: WKWebView = self.leftView == .chat ? self.chatWeb : self.configWeb
                        page.takeSnapshot(with: nil) { left, _ in save(left, "left.png"); NSApp.terminate(nil) }
                    } else {
                        NSApp.terminate(nil)
                    }
                }
            }
        }
    }

    /// 自测：真的点页面里的按钮，走完整条联动，把每一步看到的写下来
    func scheduleWalk(into file: String) {
        var log = ""
        func report(_ name: String) {
            let line = "\(name)：中间=\(leftView) 选中=\(selectedCard ?? "主对话") 右栏=\(rightBox.isHidden ? "收起" : "展开")"
                + " 宽=\(Int(window.frame.width)) 三段=\(Int(navBox.frame.width)),\(Int(leftBox.frame.width)),\(Int(rightBox.frame.width))"
                + " 切换=\(segment.selectedSegment)"
            log += line + "\n"
        }
        func nav(_ script: String) { navWeb.evaluateJavaScript(script, completionHandler: nil) }
        func threads(_ name: String) {
            navWeb.evaluateJavaScript("[].map.call(document.querySelectorAll('.thread'),function(b){return (b.classList.contains('on')?'*':'')+b.querySelector('.tt').textContent}).join(' | ')") { rows, _ in
                self.chatWeb.evaluateJavaScript("document.getElementById('msgs').innerText.slice(-60)") { text, _ in
                    log += "\(name) 高亮=\(self.navMode()) 列表：\(rows ?? "?") 中间：\(String(describing: text ?? "?").replacingOccurrences(of: "\n", with: " "))\n"
                }
            }
        }
        func resize(_ width: CGFloat) {
            var frame = window.frame
            frame.size.width = width
            window.setFrame(frame, display: true)
        }
        let card = argValue("--walk-card") ?? "CARD-003"
        func say(_ words: String) {
            chatWeb.evaluateJavaScript("var i=document.getElementById('input');i.value='\(words)';document.getElementById('composer').requestSubmit()", completionHandler: nil)
        }
        func asks(_ name: String) {
            chatWeb.evaluateJavaScript("document.querySelectorAll('.ask').length+'张确认卡 干活='+window.SuperAgentChat.isWorking()") { text, _ in
                log += "\(name)：\(String(describing: text ?? "?"))\n"
            }
        }
        let workSteps: [(String, () -> Void)] = [
            ("打开", {}),
            ("说要确认的话", { say("运行一下") }),
            ("等确认", { asks("等确认时") }),
            ("干活时切终端", { self.segment.selectedSegment = 1; self.segmentChanged() }),
            ("看切终端后", { asks("切终端后") }),
            ("打开设置", { nav("document.getElementById('gear').click()") }),
            ("设置里回主对话", { nav("document.getElementById('main').click()") }),
            ("等同步", {}),
            ("看回来后", { asks("设置回来后") }),
        ]
        func clerkView(_ name: String) {
            navWeb.evaluateJavaScript("[].map.call(document.querySelectorAll('.thread'),function(b){return (b.classList.contains('on')?'*':'')+b.querySelector('.tt').textContent}).join(' | ')") { rows, _ in
                self.chatWeb.evaluateJavaScript("document.getElementById('who').textContent+'：'+document.getElementById('msgs').innerText.replace(/\\s+/g,' ').slice(0,70)") { text, _ in
                    log += "\(name) 列表：\(rows ?? "?") 中间：\(String(describing: text ?? "?"))\n"
                }
            }
        }
        let clerkSteps: [(String, () -> Void)] = [
            ("打开", {}),
            ("说第一句", { say("你好，只回答一个好字") }),
            ("等回复", {}),
            ("等回复2", {}),
            ("看第一段", { clerkView("第一段") }),
            ("点新对话", { nav("document.getElementById('newChat').click()") }),
            ("看空对话", { clerkView("空对话") }),
            ("说第二句", { say("再说一句，只回答一个行字") }),
            ("等回复3", {}),
            ("等回复4", {}),
            ("看第二段", { clerkView("第二段") }),
            ("切回第一段", { nav("var b=document.querySelector('.thread:not(.on)');if(b)b.click()") }),
            ("看切换后", { clerkView("切换后") }),
            ("去终端", { self.segment.selectedSegment = 1; self.segmentChanged() }),
            ("回聊天", { self.segment.selectedSegment = 0; self.segmentChanged() }),
            ("看回来后", { clerkView("回来后") }),
        ]
        let allSteps: [(String, () -> Void)] = [
            ("打开", {}),
            ("点卡片", { nav("document.querySelector('[data-id=\"\(card)\"]').click()") }),
            ("点主对话", { nav("document.getElementById('main').click()") }),
            ("点卡片再点设置", { nav("document.querySelector('[data-id=\"\(card)\"]').click()"); DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { nav("document.getElementById('gear').click()") } }),
            ("设置里点主对话", { nav("document.getElementById('main').click()") }),
            ("收起右栏", { self.rightToggle.performClick(nil) }),
            ("展开右栏", { self.rightToggle.performClick(nil) }),
            ("窗口缩到900", { resize(900) }),
            ("窗口放到1280", { resize(1280) }),
            ("窗口缩到900后点卡片", { resize(900); DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { nav("document.querySelector('[data-id=\"\(card)\"]').click()") } }),
            ("搜索没有的词", { nav("var q=document.getElementById('q');q.value='没有这个';q.dispatchEvent(new Event('input'))") }),
            ("清空搜索", { nav("var q=document.getElementById('q');q.value='';q.dispatchEvent(new Event('input'))") }),
            ("切到终端", { self.segment.selectedSegment = 1; self.segmentChanged() }),
            ("切回聊天", { self.segment.selectedSegment = 0; self.segmentChanged() }),
            ("说一句", { self.chatWeb.evaluateJavaScript("var i=document.getElementById('input');i.value='第一段的问题';document.getElementById('composer').requestSubmit()", completionHandler: nil) }),
            ("点新对话", { nav("document.getElementById('newChat').click()") }),
            ("等新对话连上", {}),
            ("再说一句", { self.chatWeb.evaluateJavaScript("var i=document.getElementById('input');i.value='第二段的问题';document.getElementById('composer').requestSubmit()", completionHandler: nil) }),
            ("等列表刷新", {}),
            ("看列表", { threads("新对话后") }),
            ("点历史对话", { nav("var b=document.querySelector('.thread:not(.on)');if(b)b.click()"); DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { threads("点后0.15秒") } }),
            ("等切换", {}),
            ("看切换后", { threads("切换后") }),
        ]
        let steps = CommandLine.arguments.contains("--walk-clerk") ? clerkSteps
            : (CommandLine.arguments.contains("--walk-work") ? workSteps : allSteps)
        for (index, step) in steps.enumerated() {
            let at = 6.0 + Double(index) * 2.0
            DispatchQueue.main.asyncAfter(deadline: .now() + at) { step.1() }
            DispatchQueue.main.asyncAfter(deadline: .now() + at + 1.6) { report(step.0) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 6.0 + Double(steps.count) * 2.0 + 1) {
            try? log.write(toFile: file, atomically: true, encoding: .utf8)
            NSApp.terminate(nil)
        }
    }

    /// 自测：顶部细栏的按钮在标题栏范围里，核对点击真的落到它们身上
    func hitReport() -> String {
        guard let frameView = window.contentView?.superview else { return "无" }
        func hit(_ view: NSView) -> String {
            let point = view.convert(NSPoint(x: view.bounds.midX, y: view.bounds.midY), to: frameView.superview)
            return frameView.hitTest(point).map { String(describing: type(of: $0)) } ?? "无"
        }
        func hitTop(_ view: NSView) -> String {
            let point = view.convert(NSPoint(x: 40, y: view.isFlipped ? 20 : view.bounds.maxY - 20), to: frameView.superview)
            return frameView.hitTest(point).map { String(describing: type(of: $0)) } ?? "无"
        }
        return "切换=\(hit(segment)) 收起=\(hit(rightToggle)) 右栏顶部=\(hitTop(web))"
    }
}

MainActor.assumeIsolated {  // 程序入口本来就在主线程上
    let app = NSApplication.shared
    let delegate = AppDelegate()  // app.run() 一直运行到退出，这个变量也一直有效
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
