import AppKit
import QuartzCore
import SwiftUI

// This evidence mode uses the real IslandText SwiftUI component inside an
// AppKit hosting view. It caches one fully laid-out text frame, without an
// IslandController, hotkeys, microphone, network requests, or a visible window.
// It proves text drawing only: it does not capture live animation, Metal, audio
// synchronization, Core Animation effects, screen-sharing exclusion, or focus.
enum LiveTextEvidence {
    static let width: CGFloat = 466
    static let height: CGFloat = 220
    static let padding: CGFloat = 24
    static let rowSpacing: CGFloat = 16
    static let layoutSettleSeconds: TimeInterval = 0.05
    static let title = "Jarvis text rendering check"
    static let unicodeText = "Café, résumé, and 👩🏽‍💻 — ready, Sir."
    static let transcript = "This deliberately long transcript keeps the newest recognized words visible at the end of the row."
    static let answer = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty."
    static let activeWord = 12

    private struct Specimen: View {
        var body: some View {
            VStack(alignment: .leading, spacing: LiveTextEvidence.rowSpacing) {
                IslandText(text: LiveTextEvidence.title, secondary: false)
                IslandText(text: LiveTextEvidence.unicodeText, secondary: true)
                IslandText(text: LiveTextEvidence.transcript, secondary: false)
                IslandText(text: SpokenWords.centeredText(LiveTextEvidence.answer,
                                                          activeWord: LiveTextEvidence.activeWord),
                           secondary: true)
            }
            .padding(LiveTextEvidence.padding)
            .frame(width: LiveTextEvidence.width, height: LiveTextEvidence.height,
                   alignment: .topLeading)
            .background(Color.black)
        }
    }

    enum Failure: Error { case bitmapUnavailable, pngUnavailable, missingIntermediateFrames }

    private static func capture(_ view: NSView, to destination: URL) throws {
        view.layoutSubtreeIfNeeded()
        let size = view.bounds.size
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
                                             pixelsWide: Int(ceil(size.width)), pixelsHigh: Int(ceil(size.height)),
                                             bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                             isPlanar: false, colorSpaceName: .deviceRGB,
                                             bytesPerRow: 0, bitsPerPixel: 0) else {
            throw Failure.bitmapUnavailable
        }
        bitmap.size = size
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            throw Failure.pngUnavailable
        }
        try png.write(to: destination, options: .atomic)
    }

    static func render(to destination: URL) throws {
        let frame = NSRect(x: 0, y: 0, width: width, height: height)
        let view = NSHostingView(rootView: Specimen())
        view.frame = frame
        // Attaching to an unshown window gives NSHostingView an AppKit layout
        // environment. The window is never ordered front or made key.
        let window = NSWindow(contentRect: frame, styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.sharingType = .none
        window.contentView = view
        defer { window.close() }
        view.layoutSubtreeIfNeeded()
        CATransaction.flush()
        RunLoop.main.run(until: Date().addingTimeInterval(layoutSettleSeconds))
        view.layoutSubtreeIfNeeded()
        // Fix the pixel dimensions instead of inheriting a display's backing
        // scale, so the fixture has the same dimensions on Retina and 1x screens.
        try capture(view, to: destination)
    }

    struct TransitionResult {
        let frames: Int
        let expandingIntermediateFrames: Int
        let collapsingIntermediateFrames: Int
    }

    // Capture real offscreen geometry updates from the same driver used by the
    // Island panel. Frames use the real SwiftUI text and Canvas fallback, with
    // synthetic contents. This is bounded geometry/render evidence, not proof
    // of Metal, live microphone/audio sync, Spaces, focus or sharing exclusion.
    static func renderTransitions(directory: URL) throws -> TransitionResult {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let model = IslandViewModel()
        model.evidenceRendering = true
        model.display = IslandDisplay(phase: .listening, title: "Listening", detail: "",
                                      wordIndex: nil, microphoneLevel: 0.6)
        let margin = IslandDesign.panelMargin
        func frame(width: CGFloat, height: CGFloat) -> NSRect {
            NSRect(x: 600 - width / 2 - margin, y: 800 - height - margin,
                   width: width + 2 * margin, height: height + 2 * margin)
        }
        let compact = frame(width: IslandDesign.compactWidth, height: IslandDesign.compactPillHeight)
        let expanded = frame(width: IslandDesign.expandedWidth, height: IslandDesign.expandedTwoRowExtra)
        let collapsed = frame(width: 280, height: IslandDesign.peekContentHeight)
        let view = NSHostingView(rootView: IslandView(model: model).padding(margin))
        view.sizingOptions = []
        view.frame = NSRect(origin: .zero, size: compact.size)
        let window = NSWindow(contentRect: compact, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.sharingType = .none
        window.backgroundColor = .clear
        window.isOpaque = false
        window.hasShadow = false
        window.contentView = view
        defer { window.close() }
        RunLoop.main.run(until: Date().addingTimeInterval(layoutSettleSeconds))

        struct Frame: Codable {
            let file: String
            let phase: String
            let elapsed: Double
            let width: Double
            let height: Double
        }
        var frames: [Frame] = []
        let started = Date()
        func record(_ phase: String) throws {
            CATransaction.flush()
            let name = String(format: "%03d-%@.png", frames.count, phase)
            try capture(view, to: directory.appendingPathComponent(name))
            frames.append(Frame(file: name, phase: phase, elapsed: Date().timeIntervalSince(started),
                                width: window.frame.width, height: window.frame.height))
        }
        try record("compact")
        func transition(to target: NSRect, expanding: Bool, phase: String) throws -> Int {
            let origin = window.frame
            let driver = IslandFrameDriver(window: window, to: target, expanding: expanding)
            driver.start()
            defer { driver.stop() }
            let duration = IslandDesign.motionDuration(expanding ? IslandDesign.expansionDuration : IslandDesign.collapseDuration)
            let finish = Date().addingTimeInterval(duration + 0.05)
            var intermediate = 0
            while Date() < finish {
                RunLoop.main.run(until: Date().addingTimeInterval(1 / 30))
                if abs(window.frame.height - origin.height) > 0.5,
                   abs(window.frame.height - target.height) > 0.5 { intermediate += 1 }
                try record(phase)
            }
            return intermediate
        }
        model.display = IslandDisplay(phase: .thinking, title: "Thinking", detail: "Preparing a synthetic reply",
                                      wordIndex: nil, microphoneLevel: 0)
        let expansionFrames = try transition(to: expanded, expanding: true, phase: "expanding")
        model.display = IslandDisplay(phase: .done, title: "Done", detail: "Synthetic reply complete",
                                      wordIndex: nil, microphoneLevel: 0)
        RunLoop.main.run(until: Date().addingTimeInterval(IslandDesign.motionDuration(IslandDesign.collapseContentFade)))
        let collapseFrames = try transition(to: collapsed, expanding: false, phase: "collapsing")
        try record("collapsed")
        guard expansionFrames > 0, collapseFrames > 0 else { throw Failure.missingIntermediateFrames }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(frames).write(to: directory.appendingPathComponent("frames.json"), options: .atomic)
        try Data("Actual offscreen IslandFrameDriver geometry and SwiftUI text/Canvas frames. Synthetic contents. No microphone, hotkeys, network, visible window, Metal, audio synchronization, Spaces, focus or sharing-exclusion proof.\n".utf8)
            .write(to: directory.appendingPathComponent("LIMITATIONS.txt"), options: .atomic)
        return TransitionResult(frames: frames.count, expandingIntermediateFrames: expansionFrames,
                                collapsingIntermediateFrames: collapseFrames)
    }
}
