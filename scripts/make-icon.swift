// Render the app icon (same mark as the board logo: ring + accent pulse line) and pack assets/AppIcon.icns.
//   swift scripts/make-icon.swift            # writes assets/AppIcon.icns
//   swift scripts/make-icon.swift out.png    # also saves the 1024px master for a visual check
// The .icns is committed; re-run only when the design below changes. make-app.sh copies it into the bundle.
import AppKit

let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let icns = root.appendingPathComponent("assets/AppIcon.icns")

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255, blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

/// Draw the icon on a 1024-unit canvas scaled to `size` pixels.
func render(_ size: Int) -> Data {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)

    // macOS icon grid: 824pt rounded square centred on the 1024 canvas, soft drop shadow below.
    let plate = CGRect(x: 100, y: 100, width: 824, height: 824)
    let shape = CGPath(roundedRect: plate, cornerWidth: 185, cornerHeight: 185, transform: nil)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: color(0x000000, 0.35))
    ctx.addPath(shape); ctx.setFillColor(color(0x0d0d0f)); ctx.fillPath()
    ctx.restoreGState()

    // Plate: the board's dark surface, slightly lighter at the top.
    ctx.saveGState()
    ctx.addPath(shape); ctx.clip()
    let plateGradient = CGGradient(colorsSpace: space, colors: [color(0x25252c), color(0x0b0b0d)] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(plateGradient, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
    // faint accent glow behind the mark
    let glow = CGGradient(colorsSpace: space, colors: [color(0x4ade80, 0.20), color(0x4ade80, 0)] as CFArray, locations: [0, 1])!
    ctx.drawRadialGradient(glow, startCenter: CGPoint(x: 512, y: 470), startRadius: 0, endCenter: CGPoint(x: 512, y: 470), endRadius: 400, options: [])
    ctx.restoreGState()
    ctx.addPath(CGPath(roundedRect: plate.insetBy(dx: 2, dy: 2), cornerWidth: 183, cornerHeight: 183, transform: nil))
    ctx.setStrokeColor(color(0xffffff, 0.10)); ctx.setLineWidth(4); ctx.strokePath()

    // Mark, from the board logo's 32-unit viewBox (y down) → scale 18, centred, flipped to y up.
    let k: CGFloat = 18
    func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: 512 + (x - 16) * k, y: 512 - (y - 16) * k) }
    ctx.setLineCap(.round); ctx.setLineJoin(.round)
    ctx.addEllipse(in: CGRect(x: 512 - 14 * k, y: 512 - 14 * k, width: 28 * k, height: 28 * k))
    ctx.setStrokeColor(color(0xf4f4f5)); ctx.setLineWidth(2 * k); ctx.strokePath()
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 36, color: color(0x4ade80, 0.55))
    ctx.move(to: p(10, 19)); ctx.addLine(to: p(14, 10)); ctx.addLine(to: p(18, 16)); ctx.addLine(to: p(20, 13)); ctx.addLine(to: p(23, 19))
    ctx.setStrokeColor(color(0x4ade80)); ctx.setLineWidth(2.6 * k); ctx.strokePath()
    ctx.restoreGState()

    return NSBitmapImageRep(cgImage: ctx.makeImage()!).representation(using: .png, properties: [:])!
}

let fm = FileManager.default
let iconset = fm.temporaryDirectory.appendingPathComponent("AppIcon-\(ProcessInfo.processInfo.processIdentifier).iconset")
try fm.createDirectory(at: iconset, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: iconset) }
for base in [16, 32, 128, 256, 512] {
    try render(base).write(to: iconset.appendingPathComponent("icon_\(base)x\(base).png"))
    try render(base * 2).write(to: iconset.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
if CommandLine.arguments.count > 1 { try render(1024).write(to: URL(fileURLWithPath: CommandLine.arguments[1])) }

try fm.createDirectory(at: icns.deletingLastPathComponent(), withIntermediateDirectories: true)
let tool = Process()
tool.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
tool.arguments = ["-c", "icns", iconset.path, "-o", icns.path]
try tool.run(); tool.waitUntilExit()
guard tool.terminationStatus == 0 else { fatalError("iconutil failed (\(tool.terminationStatus))") }
print("Wrote \(icns.path)")
