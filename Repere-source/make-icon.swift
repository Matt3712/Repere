#!/usr/bin/env swift
import AppKit
import Foundation

// Rebuild the app icon with: swift make-icon.swift /path/to/Repere.icns
// The icon is drawn locally; no downloaded assets or fonts are needed.
guard CommandLine.arguments.count == 2 else {
    fputs("Usage: swift make-icon.swift /path/to/Repere.icns\n", stderr)
    exit(1)
}

let output = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let temporary = FileManager.default.temporaryDirectory
    .appendingPathComponent("repere-icon-\(UUID().uuidString)", isDirectory: true)
let iconset = temporary.appendingPathComponent("Repere.iconset", isDirectory: true)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: temporary) }

func drawIcon(pixels: Int) throws -> Data {
    let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
        isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    let graphics = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = graphics
    defer { NSGraphicsContext.restoreGraphicsState() }
    graphics.shouldAntialias = true
    graphics.imageInterpolation = .high
    let transform = NSAffineTransform()
    transform.scale(by: CGFloat(pixels) / 1024)
    transform.concat()

    // macOS icons sit within a transparent margin around the rounded tile.
    let tile = NSBezierPath(roundedRect: NSRect(x: 88, y: 88, width: 848, height: 848),
                            xRadius: 190, yRadius: 190)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.22)
    shadow.shadowOffset = NSSize(width: 0, height: -12)
    shadow.shadowBlurRadius = 22
    shadow.set()
    NSColor(calibratedRed: 0.25, green: 0.29, blue: 0.85, alpha: 1).setFill()
    tile.fill()
    NSGraphicsContext.restoreGraphicsState()

    NSGradient(colors: [
        NSColor(calibratedRed: 0.29, green: 0.41, blue: 0.98, alpha: 1),
        NSColor(calibratedRed: 0.25, green: 0.25, blue: 0.76, alpha: 1)
    ])!.draw(in: tile, angle: -65)

    NSGraphicsContext.saveGraphicsState()
    tile.addClip()
    // Quiet map contours keep the marker recognizable at small sizes.
    NSColor.white.withAlphaComponent(0.07).setStroke()
    for offset in stride(from: -220, through: 620, by: 210) {
        let road = NSBezierPath()
        road.move(to: NSPoint(x: 70, y: CGFloat(offset)))
        road.curve(to: NSPoint(x: 950, y: CGFloat(offset + 470)),
                   controlPoint1: NSPoint(x: 360, y: CGFloat(offset + 290)),
                   controlPoint2: NSPoint(x: 710, y: CGFloat(offset + 90)))
        road.lineWidth = 14
        road.stroke()
    }
    NSGraphicsContext.restoreGraphicsState()

    let halo = NSBezierPath(ovalIn: NSRect(x: 375, y: 223, width: 274, height: 73))
    NSColor.white.withAlphaComponent(0.17).setFill()
    halo.fill()

    let marker = NSBezierPath()
    marker.move(to: NSPoint(x: 512, y: 269))
    marker.curve(to: NSPoint(x: 337, y: 550),
                 controlPoint1: NSPoint(x: 476, y: 317),
                 controlPoint2: NSPoint(x: 337, y: 435))
    marker.curve(to: NSPoint(x: 512, y: 731),
                 controlPoint1: NSPoint(x: 337, y: 650),
                 controlPoint2: NSPoint(x: 414, y: 731))
    marker.curve(to: NSPoint(x: 687, y: 550),
                 controlPoint1: NSPoint(x: 610, y: 731),
                 controlPoint2: NSPoint(x: 687, y: 650))
    marker.curve(to: NSPoint(x: 512, y: 269),
                 controlPoint1: NSPoint(x: 687, y: 435),
                 controlPoint2: NSPoint(x: 548, y: 317))
    marker.close()
    marker.appendOval(in: NSRect(x: 448, y: 489, width: 128, height: 128))
    marker.windingRule = .evenOdd
    NSColor.white.setFill()
    marker.fill()

    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "RepereIcon", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Could not encode icon PNG."])
    }
    return data
}

for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let filename = "icon_\(points)x\(points)\(scale == 2 ? "@2x" : "").png"
        try drawIcon(pixels: points * scale).write(to: iconset.appendingPathComponent(filename))
    }
}

try FileManager.default.createDirectory(at: output.deletingLastPathComponent(),
                                        withIntermediateDirectories: true)
let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
process.arguments = ["-c", "icns", "-o", output.path, iconset.path]
try process.run()
process.waitUntilExit()
guard process.terminationStatus == 0 else { exit(process.terminationStatus) }
print(output.path)
