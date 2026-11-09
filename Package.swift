// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MacCleaner",
    platforms: [.macOS(.v13)],
    dependencies: [
        // Terminal PTY nhúng (panel dưới board): log quét/dọn trực tiếp + shell để gõ lệnh mac-cleaner.
        // Cùng thư viện terminal mà Ciel Monitor dùng; chỉ app Swift cần, lõi Python vẫn chỉ thư viện chuẩn.
        .package(url: "https://github.com/migueldeicaza/SwiftTerm", exact: "1.99.3"),
    ],
    targets: [
        .executableTarget(name: "MacCleaner", dependencies: [.product(name: "SwiftTerm", package: "SwiftTerm")]),
    ]
)
