import AppKit
import WebKit
import SwiftTerm

// Cửa sổ AppKit: WKWebView (board) ở trên, terminal PTY thật (SwiftTerm) ở dưới. Toàn bộ quét/dọn nằm ở maccleaner.py.
let args = CommandLine.arguments
#if DEBUG
// `swift run` (debug): tìm source qua #filePath.
let sourceRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../..").standardized
#else
// Bản release (.app phát hành): không nhúng đường dẫn máy build; ngoài bundle thì dùng thư mục hiện tại.
let sourceRoot = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
#endif
let inBundle = Bundle.main.url(forResource: "maccleaner", withExtension: "py") != nil
let resRoot = inBundle ? Bundle.main.resourceURL! : sourceRoot
let script = resRoot.appendingPathComponent("maccleaner.py")
let htmlURL = resRoot.appendingPathComponent("Web/board.html")
/// reports/ và logs/: biến MACCLEANER_DATA nếu có (dùng chung với lệnh mac-cleaner); không thì cạnh source
/// khi `swift run`, trong Application Support khi chạy từ .app.
let dataDir = ProcessInfo.processInfo.environment["MACCLEANER_DATA"].map { URL(fileURLWithPath: $0) } ?? (inBundle
    ? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("MacCleaner")
    : sourceRoot)

/// Chạy maccleaner.py: stdout (JSON) gom lại; stderr (tiến độ) đẩy TỪNG DÒNG ra `onLine` ngay khi có.
/// Đọc hai pipe song song nên không bao giờ kẹt khi một bên đầy.
func python(_ arguments: [String], onLine: @escaping (String) -> Void = { _ in }) -> Result<String, Error> {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    p.arguments = ["python3", script.path] + arguments
    var env = ProcessInfo.processInfo.environment
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
    env["MACCLEANER_DATA"] = dataDir.path
    env["PYTHONUNBUFFERED"] = "1"
    p.environment = env
    let out = Pipe(), err = Pipe()
    p.standardOutput = out; p.standardError = err
    let lock = NSLock()
    var outData = Data(), errBuf = Data(), errTail: [String] = []
    out.fileHandleForReading.readabilityHandler = { h in
        let d = h.availableData
        lock.lock(); outData.append(d); lock.unlock()
    }
    err.fileHandleForReading.readabilityHandler = { h in
        let d = h.availableData
        guard !d.isEmpty else { return }
        lock.lock(); errBuf.append(d)
        var lines: [String] = []
        while let nl = errBuf.firstIndex(of: 10) {
            lines.append(String(decoding: errBuf[errBuf.startIndex..<nl], as: UTF8.self))
            errBuf.removeSubrange(errBuf.startIndex...nl)
        }
        errTail = Array((errTail + lines).suffix(20))
        lock.unlock()
        lines.forEach(onLine)
    }
    do { try p.run() } catch {
        out.fileHandleForReading.readabilityHandler = nil; err.fileHandleForReading.readabilityHandler = nil
        return .failure(error)
    }
    p.waitUntilExit()
    out.fileHandleForReading.readabilityHandler = nil; err.fileHandleForReading.readabilityHandler = nil
    lock.lock()
    outData.append(out.fileHandleForReading.readDataToEndOfFile())
    let rest = String(decoding: errBuf + err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
    let tail = (errTail + [rest]).joined(separator: "\n")
    lock.unlock()
    rest.split(separator: "\n").forEach { onLine(String($0)) }
    guard p.terminationStatus == 0 else {
        return .failure(NSError(domain: "maccleaner", code: Int(p.terminationStatus),
                                userInfo: [NSLocalizedDescriptionKey: "python3 thoát mã \(p.terminationStatus): " + String(tail.suffix(400))]))
    }
    return .success(String(decoding: outData, as: UTF8.self))
}

/// Ghi một dòng log ra file logs/app-<ngày>.log (cạnh report) — để xem lại sau khi đóng app.
let logFile: URL = {
    let f = DateFormatter(); f.dateFormat = "yyyyMMdd"
    let dir = dataDir.appendingPathComponent("logs")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir.appendingPathComponent("app-\(f.string(from: Date())).log")
}()
func appendLogFile(_ line: String) {
    let f = DateFormatter(); f.dateFormat = "HH:mm:ss"
    let data = Data("\(f.string(from: Date()))  \(line)\n".utf8)
    if let h = try? FileHandle(forWritingTo: logFile) { h.seekToEndOfFile(); h.write(data); try? h.close() }
    else { try? data.write(to: logFile) }
}

/// Python 3 thật có sẵn chưa? (Homebrew, hoặc Command Line Tools của Apple — máy Mac mới không có;
/// /usr/bin/python3 khi thiếu CLT chỉ là shim bật trình cài rồi thoát lỗi.)
func pythonAvailable() -> Bool {
    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"] where FileManager.default.isExecutableFile(atPath: p) {
        return true
    }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
    p.arguments = ["-p"]
    p.standardOutput = Pipe(); p.standardError = Pipe()
    do { try p.run(); p.waitUntilExit() } catch { return false }
    return p.terminationStatus == 0
}

/// Thiếu Python 3: hỏi người dùng rồi mở trình cài Command Line Tools chính thức của Apple.
func offerInstallCommandLineTools() {
    let alert = NSAlert()
    alert.messageText = "Cần Python 3"
    alert.informativeText = "Mac Cleaner dùng Python 3 có trong Command Line Tools của Apple (miễn phí). Bấm Cài đặt, chờ cài xong rồi mở lại Mac Cleaner."
    alert.addButton(withTitle: "Cài đặt")
    alert.addButton(withTitle: "Thoát")
    if alert.runModal() == .alertFirstButtonReturn {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        p.arguments = ["--install"]
        try? p.run()
    }
    NSApp.terminate(nil)
}

func latestReport() -> URL? {
    let dir = dataDir.appendingPathComponent("reports")
    let files = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
    // chỉ report của `scan` (report-<yyyyMMdd>-<HHmmss>.json); bỏ report-ciel-*.json của công cụ khác ghi chung thư mục
    return files.filter { $0.lastPathComponent.range(of: #"^report-\d{8}-\d{6}\.json$"#, options: .regularExpression) != nil }
        .max { ($0.path) < ($1.path) }  // tên có timestamp yyyyMMdd-HHmmss nên sort chuỗi = sort thời gian
}

/// Quote đơn cho zsh (an toàn với dấu cách, `'`, `$`).
func shq(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

/// Script của terminal (nhúng và Terminal.app): `tail -F` log của app; Ctrl-C → shell đăng nhập ở thư mục dữ liệu.
func terminalScript() -> String {
    """
    #!/bin/zsh
    cd \(shq(dataDir.path)) 2>/dev/null || cd ~
    printf '\\033]0;Mac Cleaner — log\\007'
    printf '%s\\n' 'Mac Cleaner · log trực tiếp:' \(shq(logFile.path))
    printf '%s\\n' 'Ctrl-C để thôi xem log và dùng shell ở đây. Lệnh:' \(shq("  python3 " + shq(script.path) + " scan | clean | status …")) ''
    touch \(shq(logFile.path))
    trap ':' INT
    tail -n 200 -F \(shq(logFile.path))
    trap - INT
    echo
    exec "${SHELL:-/bin/zsh}" -l

    """
}

/// Ghi script terminal vào logs/ (quyền 700). Trả đường dẫn hoặc nil.
func writeTerminalScript(_ name: String) -> URL? {
    let dir = dataDir.appendingPathComponent("logs"), file = dir.appendingPathComponent(name)
    do {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try terminalScript().write(to: file, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        return file
    } catch { return nil }
}

/// Payload `clean:{"ids":[…], "mode":"trash"|"permanent", "yes":bool, "force":bool}` của board → tham số maccleaner.py.
/// Id chỉ 10 ký tự hex; "permanent" không đi kèm --force (maccleaner.py cũng từ chối). Sai định dạng → nil.
func cleanArguments(report: String, payload: String) -> [String]? {
    guard let obj = try? JSONSerialization.jsonObject(with: Data(payload.utf8)) as? [String: Any],
          let ids = obj["ids"] as? [String], !ids.isEmpty,
          ids.allSatisfy({ $0.range(of: "^[0-9a-f]{10}$", options: .regularExpression) != nil }) else { return nil }
    let mode = obj["mode"] as? String ?? "trash", force = obj["force"] as? Bool == true
    guard mode == "trash" || mode == "permanent", !(mode == "permanent" && force) else { return nil }
    var a = ["clean", "--report", report, "--ids", ids.joined(separator: ","), "--json"]
    if mode == "permanent" { a += ["--mode", "permanent"] }
    if force { a.append("--force") }  // xoá đích danh mục kiểm kê/bảo vệ (người dùng đã xác nhận trên board)
    if obj["yes"] as? Bool == true { a.append("--yes") }
    return a
}

/// Chuỗi JSON → literal JS an toàn để nhúng vào evaluateJavaScript.
func jsLiteral(_ s: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [s], options: [])
    return String(String(data: data, encoding: .utf8)!.dropFirst().dropLast())
}

/// Panel terminal dưới board: thanh tiêu đề (thu gọn, mở lại phiên, Terminal.app) + PTY thật chạy `terminalScript()`.
final class TerminalPanel: NSView, LocalProcessTerminalViewDelegate {
    let term = LocalProcessTerminalView(frame: .zero)
    private let status = NSTextField(labelWithString: "")
    private let restart = NSButton(title: "Mở lại", target: nil, action: nil)
    private let toggle = NSButton(title: "", target: nil, action: nil)
    var onToggle: () -> Void = {}
    var onOpenExternal: () -> Void = {}
    static let barHeight: CGFloat = 28
    private var appearanceObs: NSKeyValueObservation?

    override init(frame: NSRect) {
        super.init(frame: frame)
        let title = NSTextField(labelWithString: "Terminal")
        title.font = .systemFont(ofSize: 12, weight: .semibold)
        status.font = .systemFont(ofSize: 11); status.textColor = .secondaryLabelColor
        restart.bezelStyle = .inline; restart.target = self; restart.action = #selector(start); restart.isHidden = true
        let ext = NSButton(title: "Terminal.app", target: self, action: #selector(external))
        ext.bezelStyle = .inline; ext.toolTip = "Mở Terminal.app thật: cùng log trực tiếp, Ctrl-C để gõ lệnh"
        toggle.bezelStyle = .inline; toggle.target = self; toggle.action = #selector(toggled)
        let bar = NSStackView(views: [title, status, restart, NSView(), ext, toggle])
        bar.orientation = .horizontal; bar.spacing = 8; bar.edgeInsets = NSEdgeInsets(top: 0, left: 12, bottom: 0, right: 8)
        bar.setHuggingPriority(.defaultLow, for: .horizontal)
        for v in [bar, term] as [NSView] { v.translatesAutoresizingMaskIntoConstraints = false; addSubview(v) }
        NSLayoutConstraint.activate([
            bar.topAnchor.constraint(equalTo: topAnchor), bar.leadingAnchor.constraint(equalTo: leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: trailingAnchor), bar.heightAnchor.constraint(equalToConstant: Self.barHeight),
            term.topAnchor.constraint(equalTo: bar.bottomAnchor), term.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 6),
            term.trailingAnchor.constraint(equalTo: trailingAnchor), term.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
        term.processDelegate = self
        term.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        applyColors()
        appearanceObs = NSApp.observe(\.effectiveAppearance) { [weak self] _, _ in DispatchQueue.main.async { self?.applyColors() } }
        setCollapsed(false)
    }
    required init?(coder: NSCoder) { fatalError() }

    /// Màu theo theme mặc định của board (mac-cleaner): tối #0d0d0f/#f4f4f5, sáng #ffffff/#111114.
    func applyColors() {
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let bg = dark ? NSColor(red: 0.051, green: 0.051, blue: 0.059, alpha: 1) : .white
        term.nativeBackgroundColor = bg
        term.nativeForegroundColor = dark ? NSColor(red: 0.957, green: 0.957, blue: 0.961, alpha: 1) : NSColor(red: 0.067, green: 0.067, blue: 0.078, alpha: 1)
        wantsLayer = true; layer?.backgroundColor = bg.cgColor
    }

    func setCollapsed(_ c: Bool) {
        toggle.title = c ? "Hiện ▴" : "Thu gọn ▾"
        term.isHidden = c
    }

    /// Chạy (lại) phiên: zsh chạy script tail -F log → Ctrl-C → shell đăng nhập ở thư mục dữ liệu.
    @objc func start() {
        guard let file = writeTerminalScript("terminal.zsh") else { status.stringValue = "không ghi được script terminal"; return }
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
        env["MACCLEANER_DATA"] = dataDir.path
        env["TERM"] = "xterm-256color"; env["LANG"] = env["LANG"] ?? "en_US.UTF-8"
        restart.isHidden = true; status.stringValue = ""
        term.startProcess(executable: "/bin/zsh", args: [file.path], environment: env.map { "\($0.key)=\($0.value)" },
                          currentDirectory: dataDir.path)
    }
    @objc private func toggled() { onToggle() }
    @objc private func external() { onOpenExternal() }
    func focus() { window?.makeFirstResponder(term) }

    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
    func processTerminated(source: TerminalView, exitCode: Int32?) {
        DispatchQueue.main.async { self.status.stringValue = "phiên đã kết thúc"; self.restart.isHidden = false }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler, NSSplitViewDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var split: NSSplitView?
    var panel: TerminalPanel?
    var termCollapsed = UserDefaults.standard.bool(forKey: "termCollapsed") {
        didSet { UserDefaults.standard.set(termCollapsed, forKey: "termCollapsed") }
    }
    var reportPath: String?
    let snapshotPath = args.firstIndex(of: "--snapshot").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }
    /// `--view <tính năng|proposal>` (kèm --snapshot): chụp khu Tính năng đó / popup Đề xuất xoá thay vì Tổng quan.
    let snapshotView = args.firstIndex(of: "--view").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }

    func applicationDidFinishLaunching(_ note: Notification) {
        guard pythonAvailable() else { offerInstallCommandLineTools(); return }
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "cleaner")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: snapshotPath == nil ? 940 : 2200),
                          styleMask: snapshotPath == nil ? [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView] : [.borderless],
                          backing: .buffered, defer: false)
        window.title = "Mac Cleaner"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = .black
        window.minSize = NSSize(width: 900, height: 600)
        if snapshotPath == nil { buildSplit() } else { window.contentView = web }
        if snapshotPath == nil {
            window.setFrameAutosaveName("MacCleanerWindow")
            window.center()
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else {
            // Chụp ảnh: cửa sổ ngoài màn hình, không giành focus, không lưu vị trí cửa sổ.
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.orderBack(nil)
        }
        web.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // MARK: Terminal nhúng

    /// Board ở trên, terminal ở dưới; kéo thanh chia để đổi cỡ (vị trí lưu lại), thu gọn còn thanh tiêu đề.
    func buildSplit() {
        let sv = NSSplitView()
        sv.isVertical = false; sv.dividerStyle = .thin; sv.delegate = self
        let p = TerminalPanel(frame: NSRect(x: 0, y: 0, width: 1440, height: 280))
        p.onToggle = { [weak self] in self?.setTerminal(collapsed: !(self?.termCollapsed ?? false)) }
        p.onOpenExternal = { [weak self] in self?.openRealTerminal() }
        sv.addArrangedSubview(web); sv.addArrangedSubview(p)
        sv.setHoldingPriority(.defaultLow, forSubviewAt: 0); sv.setHoldingPriority(.defaultHigh, forSubviewAt: 1)
        window.contentView = sv
        split = sv; panel = p
        sv.layoutSubtreeIfNeeded()
        let h = UserDefaults.standard.double(forKey: "termHeight")
        sv.setPosition(sv.bounds.height - (termCollapsed ? TerminalPanel.barHeight : (h > 80 ? h : 280)), ofDividerAt: 0)
        p.setCollapsed(termCollapsed)
        p.start()
    }

    func setTerminal(collapsed: Bool) {
        guard let sv = split, let p = panel else { return }
        if p.frame.height > TerminalPanel.barHeight + 20 { UserDefaults.standard.set(Double(p.frame.height), forKey: "termHeight") }  // nhớ cỡ đang mở
        termCollapsed = collapsed
        p.setCollapsed(collapsed)
        let h = UserDefaults.standard.double(forKey: "termHeight")
        sv.setPosition(sv.bounds.height - (collapsed ? TerminalPanel.barHeight : (h > 80 ? h : 280)), ofDividerAt: 0)
    }

    /// Message `terminal` của board / ⌘L: mở panel (nếu đang thu gọn) và focus terminal.
    @objc func showTerminal() {
        if termCollapsed { setTerminal(collapsed: false) }
        panel?.focus()
    }
    @objc func toggleTerminal() {
        if termCollapsed { showTerminal() } else { setTerminal(collapsed: true) }
    }

    func splitView(_ sv: NSSplitView, constrainMinCoordinate p: CGFloat, ofSubviewAt i: Int) -> CGFloat { 200 }
    func splitView(_ sv: NSSplitView, constrainMaxCoordinate p: CGFloat, ofSubviewAt i: Int) -> CGFloat { sv.bounds.height - TerminalPanel.barHeight }
    func splitViewDidResizeSubviews(_ n: Notification) {
        guard let p = panel else { return }
        let collapsed = p.frame.height <= TerminalPanel.barHeight + 4
        if collapsed != termCollapsed { termCollapsed = collapsed; p.setCollapsed(collapsed) }
    }

    /// Một dòng log: lên terminal trong app + ghi file. Gọi từ thread nào cũng được.
    func log(_ line: String) {
        appendLogFile(line)
        DispatchQueue.main.async { self.web?.evaluateJavaScript("window.log && window.log(\(jsLiteral(line)))") }
    }

    /// Mở app: hiện report gần nhất ngay (quét toàn máy mất vài phút); chưa có thì quét.
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        log("Mac Cleaner \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev") · dữ liệu: \(dataDir.path)")
        log("Log ghi vào \(logFile.path)")
        if let url = latestReport(), let json = try? String(contentsOf: url, encoding: .utf8) {
            reportPath = url.path
            log("Mở report gần nhất: \(url.lastPathComponent) — bấm Quét lại để quét mới")
            render(json)
        } else {
            log("Chưa có report — quét lần đầu toàn máy (thường 1–5 phút)")
            scan(first: true)
        }
    }

    func render(_ json: String) {
        // MC_SCRIPT: đường dẫn maccleaner.py thật, để board in đúng lệnh cho người dùng copy vào Terminal.
        // `--view proposal`: chụp popup Đề xuất xoá; tên khác: khu Tính năng đó.
        let view = snapshotPath == nil ? "" : snapshotView.map {
            $0 == "proposal" ? "; window.openProposal()" : "; window.showFeature && window.showFeature(\(jsLiteral($0)))"
        } ?? ""
        web.evaluateJavaScript("window.MC_SCRIPT = \(jsLiteral(script.path)); window.render(JSON.parse(\(jsLiteral(json))))" + view) { _, error in
            if let error { NSLog("render failed: \(error)") }
            if let path = self.snapshotPath { self.snapshot(to: path) }
        }
    }

    func fail(_ error: Error) {
        log("✗ \(error.localizedDescription)")
        web.evaluateJavaScript("window.scanFailed(\(jsLiteral(error.localizedDescription)))")
    }

    @objc func rescan() { scan(first: false) }

    func scan(first: Bool) {
        web.evaluateJavaScript("window.scanStarted && window.scanStarted(\(first))")
        if first { DispatchQueue.main.async { if self.termCollapsed { self.setTerminal(collapsed: false) } } }  // lần đầu: hiện tiến độ
        log("$ python3 maccleaner.py scan --json")
        let t0 = Date()
        DispatchQueue.global(qos: .userInitiated).async {
            let r = python(["scan", "--json"], onLine: { self.log($0) })
            DispatchQueue.main.async {
                switch r {
                case .success(let json):
                    self.reportPath = latestReport()?.path
                    self.log(String(format: "Hiện kết quả (%.0fs)", Date().timeIntervalSince(t0)))
                    self.render(json)
                case .failure(let e): self.fail(e)
                }
            }
        }
    }

    func clean(_ payload: String) {
        guard let report = reportPath, let a = cleanArguments(report: report, payload: payload) else {
            log("✗ Yêu cầu dọn không hợp lệ (report/id/mode)")
            web.evaluateJavaScript("window.scanFailed && window.scanFailed('yêu cầu dọn không hợp lệ')")
            return
        }
        log("$ python3 maccleaner.py " + a.joined(separator: " "))
        DispatchQueue.global(qos: .userInitiated).async {
            let r = python(a, onLine: { self.log($0) })
            DispatchQueue.main.async {
                switch r {
                case .success(let json): self.web.evaluateJavaScript("window.cleaned(JSON.parse(\(jsLiteral(json))))")
                case .failure(let e): self.fail(e)
                }
            }
        }
    }

    /// `--snapshot out.png`: render một lần, lưu ảnh rồi thoát (kiểm UI không cần quyền màn hình).
    func snapshot(to path: String) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            self.web.takeSnapshot(with: nil) { image, error in
                guard let image, let tiff = image.tiffRepresentation,
                      let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) else {
                    NSLog("snapshot failed: \(String(describing: error))"); exit(1)
                }
                do { try png.write(to: URL(fileURLWithPath: path)); exit(0) } catch { NSLog("\(error)"); exit(1) }
            }
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? String else { return }
        if body == "scan" { scan(first: false) }
        else if body.hasPrefix("clean:") { clean(String(body.dropFirst(6))) }
        else if body.hasPrefix("copy:") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(String(body.dropFirst(5)), forType: .string)
        } else if body == "terminal" {
            showTerminal()
        } else if body.hasPrefix("reveal:") {
            let url = URL(fileURLWithPath: String(body.dropFirst(7)))
            if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        }
    }

    /// Bật Terminal.app thật: script `.command` trong logs/ hiện log trực tiếp (`tail -F`), Ctrl-C → shell tương tác
    /// ở thư mục dữ liệu để gõ lệnh mac-cleaner. Mở `.command` bằng Terminal nên không cần quyền Automation.
    func openRealTerminal() {
        guard let cmd = writeTerminalScript("open-terminal.command") else { log("✗ Không tạo được script mở Terminal"); return }
        log("$ open -a Terminal \(cmd.path)")
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        NSWorkspace.shared.open([cmd], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration()) { _, error in
            if let error { self.log("✗ Không mở được Terminal: \(error.localizedDescription)") }
        }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quét lại", action: #selector(rescan), keyEquivalent: "r").target = self
        appMenu.addItem(withTitle: "Hiện/ẩn Terminal", action: #selector(toggleTerminal), keyEquivalent: "l").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Mac Cleaner", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(args.contains("--snapshot") ? .accessory : .regular)  // chụp ảnh: không hiện icon Dock
app.run()
