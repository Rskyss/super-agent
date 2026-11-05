// 超级助手桌面窗口
//
// 打开一个项目文件夹：
//   - 没接入过的 git 项目：自动接入（项目里只多一个 .collab/，并加进不提交名单），不复制协作包；
//   - 旧方式装过协作包的项目：问用户要不要删掉项目里的旧协作包，再迁移（记录一条不动）；
//   - 不是 git 项目：不接入，左边照常能聊。
// 左边：和这个项目的调度员对话（用户亲手选，每个项目分别记住；没选不启动任何 AI）。
//       顶部「聊天 | 终端」切换：Codex 默认聊天视图（一问一答、图片直接显示、允许/拒绝按钮），
//       其他调度员先用终端视图。切换时前一个视图先让出对话，后一个接着同一个对话，不重复开场交代。
// 右边：流转面板（只读项目的 .collab/）。
// 左右两个页面都由本机数据服务提供；聊天地址要带窗口启动时生成的一次性口令。
// 协作包与面板优先用源目录（编译时所在的仓库）里的那份，源目录不在时才用应用里自带的。
//
// 自测用参数（正常双击打开时用不到；都只对这一次有效，不改用户的任何设置）：
//   --project <文件夹>          指定项目，不弹选择框
//   --dispatcher <codex|claude> 这一次用哪位调度员
//   --command <命令>            左边改跑这条命令（终端视图）
//   --left <chat|terminal>      这一次左边先显示哪种视图
//   --old-package <keep|remove> 自动回答“要不要删旧协作包”
//   --snapshot <文件夹>         启动后把左右两边各截一张图存进去，然后退出
//   --snapshot-delay <秒>       截图前等多久，默认 8 秒
import AppKit
import SwiftTerm
import WebKit

let lastProjectKey = "lastProject"
let recentProjectsKey = "recentProjects"
let panelBackground = NSColor(srgbRed: 0x10 / 255.0, green: 0x1a / 255.0, blue: 0x2b / 255.0, alpha: 1)
let chatBackground = NSColor(srgbRed: 0x0d / 255.0, green: 0x15 / 255.0, blue: 0x24 / 255.0, alpha: 1)
let terminalBackground = NSColor(srgbRed: 0x0a / 255.0, green: 0x0f / 255.0, blue: 0x18 / 255.0, alpha: 1)

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

    static func key(for project: URL) -> String { "dispatcher:" + project.path }

    static func forProject(_ project: URL) -> Dispatcher? {
        if let once = argValue("--dispatcher") { return Dispatcher(saved: once) }  // 自测：只这一次
        let config = project.appendingPathComponent(".collab/配置.json")
        if let data = try? Data(contentsOf: config),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let team = json["team"] as? [String: Any], let who = team["dispatcher"] as? String, !who.isEmpty {
            return Dispatcher(saved: who)
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

    func freshCommand(_ opening: String?) -> String {
        let say = opening.map { " " + shellQuote($0) } ?? ""
        switch saved {
        case "codex": return "codex --no-daemon" + say
        case "cursor": return "agent" + say                       // Cursor 的命令行叫 agent
        case "agy": return opening == nil ? "agy" : "agy -i" + say  // -i：带着开场话进入对话
        default: return String(saved.dropFirst("custom:".count))
        }
    }

    /// 接着这个项目里最近一次的对话；还没聊过（几秒内就退出）时，开一个新的
    func continueCommand(_ opening: String?) -> String {
        let say = opening.map { " " + shellQuote($0) } ?? ""
        let resume: String
        switch saved {
        case "codex": resume = "codex resume --no-daemon --last" + say  // 只找当前文件夹里的对话；不走后台服务，关掉立刻让出
        case "cursor": resume = "agent --continue" + say
        case "agy": resume = "agy --continue" + (opening == nil ? "" : " -i" + say)
        default: return freshCommand(nil)
        }
        return "s=$(date +%s); \(resume); c=$?; "
            + "if [ $c -ne 0 ] && [ $(( $(date +%s) - s )) -lt 5 ]; then "
            + "echo '（这个项目还没有可以接着的对话，开一个新的）'; \(freshCommand(opening)); fi"
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSSplitViewDelegate, NSMenuItemValidation,
    WKNavigationDelegate, LocalProcessTerminalViewDelegate {
    var window: NSWindow!
    var split: NSSplitView!
    var leftBox: NSView!                 // 顶部切换条 + 下面的内容
    var leftContent: NSView!
    var segment: NSSegmentedControl!
    var term: LocalProcessTerminalView?  // 终端视图时才有
    var chatWeb: WKWebView!              // 聊天视图
    var configWeb: WKWebView!            // 团队配置
    var web: WKWebView!                  // 右边流转面板
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
        showLeft(firstLeft(), opening: withOpening)
        if let dir = argValue("--snapshot") { scheduleSnapshot(into: dir) }
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

    func openingWords() -> String {
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
        // 终端里只有一句话能交代：标记 + 这次的任务 + 调度员守则
        return (json["marker"] as? String ?? "") + fill(firstTime ? "first" : "every") + "\n\n" + fill("background")
    }

    // MARK: 窗口

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.minSize = NSSize(width: 760, height: 480)
        window.backgroundColor = panelBackground
        window.appearance = NSAppearance(named: .darkAqua)
        updateTitle()
        window.center()
        window.setFrameAutosaveName("SuperAgentMainWindow")

        split = NSSplitView()
        split.isVertical = true
        split.dividerStyle = .thin
        split.delegate = self
        split.autosaveName = "SuperAgentSplit"

        buildLeft()
        web = makeWeb(message: "正在启动流转面板…")
        split.addArrangedSubview(leftBox)
        split.addArrangedSubview(web)
        split.setHoldingPriority(.defaultLow, forSubviewAt: 0)
        split.setHoldingPriority(.defaultHigh, forSubviewAt: 1)
        window.contentView = split
        window.makeKeyAndOrderFront(nil)

        // 第一次打开时左边占六成；以后用你拖过的位置（autosaveName 自动记住）
        if UserDefaults.standard.object(forKey: "NSSplitView Subview Frames SuperAgentSplit") == nil {
            split.setPosition(split.bounds.width * 0.6, ofDividerAt: 0)
        }
    }

    /// 左边：顶部一条「聊天 | 终端」切换，下面放当前的视图
    func buildLeft() {
        leftBox = NSView(frame: NSRect(x: 0, y: 0, width: 760, height: 800))
        leftBox.wantsLayer = true
        leftBox.layer?.backgroundColor = chatBackground.cgColor

        segment = NSSegmentedControl(labels: ["聊天", "终端"], trackingMode: .selectOne,
                                     target: self, action: #selector(segmentChanged(_:)))
        segment.translatesAutoresizingMaskIntoConstraints = false
        segment.controlSize = .small
        leftBox.addSubview(segment)

        leftContent = NSView()
        leftContent.translatesAutoresizingMaskIntoConstraints = false
        leftBox.addSubview(leftContent)
        NSLayoutConstraint.activate([
            segment.topAnchor.constraint(equalTo: leftBox.topAnchor, constant: 8),
            segment.leadingAnchor.constraint(equalTo: leftBox.leadingAnchor, constant: 12),
            leftContent.topAnchor.constraint(equalTo: segment.bottomAnchor, constant: 8),
            leftContent.leadingAnchor.constraint(equalTo: leftBox.leadingAnchor),
            leftContent.trailingAnchor.constraint(equalTo: leftBox.trailingAnchor),
            leftContent.bottomAnchor.constraint(equalTo: leftBox.bottomAnchor),
        ])
        chatWeb = makeWeb(message: "正在连接调度员…", background: "#0d1524")
        configWeb = makeWeb(message: "正在打开团队配置…", background: "#0d1524")
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
        view.nativeForegroundColor = NSColor(srgbRed: 0.79, green: 0.83, blue: 0.89, alpha: 1)
        return view
    }

    func makeWeb(message: String, background: String = "#101a2b") -> WKWebView {
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 520, height: 800), configuration: WKWebViewConfiguration())
        view.setValue(false, forKey: "drawsBackground")  // 加载前不闪白
        view.loadHTMLString(Self.message(message, background: background), baseURL: nil)
        return view
    }

    static func message(_ text: String, background: String = "#101a2b") -> String {
        "<!DOCTYPE html><html><body style=\"margin:0;height:100vh;display:grid;place-items:center;"
            + "background:\(background);color:#8796ad;font:14px -apple-system,'PingFang SC',sans-serif;padding:24px;"
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

    // 左边最少 320、右边最少 360，谁都不会被拖没
    func splitView(_ splitView: NSSplitView, constrainMinCoordinate proposedMinimumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat { max(proposedMinimumPosition, 320) }

    func splitView(_ splitView: NSSplitView, constrainMaxCoordinate proposedMaximumPosition: CGFloat,
                   ofSubviewAt dividerIndex: Int) -> CGFloat { min(proposedMaximumPosition, splitView.bounds.width - 360) }

    // MARK: 左边：聊天 / 终端

    /// 这个项目默认用哪种视图：能聊天的调度员用聊天，其余用终端
    func preferredLeft() -> LeftView {
        if let forced = argValue("--left") {
            return forced == "chat" ? .chat : (forced == "config" ? .config : .terminal)
        }
        if argValue("--command") != nil { return .terminal }
        return Dispatcher.forProject(project)?.hasChat == true ? .chat : .terminal
    }

    /// 打开项目时左边先显示什么：还没选调度员 → 先出团队配置
    func firstLeft() -> LeftView {
        if argValue("--left") != nil || argValue("--command") != nil { return preferredLeft() }
        return Dispatcher.forProject(project) == nil ? .config : preferredLeft()
    }

    /// 切换左边。opening：要不要发开场交代（打开项目时要，来回切换时不要）
    func showLeft(_ view: LeftView, opening: Bool, chatMode: String = "continue") {
        if view == .config {
            if leftView == .chat { closeChatOnServer() }
            stopTerminal()
            place(configWeb)
            leftView = .config
            loadConfig()
            segment.selectedSegment = -1
            window.makeFirstResponder(configWeb)
            return
        }
        let canChat = Dispatcher.forProject(project)?.hasChat == true
        let target: LeftView = (view == .chat && canChat) ? .chat : .terminal
        segment.setEnabled(canChat, forSegment: 0)
        segment.setToolTip(canChat ? nil : "这位调度员暂时只能用终端视图", forSegment: 0)

        if target == .chat {
            place(chatWeb)
            leftView = .chat
            segment.selectedSegment = 0
            window.makeFirstResponder(chatWeb)
            stopTerminal()
            loadChat(mode: chatMode, opening: opening)
        } else {
            let wasChat = (leftView == .chat)
            leftView = .terminal
            segment.selectedSegment = 1
            stopTerminal()
            let fresh = makeTerminal()
            term = fresh
            place(fresh)
            window.makeFirstResponder(fresh)
            let tMode: ChatMode = chatMode == "fresh" ? .fresh : .continueLast
            if wasChat {
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
        guard let port = port, let who = Dispatcher.forProject(project) else {
            if chatLoadedKey == nil {
                chatWeb.loadHTMLString(Self.message("正在连接调度员…", background: "#0d1524"), baseURL: nil)
            }
            return  // 数据服务起来后（serverSaid）会再调一次
        }
        let key = "\(project.path)|\(who.saved)"
        if mode != "fresh", !opening, chatLoadedKey == key {
            chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.resume()", completionHandler: nil)
            return
        }
        chatLoadedKey = key
        let query = "project=\(urlEncode(project.path))&dispatcher=\(urlEncode(who.saved))&token=\(chatToken)"
            + "&mode=\(mode)&opening=\(opening ? 1 : 0)"
        if let url = URL(string: "http://127.0.0.1:\(port)/static/chat.html?\(query)") {
            chatWeb.load(URLRequest(url: url))
        }
    }

    func loadConfig() {
        guard leftView == .config else { return }
        guard let port = port else {
            configWeb.loadHTMLString(Self.message("正在打开团队配置…", background: "#0d1524"), baseURL: nil)
            return  // 数据服务起来后（serverSaid）会再调一次
        }
        let local = UserDefaults.standard.string(forKey: Dispatcher.key(for: project)) ?? ""
        let query = "project=\(urlEncode(project.path))&token=\(chatToken)&dispatcher=\(urlEncode(local))"
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
        guard url.host == "saved" else { return }
        // 没接入的文件夹：调度员存在窗口自己的设置里
        if let who = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?
            .first(where: { $0.name == "dispatcher" })?.value, !who.isEmpty {
            UserDefaults.standard.set(who, forKey: Dispatcher.key(for: project))
        }
        updateTitle()
        showLeft(preferredLeft(), opening: true)
        loadPanel()
    }

    @objc func openTeamConfig(_ sender: Any?) { showLeft(.config, opening: false) }

    /// 告诉数据服务让出这个项目的对话（切到终端、换项目时）
    func closeChatOnServer(clearView: Bool = false, then: (@MainActor @Sendable () -> Void)? = nil) {
        if clearView {
            chatLoadedKey = nil
            chatWeb.stopLoading()
            chatWeb.loadHTMLString(Self.message("", background: "#0d1524"), baseURL: nil)
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

    @objc func segmentChanged(_ sender: NSSegmentedControl) {
        showLeft(sender.selectedSegment == 0 ? .chat : .terminal, opening: false)
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
        loadPanel()
        loadChat(opening: pendingOpening)
        loadConfig()
    }

    func serverStopped(code: Int32) {
        guard !quitting else { return }
        server = nil
        let detail = serverOutput.isEmpty ? "" : "<br><br><small>\(htmlEscape(String(serverOutput.suffix(300))))</small>"
        web.loadHTMLString(Self.message("流转面板停止了（代码 \(code)）。请确认电脑上能用 python3。" + detail), baseURL: nil)
        if leftView == .chat {
            chatLoadedKey = nil
            chatWeb.loadHTMLString(Self.message("聊天连接停止了。可以切到终端视图继续。", background: "#0d1524"), baseURL: nil)
        }
    }

    func loadPanel() {
        guard let port = port, let project = project else { return }
        let who = urlEncode(Dispatcher.forProject(project)?.saved ?? "")
        if let url = URL(string: "http://127.0.0.1:\(port)/?project=\(urlEncode(project.path))&dispatcher=\(who)") {
            web.load(URLRequest(url: url))
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
            let words = (opening && who.takesOpening) ? openingWords() : nil
            command = mode == .continueLast ? who.continueCommand(words) : who.freshCommand(words)
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
        main.addItem(submenu("面板", [("刷新右边面板", #selector(reloadPanel(_:)), "R")]))
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
            return who?.hasChat == true
        case #selector(showTerminalView(_:)):
            item.state = leftView == .terminal ? .on : .off
            return who != nil
        default: break
        }
        return true
    }

    @objc func continueChat(_ sender: Any?) {
        if leftView == .chat { loadChat(mode: "continue", opening: false) } else { showLeft(.terminal, opening: true) }
    }

    @objc func newChat(_ sender: Any?) {
        if leftView == .chat {
            if chatLoadedKey != nil {
                chatWeb.evaluateJavaScript("window.SuperAgentChat && window.SuperAgentChat.startFresh && window.SuperAgentChat.startFresh()", completionHandler: nil)
            } else {
                loadChat(mode: "fresh", opening: true)
            }
        } else {
            showLeft(.terminal, opening: true, chatMode: "fresh")
        }
    }

    @objc func showChatView(_ sender: Any?) { showLeft(.chat, opening: false) }
    @objc func showTerminalView(_ sender: Any?) { showLeft(.terminal, opening: false) }

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
        if argValue("--switch-test") != nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
                guard let self = self else { return }
                self.segment.selectedSegment = 1
                self.segmentChanged(self.segment)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self = self else { return }
                self.segment.selectedSegment = 0
                self.segmentChanged(self.segment)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self else { return }
            let folder = URL(fileURLWithPath: dir)
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            if let term = self.term {
                term.selectAll(nil)  // 终端里的文字存一份，截图拍不准时用文字核对
                try? (term.getSelection() ?? "").write(to: folder.appendingPathComponent("left.txt"), atomically: true, encoding: .utf8)
            }
            let status = "port=\(self.port.map(String.init) ?? "无")\nproject=\(self.project.path)\n"
                + "firstTime=\(self.firstTime)\nleft=\(self.leftView == .chat ? "chat" : (self.leftView == .config ? "config" : "terminal"))\n"
                + "root=\(self.superAgentRoot().path)\n"
            try? status.write(to: folder.appendingPathComponent("status.txt"), atomically: true, encoding: .utf8)
            func save(_ image: NSImage?, _ name: String) {
                if let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) {
                    try? rep.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent(name))
                }
            }
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

MainActor.assumeIsolated {  // 程序入口本来就在主线程上
    let app = NSApplication.shared
    let delegate = AppDelegate()  // app.run() 一直运行到退出，这个变量也一直有效
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
