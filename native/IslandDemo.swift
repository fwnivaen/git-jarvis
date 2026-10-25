import AppKit
import SwiftUI

enum IslandDemo {
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
        var written = 0
        for notch in [false, true] {
            model.hasNotch = notch
            model.notchHeight = notch ? 32 : 0
            for (phase, title, detail) in cases {
                let width = phase == .ready || phase == .waking || phase == .done || phase == .notification
                    ? IslandDesign.compactWidth : IslandDesign.expandedWidth
                let height: CGFloat = width == IslandDesign.compactWidth ? (notch ? 62 : 30) : (notch ? 108 : 76)
                model.display = IslandDisplay(phase: phase, title: title, detail: detail,
                                              wordIndex: phase == .speaking ? 3 : nil,
                                              microphoneLevel: phase == .listening ? 0.7 : 0)
                let view = NSHostingView(rootView: IslandView(model: model))
                view.frame = NSRect(x: 0, y: 0, width: width, height: height)
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
        var index = 0
        func showNext() {
            let item = cases[index % cases.count]
            island.approval(nil)
            switch item.0 {
            case .ready:
                island.ready(true)
                island.end(.status, text: "")
            case .waking, .listening, .thinking, .working, .speaking, .mutedReply, .modelDownload:
                island.begin(item.0, title: item.1, detail: item.2)
            case .done: island.end(.answer, text: item.2)
            case .notification: island.notice(item.2)
            case .approval: island.approval(item.2)
            default: island.show(item.0, title: item.1, detail: item.2)
            }
            index += 1
        }
        showNext()
        Timer.scheduledTimer(withTimeInterval: 2.5, repeats: true) { _ in showNext() }
    }
}
