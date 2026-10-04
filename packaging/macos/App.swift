// LeXWeft Lite の Mac アプリ。
// セットアップで作った .venv の lexweft を起こし、その画面を WKWebView で出すだけの殻。
// 資料・意味層・検索はすべて lexweft 側にある。この殻は状態を持たない。
import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandlerWithReply {
    let appName = "LeXWeft Lite"
    var window: NSWindow!
    var webView: WKWebView!
    var engine: Process?
    var log: FileHandle?
    var poll: Timer?
    var readyURL: URL?
    var baseURL: URL?
    var startedAt = Date()
    var quitting = false
    let fm = FileManager.default

    /// セットアップ時に Info.plist へ書き込んだ lexweft コマンドの絶対パス
    var command: URL? {
        guard let path = Bundle.main.object(forInfoDictionaryKey: "LWLiteCommand") as? String, !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path)
    }

    /// 保存先 (lexweft と同じ規則: LEXWEFT_HOME、無ければ ~/LeXWeftLite)
    var dataDirectory: URL {
        if let raw = ProcessInfo.processInfo.environment["LEXWEFT_HOME"], !raw.isEmpty {
            return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath, isDirectory: true)
        }
        return fm.homeDirectoryForCurrentUser.appendingPathComponent("LeXWeftLite", isDirectory: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        // 画面の「フォルダを選ぶ」から呼ばれる (window.webkit.messageHandlers.pickFolder)
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "pickFolder")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.autoresizingMask = [.width, .height]
        webView.underPageBackgroundColor = .windowBackgroundColor   // 読み込み中に白く光らないように (OS の明暗に合わせる)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.minSize = NSSize(width: 900, height: 600)
        window.contentView = webView
        window.setFrameAutosaveName("lexweft-lite-main")
        if !window.setFrameUsingName("lexweft-lite-main") { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startEngine()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        stopEngine()
    }

    // MARK: メニュー
    func buildMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "\(appName) について", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "保存先のフォルダを開く", action: #selector(openData), keyEquivalent: "")
        appMenu.addItem(withTitle: "ログを開く", action: #selector(openLog), keyEquivalent: "")
        appMenu.addItem(withTitle: "再読み込み", action: #selector(reloadPage), keyEquivalent: "r")
        appMenu.addItem(withTitle: "再起動", action: #selector(restart), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "\(appName) を隠す", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "\(appName) を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        bar.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "編集")
        editMenu.addItem(withTitle: "元に戻す", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "やり直す", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "切り取り", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼り付け", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべて選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        bar.addItem(editItem)

        let winItem = NSMenuItem()
        let winMenu = NSMenu(title: "ウインドウ")
        winMenu.addItem(withTitle: "しまう", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winMenu.addItem(withTitle: "拡大／縮小", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winMenu.addItem(withTitle: "閉じる", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = winMenu
        bar.addItem(winItem)
        NSApp.mainMenu = bar
        NSApp.windowsMenu = winMenu
    }

    // MARK: lexweft の起動と停止
    func startEngine() {
        stopEngine()
        baseURL = nil
        splash("起動しています…")
        do {
            guard let cmd = command, fm.isExecutableFile(atPath: cmd.path) else {
                throw fail("lexweft が見つかりません。LeXWeft Lite のフォルダを動かした場合は、そのフォルダで install.sh を実行し直してください。")
            }
            try fm.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            let ready = dataDirectory.appendingPathComponent("ready-\(UUID().uuidString).json")
            readyURL = ready
            let logFile = dataDirectory.appendingPathComponent("app.log")
            if !fm.fileExists(atPath: logFile.path) { fm.createFile(atPath: logFile.path, contents: nil) }
            let handle = try FileHandle(forWritingTo: logFile)
            handle.seekToEndOfFile()
            log = handle

            let child = Process()
            child.executableURL = cmd
            child.arguments = ["serve", "--no-browser", "--ready-file", ready.path,
                               "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
            var env = ProcessInfo.processInfo.environment
            env.removeValue(forKey: "PYTHONHOME")
            env.removeValue(forKey: "PYTHONPATH")
            child.environment = env
            child.currentDirectoryURL = dataDirectory
            child.standardOutput = handle
            child.standardError = handle
            child.terminationHandler = { [weak self] task in
                DispatchQueue.main.async {
                    guard let self = self, !self.quitting, self.engine === task else { return }
                    self.poll?.invalidate(); self.poll = nil
                    self.showError("LeXWeft Lite の処理が止まりました。メニューの「ログを開く」で app.log を確かめてください。")
                }
            }
            engine = child
            try child.run()
            startedAt = Date()
            poll = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in self?.checkReady() }
        } catch {
            showError(error.localizedDescription)
        }
    }

    func checkReady() {
        guard let ready = readyURL else { return }
        if let data = try? Data(contentsOf: ready),
           let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let value = object["url"] as? String, let url = URL(string: value),
           url.scheme == "http", url.host == "127.0.0.1", (url.port ?? 0) > 0 {
            poll?.invalidate(); poll = nil
            baseURL = url
            try? fm.removeItem(at: ready)
            readyURL = nil
            webView.load(URLRequest(url: url))
            return
        }
        let waited = Date().timeIntervalSince(startedAt)
        if waited > 10, waited < 10.2 { splash("起動しています…\n初回は OS の検査で時間がかかることがあります") }
        if waited > 90 {
            stopEngine()
            showError("90 秒以内に起動できませんでした。メニューの「ログを開く」で app.log を確かめてください。")
        }
    }

    func stopEngine() {
        poll?.invalidate(); poll = nil
        if let task = engine {
            engine = nil
            task.terminationHandler = nil
            if task.isRunning {
                task.terminate()
                let deadline = Date().addingTimeInterval(3)
                while task.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
                if task.isRunning { kill(task.processIdentifier, SIGKILL) }
            }
        }
        try? log?.close(); log = nil
        if let ready = readyURL { try? fm.removeItem(at: ready); readyURL = nil }
    }

    // MARK: 画面の制限 (外のページはブラウザで開く)
    func isLocal(_ url: URL?) -> Bool {
        guard let url = url, let base = baseURL else { return false }
        return url.scheme == base.scheme && url.host == base.host && url.port == base.port
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        if navigationAction.shouldPerformDownload && isLocal(url) {
            decisionHandler(.download)
            return
        }
        if url == nil || isLocal(url) || url?.scheme == "about" {
            decisionHandler(.allow)
            return
        }
        if let external = url, external.scheme == "http" || external.scheme == "https" {
            NSWorkspace.shared.open(external)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, !isLocal(url) { NSWorkspace.shared.open(url) }
        return nil
    }

    // MARK: ファイル選択・確認・保存 (WKWebView は既定ではどれも出さない)
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "やめる")
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let url = panel.url else { completionHandler(nil); return }
            try? FileManager.default.removeItem(at: url)
            completionHandler(url)
        }
    }

    // MARK: フォルダを選ぶ
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.name == "pickFolder", isLocal(message.frameInfo.request.url) else { replyHandler(nil, "not allowed"); return }
        let panel = NSOpenPanel()
        panel.title = "取り込むフォルダを選ぶ"
        panel.prompt = "このフォルダを選ぶ"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = fm.homeDirectoryForCurrentUser.appendingPathComponent("Documents")
        panel.beginSheetModal(for: window) { result in
            replyHandler(result == .OK ? panel.url?.path : nil, nil)
        }
    }

    // MARK: 操作
    @objc func reloadPage() { if baseURL != nil { webView.reload() } else { startEngine() } }
    @objc func restart() { startEngine() }
    @objc func openData() { NSWorkspace.shared.open(dataDirectory) }
    @objc func openLog() { NSWorkspace.shared.open(dataDirectory.appendingPathComponent("app.log")) }

    @objc func showAbout() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let alert = NSAlert()
        alert.messageText = "\(appName) \(version)"
        alert.informativeText = """
        資料を蓄積し、課題と解決手段のつながりを作って見て、LLM から引けるローカルアプリ。
        保存先: \(dataDirectory.path)
        画面との通信はこの Mac の中 (127.0.0.1) だけです。
        """
        alert.runModal()
    }

    func splash(_ message: String) {
        let html = """
        <html lang="ja"><head><meta name="color-scheme" content="light dark"><style>body{background:#f7f7f5;color:#1f2328}@media (prefers-color-scheme: dark){body{background:#15171a;color:#e6e6e6}}</style></head><body style="font:15px -apple-system;padding:64px">
        <h2 style="font-weight:600">LeXWeft <span style="color:#0e7490">Lite</span></h2><p style="white-space:pre-line">\(message)</p></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func fail(_ message: String) -> NSError {
        NSError(domain: "LeXWeftLite", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "起動できませんでした"
        alert.informativeText = message
        alert.addButton(withTitle: "再起動")
        alert.addButton(withTitle: "ログを開く")
        alert.addButton(withTitle: "終了")
        switch alert.runModal() {
        case .alertFirstButtonReturn: startEngine()
        case .alertSecondButtonReturn: openLog()
        default: NSApp.terminate(nil)
        }
    }
}

let delegate = AppDelegate()
let application = NSApplication.shared
application.delegate = delegate
application.run()
