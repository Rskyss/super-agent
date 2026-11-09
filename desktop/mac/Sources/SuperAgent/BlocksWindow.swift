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
    private var generation = 0  // 每次加载、关窗口、服务停了都加一；慢的旧请求回来对不上号就不管

    init(app: AppDelegate) {
        self.app = app
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
        if s == "tencent" || s == "bailian" { return true }
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
