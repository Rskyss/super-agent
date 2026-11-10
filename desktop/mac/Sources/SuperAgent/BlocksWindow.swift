// 积木库窗口（59 号单）：单独一个窗口，里面整个是积木库页面 blocks.html。
//   - 每次从关着变成开着：先用钥匙串里的密钥把行政连上（不发测试话、不花额度），再加载页面；
//   - 页面检测通过的密钥交过来（blocksKey），存进钥匙串，和项目窗口同一套账号；
//   - 关掉只关自己；项目窗口藏着时它也关了，就退出（AppDelegate.blocksWindowClosed）。
import AppKit
import WebKit

/// 网页视图会一直拉住收消息的对象：中间隔一个只弱引用积木库窗口的转发，窗口才放得掉
private final class WeakKeyForwarder: NSObject, WKScriptMessageHandler {
    weak var target: BlocksWindow?
    init(_ target: BlocksWindow) { self.target = target }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}

@MainActor
final class BlocksWindow: NSObject, NSWindowDelegate, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
    weak var app: AppDelegate?
    private var window: NSWindow?
    private var web: WKWebView?
    private var gaveKeys = false  // 这次运行里交过密钥给本机服务
    private var generation = 0  // 每次加载、关窗口、服务停了都加一；慢的旧请求回来对不上号就不管

    init(app: AppDelegate) {
        self.app = app
        super.init()
        // 项目窗口或积木库存、删了密钥：开着就再交一次整份，再叫页面重新取“已保存”
        NotificationCenter.default.addObserver(self, selector: #selector(keysChanged(_:)), name: ClerkKey.changed, object: nil)
    }

    /// 这次运行里交过密钥（积木库开过），钥匙串一变就再交整份，关着也交：本机服务里的始终和钥匙串一样，
    /// 删掉的马上没了。从没开过积木库就不交（用户 2026-11-09 定：打开时才交）
    @objc private func keysChanged(_ note: Notification) {
        guard gaveKeys, let app = app, let port = app.port else { return }
        let mine = generation
        syncKeys(port: port, token: app.chatToken) { [weak self] in
            guard let self = self, self.generation == mine, self.isOpen else { return }
            self.web?.evaluateJavaScript("window.blocksKeysChanged && window.blocksKeysChanged()", completionHandler: nil)
        }
    }

    /// 把钥匙串里本应用存的密钥（账号名合法的）整份交给本机服务，只记在内存里（60 号单，用户 2026-11-09 定）。
    /// 密钥只在这次请求里用；成没成都接着往下走
    func syncKeys(port: Int, token: String, then: @escaping @MainActor @Sendable () -> Void) {
        gaveKeys = true
        var keys: [String: String] = [:]
        for account in ClerkKey.accounts() where Self.isValidAccount(account) {
            if let key = ClerkKey.read(account: account), !key.isEmpty { keys[account] = key }
        }
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/blocks/keys") else { return then() }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "X-Chat-Token")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["keys": keys])
        request.timeoutInterval = 10
        URLSession.shared.dataTask(with: request) { _, _, _ in
            DispatchQueue.main.async { then() }
        }.resume()
    }

    var isVisible: Bool { window?.isVisible == true }
    /// 开着（含最小化到程序坞）
    var isOpen: Bool { isVisible || window?.isMiniaturized == true }

    func show() {
        let wasOpen = isOpen
        if window == nil {
            let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
                               styleMask: [.titled, .closable, .miniaturizable, .resizable],
                               backing: .buffered, defer: false)
            win.title = "积木库"
            win.isReleasedWhenClosed = false
            win.minSize = NSSize(width: 760, height: 480)
            win.appearance = NSAppearance(named: .darkAqua)
            win.backgroundColor = panelBackground
            win.center()
            win.setFrameAutosaveName(app?.selfTest == true ? "SuperAgentBlocksSelfTest" : "SuperAgentBlocksWindow")

            let config = WKWebViewConfiguration()
            config.userContentController.add(WeakKeyForwarder(self), name: "blocksKey")
            let view = WKWebView(frame: win.contentRect(forFrameRect: win.frame), configuration: config)
            view.setValue(false, forKey: "drawsBackground")  // 加载前不闪白
            view.uiDelegate = self
            view.navigationDelegate = self
            win.contentView = view
            win.delegate = self
            window = win
            web = view
        }
        window?.makeKeyAndOrderFront(nil)
        if !wasOpen, let app = app { load(port: app.port, token: app.chatToken) }
    }

    /// 先问本机服务行政连上没有；没连上、钥匙串里有这家的密钥，就先连上，再加载页面
    func load(port: Int?, token: String) {
        guard let web = web else { return }
        generation += 1
        let mine = generation
        guard let port = port else {
            let words = app?.server == nil ? Self.stoppedWords : "正在启动本机服务…"
            web.loadHTMLString(AppDelegate.message(words), baseURL: nil)
            return
        }
        web.loadHTMLString(AppDelegate.message("正在连接本机服务…"), baseURL: nil)  // 问话、重连最多要几十秒，别空着
        let base = "http://127.0.0.1:\(port)"
        let page: @MainActor @Sendable () -> Void = { [weak self] in
            guard let self = self, self.generation == mine, self.isOpen,
                  let url = URL(string: base + "/static/blocks.html?token=" + token) else { return }
            self.web?.load(URLRequest(url: url))
        }
        guard let stateURL = URL(string: base + "/api/blocks/state?token=" + token) else { return page() }
        var ask = URLRequest(url: stateURL)
        ask.timeoutInterval = 10
        // 先把钥匙串交给本机服务（60 号单），页面一打开就知道哪些存过；再走 59 号单的自动重连：
        // 行政没连上、钥匙串里有这家的密钥就先连上（不发测试话），成没成都加载页面
        syncKeys(port: port, token: token) { [weak self] in
            guard let self = self, self.generation == mine else { return }
            URLSession.shared.dataTask(with: ask) { [weak self] data, _, _ in
                let state = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                let account = state.flatMap { s -> String? in
                    guard s["setting"] is [String: Any], s["connected"] as? Bool == false else { return nil }
                    return s["key_account"] as? String
                } ?? ""
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == mine else { return }
                    guard !account.isEmpty, let key = ClerkKey.read(account: account), !key.isEmpty,
                          let url = URL(string: base + "/api/blocks/connect") else { return page() }
                    // 自动重连不带 check：不发测试话，不多花额度（55、56 号单约定）
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue(token, forHTTPHeaderField: "X-Chat-Token")
                    request.httpBody = try? JSONSerialization.data(withJSONObject: ["api_key": key])
                    request.timeoutInterval = 40
                    URLSession.shared.dataTask(with: request) { _, _, _ in  // 成没成都往下走，页面自己会显示
                        DispatchQueue.main.async { page() }
                    }.resume()
                }
            }.resume()
        }
    }

    static let stoppedWords = "本机服务停了，可以用菜单「面板 → 刷新右栏」再试一次。"

    func serverStopped() {
        generation += 1
        guard isOpen else { return }
        web?.loadHTMLString(AppDelegate.message(Self.stoppedWords), baseURL: nil)
    }

    func windowWillClose(_ notification: Notification) {
        generation += 1
        if let blank = URL(string: "about:blank") { web?.load(URLRequest(url: blank)) }  // 停掉页面每几秒的查询
        app?.blocksWindowClosed()
    }

    // MARK: 页面交来的密钥

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "blocksKey", let web = web, message.webView === web, let port = app?.port else { return }
        // 只收本机服务的页面（积木库页面和嵌在里面的聊天页同源）
        let origin = message.frameInfo.securityOrigin
        guard origin.protocol == "http", origin.host == "127.0.0.1", origin.port == port,
              let body = message.body as? [String: Any],
              let account = body["account"] as? String, let raw = body["key"] as? String else { return }
        let key = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, key.count <= 500, !key.contains(where: { $0.isNewline }),
              Self.isValidAccount(account) else { return }
        ClerkKey.save(key, account: account)
    }

    /// 和 collab/providers.py 的 account_for、AppDelegate.clerkAccount 一套账号名
    static func isValidAccount(_ s: String) -> Bool {
        if s == "tencent" || s == "bailian" || s == "tavily" { return true }
        if s.hasPrefix("cli:") {
            // 普通工具的密钥 cli:<卡编号>:<名字>，和本机服务 server.py 的 _SAVED_ACCOUNT 同一条规矩（沙盒接入 65 号单）
            let parts = s.split(separator: ":", omittingEmptySubsequences: false)
            guard parts.count == 3 else { return false }
            let id = parts[1], env = parts[2]
            guard (1...40).contains(id.count), let first = id.unicodeScalars.first,
                  ("a"..."z").contains(first) || ("0"..."9").contains(first),
                  id.unicodeScalars.allSatisfy({ ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "-" })
            else { return false }
            guard (2...64).contains(env.count), let head = env.unicodeScalars.first, ("A"..."Z").contains(head),
                  env.unicodeScalars.allSatisfy({ ("A"..."Z").contains($0) || ("0"..."9").contains($0) || $0 == "_" })
            else { return false }
            return true
        }
        guard s.hasPrefix("other:") else { return false }
        let host = s.dropFirst(6)
        return (1...253).contains(host.count) && host.unicodeScalars.allSatisfy {
            ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "." || $0 == "-"
        }
    }

    // MARK: 页面里的确认框、提示框：弹在积木库窗口上；回调必须调用，不然页面会卡住

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable () -> Void) {
        guard let host = webView.window else { completionHandler(); return }
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: host) { _ in
            MainActor.assumeIsolated { completionHandler() }
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping @MainActor @Sendable (Bool) -> Void) {
        guard let host = webView.window else { completionHandler(false); return }
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: host) { response in
            MainActor.assumeIsolated { completionHandler(response == .alertFirstButtonReturn) }
        }
    }

    /// 积木库页面用不到 superagent:// 地址，一律取消、不做事
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.scheme == "superagent" ? .cancel : .allow)
    }
}
