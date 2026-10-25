import Cocoa
import CoreGraphics
import Darwin
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var logHandle: FileHandle?
    private let host = "127.0.0.1"
    private var port = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: configuration)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "咚咚"
        window.center()
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // FFmpeg 作为 App 内子进程抓屏；先由宿主 App 主动触发系统授权提示。
        let screenAccessBefore = CGPreflightScreenCaptureAccess()
        NSLog("屏幕录制权限状态（启动前）: \(screenAccessBefore)")
        if !screenAccessBefore {
            _ = CGRequestScreenCaptureAccess()
            NSLog("已请求屏幕录制权限；如果系统设置未出现新条目，请删除旧 Dongdong 后重新添加当前 App")
        }

        guard let selectedPort = availablePort() else {
            showError("没有可用的本机端口")
            NSApp.terminate(nil)
            return
        }
        port = selectedPort
        startBackend()
    }

    private func startBackend() {
        guard let resources = Bundle.main.resourceURL else {
            showError("找不到 App 资源目录")
            return
        }
        let python = resources.appendingPathComponent("python/bin/python3")
        let appRoot = resources.appendingPathComponent("app")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            showError("App 内 Python 运行时缺失：\(python.path)")
            return
        }

        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "dongdong", "web", "--host", host, "--port", String(port)]
        process.currentDirectoryURL = appRoot
        var environment = ProcessInfo.processInfo.environment
        let packagePath = appRoot.appendingPathComponent("apps/device-mac")
        environment["PYTHONPATH"] = packagePath.path
        environment["DONGDONG_MEMORY_DIR"] = environment["DONGDONG_MEMORY_DIR"] ??
            (FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".dongdong/.memory").path)
        let bundledLib = resources.appendingPathComponent("lib").path
        environment["DYLD_LIBRARY_PATH"] = [bundledLib, environment["DYLD_LIBRARY_PATH"] ?? ""]
            .filter { !$0.isEmpty }.joined(separator: ":")
        process.environment = environment
        let logPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/dongdong-app.log")
        FileManager.default.createFile(atPath: logPath.path, contents: nil)
        logHandle = FileHandle(forWritingAtPath: logPath.path)
        process.standardOutput = logHandle
        process.standardError = logHandle
        process.terminationHandler = { [weak self] process in
            guard process.terminationStatus != 0 else { return }
            DispatchQueue.main.async {
                self?.showError("咚咚后端启动失败，请查看 ~/Library/Logs/dongdong-app.log")
            }
        }
        do {
            try process.run()
            backend = process
        } catch {
            showError("启动内置 Python 服务失败：\(error.localizedDescription)")
            return
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(20)
            while Date() < deadline {
                if self.portIsOpen() {
                    DispatchQueue.main.async {
                        let url = URL(string: "http://\(self.host):\(self.port)/")!
                        self.webView.load(URLRequest(url: url))
                    }
                    return
                }
                if !process.isRunning { break }
                Thread.sleep(forTimeInterval: 0.15)
            }
            DispatchQueue.main.async {
                self.showError("咚咚后端未能在 20 秒内启动，请查看 ~/Library/Logs/dongdong-app.log")
            }
        }
    }

    private func availablePort() -> Int? {
        for candidate in 8766...8866 {
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            guard fd >= 0 else { continue }
            var address = sockaddr_in()
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = in_port_t(candidate).bigEndian
            address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            close(fd)
            if result == 0 { return candidate }
        }
        return nil
    }

    private func portIsOpen() -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(port).bigEndian
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        return withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
            }
        }
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "咚咚启动失败"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        guard let backend, backend.isRunning else { return }
        backend.terminate()
        let deadline = Date().addingTimeInterval(3)
        while backend.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if backend.isRunning { kill(backend.processIdentifier, SIGKILL) }
        backend.waitUntilExit()
        try? logHandle?.close()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
