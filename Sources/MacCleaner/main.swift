import AppKit
import WebKit

// Cửa sổ AppKit + WKWebView. Toàn bộ quét/dọn nằm ở maccleaner.py.
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
    return files.filter { $0.lastPathComponent.hasPrefix("report-") && $0.pathExtension == "json" }
        .max { ($0.path) < ($1.path) }  // tên có timestamp yyyyMMdd-HHmmss nên sort chuỗi = sort thời gian
}

/// Chuỗi JSON → literal JS an toàn để nhúng vào evaluateJavaScript.
func jsLiteral(_ s: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [s], options: [])
    return String(String(data: data, encoding: .utf8)!.dropFirst().dropLast())
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var reportPath: String?
    let snapshotPath = args.firstIndex(of: "--snapshot").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }

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
        window.contentView = web
        window.setFrameAutosaveName("MacCleanerWindow")
        if snapshotPath == nil { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

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
        web.evaluateJavaScript("window.render(JSON.parse(\(jsLiteral(json))))") { _, error in
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
        guard let report = reportPath,
              let obj = try? JSONSerialization.jsonObject(with: Data(payload.utf8)) as? [String: Any],
              let ids = obj["ids"] as? [String], !ids.isEmpty,
              ids.allSatisfy({ $0.range(of: "^[0-9a-f]{10}$", options: .regularExpression) != nil }) else { return }
        var a = ["clean", "--report", report, "--ids", ids.joined(separator: ","), "--json"]
        if obj["force"] as? Bool == true { a.append("--force") }  // xoá đích danh mục kiểm kê/bảo vệ (người dùng đã xác nhận)
        if obj["yes"] as? Bool == true { a.append("--yes") }
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
        } else if body.hasPrefix("reveal:") {
            let url = URL(fileURLWithPath: String(body.dropFirst(7)))
            if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quét lại", action: #selector(rescan), keyEquivalent: "r").target = self
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
app.setActivationPolicy(.regular)
app.run()
