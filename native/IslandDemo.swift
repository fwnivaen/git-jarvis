import AppKit
import SwiftUI

enum IslandDemo {
    static let fixtureNotchHeight: CGFloat = 32
    static let fixtureNotchWidth: CGFloat = 180
    static let snapshotSettleDuration: TimeInterval = 0.05
    static let cases: [(IslandPhase, String, String)] = [
        (.ready, "Jarvis", ""), (.waking, "Preparing microphone", ""),
        (.listening, "What's the plan for today?", "Listening"),
        (.thinking, "Thinking", "What's the plan for today?"),
        (.working, "Working", "Checking the notes"),
        (.speaking, "Jarvis", "Good morning, Sir. Your schedule is clear."),
        (.mutedReply, "Jarvis", "Your schedule is clear."),
        (.modelDownload, "Downloading speech model", "42%"),
        (.done, "Done", "Your schedule is clear."),
        (.notification, "Jarvis", "Background task complete"),
        (.approval, "Review ↗", "A task needs your decision"),
        (.offline, "Jarvis needs attention", "Local server unavailable"),
        (.permissionDenied, "Jarvis needs attention", "Microphone permission is off"),
        (.microphoneFailure, "Jarvis needs attention", "No microphone input"),
        (.error, "Jarvis needs attention", "The request failed")
    ]

    static func snapshot(directory: URL) throws -> Int {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let model = IslandViewModel()
        model.evidenceRendering = true
        var written = 0
        for notch in [false, true] {
            model.hasNotch = notch
            model.notchHeight = notch ? fixtureNotchHeight : 0
            for (phase, title, detail) in cases {
                model.display = IslandDisplay(phase: phase, title: title, detail: detail,
                                              wordIndex: phase == .speaking ? 3 : nil,
                                              microphoneLevel: phase == .listening ? 0.7 : 0,
                                              listeningExpanded: phase == .listening)
                model.layerMeter.store(model.display.microphoneLevel)
                let size = IslandLayout.size(for: model.display, hasNotch: notch,
                    notchHeight: model.notchHeight, notchWidth: fixtureNotchWidth,
                    availableWidth: IslandDesign.expandedWidth)
                let frame = NSRect(origin: .zero, size: size).insetBy(dx: -IslandDesign.panelMargin,
                                                                    dy: -IslandDesign.panelMargin)
                let view = NSHostingView(rootView: IslandView(model: model).padding(IslandDesign.panelMargin))
                view.sizingOptions = []
                view.frame = NSRect(origin: .zero, size: frame.size)
                let window = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
                window.isReleasedWhenClosed = false
                window.sharingType = .none
                window.backgroundColor = .clear
                window.isOpaque = false
                window.contentView = view
                defer { window.close() }
                RunLoop.main.run(until: Date().addingTimeInterval(snapshotSettleDuration))
                view.layoutSubtreeIfNeeded()
                guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                view.cacheDisplay(in: view.bounds, to: bitmap)
                guard let png = bitmap.representation(using: .png, properties: [:]) else { continue }
                let name = "\(notch ? "notch" : "pill")-\(phase.rawValue).png"
                try png.write(to: directory.appendingPathComponent(name))
                written += 1
            }
        }
        return written
    }

    static func run(_ island: IslandController) {
        let selectedName = ProcessInfo.processInfo.environment["JARVIS_ISLAND_DEMO_STATE"]
        let selected = cases.first { $0.0.rawValue == selectedName }
        let sequence = selected.map { [$0] } ?? cases
        var index = 0
        func showNext() {
            let item = sequence[index % sequence.count]
            island.approval(nil)
            switch item.0 {
            case .ready:
                island.ready(true)
                island.end(.status, text: "")
            case .listening:
                island.begin(.listening, title: "Listening")
                island.transcript(item.1)
            case .waking, .thinking, .working, .speaking, .mutedReply, .modelDownload:
                island.begin(item.0, title: item.1, detail: item.2)
            case .done: island.end(.answer, text: item.2)
            case .notification: island.notice(item.2)
            case .approval: island.approval(item.2)
            default: island.show(item.0, title: item.1, detail: item.2)
            }
            index += 1
        }
        showNext()
        if selected != nil { return }
        Timer.scheduledTimer(withTimeInterval: 2.5, repeats: true) { _ in showNext() }
    }
}
