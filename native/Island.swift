import AppKit
import SwiftUI

final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

// All UI methods are called on the app's main run loop.
final class IslandController: @unchecked Sendable {
    static let refreshInterval: TimeInterval = 0.1
    static let hoverOpenDelay: TimeInterval = 0.15
    static let hoverCloseDelay: TimeInterval = 0.30
    static let maximumScreenInset: CGFloat = 24

    private(set) var state = IslandState()
    private let model = IslandViewModel()
    private var panel: IslandPanel?
    private var timer: Timer?
    private var mouseMonitor: Any?
    private var sessionScreen: NSScreen?
    private var pendingHover: Bool?
    private var hoverChangeAt = Date.distantPast
    var enabled = true { didSet { render() } }

    init() {
        model.review = {
            guard let url = URL(string: "http://127.0.0.1:4700/") else { return }
            NSWorkspace.shared.open(url)
        }
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval, repeats: true) { [weak self] _ in
            self?.render()
        }
        mouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: .mouseMoved) { [weak self] event in
            self?.trackHover(event.locationInWindow)
        }
    }

    deinit {
        timer?.invalidate()
        if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
        panel?.close()
    }

    func begin(_ phase: IslandPhase, title: String, detail: String = "") {
        if sessionScreen == nil { sessionScreen = NSScreen.main ?? NSScreen.screens.first }
        state.begin(phase, title: title, detail: detail)
        render()
    }

    func update(title: String? = nil, detail: String? = nil,
                wordIndex: Int? = nil, microphoneLevel: Double? = nil) {
        state.update(title: title, detail: detail, wordIndex: wordIndex,
                     microphoneLevel: microphoneLevel)
        render()
    }

    func end(_ kind: IslandReplyKind, text: String) {
        state.end(kind, text: text)
        sessionScreen = nil
        render()
    }

    func show(_ phase: IslandPhase, title: String, detail: String = "") {
        state.show(phase, title: title, detail: detail)
        sessionScreen = nil
        render()
    }

    func approval(_ title: String?) {
        state.setApproval(title)
        render()
    }

    func backgroundWork(_ active: Bool) {
        state.setBackgroundWork(active)
        render()
    }

    func notice(_ title: String) {
        state.enqueueNotice(title)
        render()
    }

    func ready(_ enabled: Bool) {
        state.readyEnabled = enabled
        render()
    }

    func render() {
        let display = state.resolve()
        guard enabled, display.phase != .hidden,
              let screen = sessionScreen ?? NSScreen.main ?? NSScreen.screens.first else {
            panel?.orderOut(nil)
            return
        }
        let notchHeight = max(0, screen.safeAreaInsets.top)
        let hasNotch = notchHeight >= IslandDesign.compactPillHeight
        let width = display.isExpanded
            ? min(IslandDesign.expandedWidth, screen.frame.width - 2 * Self.maximumScreenInset)
            : min(IslandDesign.compactWidth, screen.frame.width - 2 * Self.maximumScreenInset)
        let height = display.isExpanded
            ? notchHeight + (display.detail.isEmpty ? IslandDesign.expandedOneRowExtra
                                                    : IslandDesign.expandedTwoRowExtra)
            : max(IslandDesign.compactPillHeight, notchHeight + IslandDesign.compactPillHeight)
        let frame = NSRect(x: screen.frame.midX - width / 2,
                           y: screen.frame.maxY - height, width: width, height: height)
        if panel == nil { makePanel(frame: frame) }
        guard let panel else { return }
        model.hasNotch = hasNotch
        model.notchHeight = hasNotch ? notchHeight : 0
        if model.display != display { model.display = display }
        panel.ignoresMouseEvents = display.phase != .approval
        if panel.frame != frame {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = display.isExpanded ? IslandDesign.expansionDuration
                                                     : IslandDesign.collapseDuration
                panel.animator().setFrame(frame, display: true)
            }
        }
        if !panel.isVisible { panel.orderFrontRegardless() }
    }

    private func makePanel(frame: NSRect) {
        let panel = IslandPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel],
                                backing: .buffered, defer: false)
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 3)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.sharingType = .none
        panel.hidesOnDeactivate = false
        panel.contentView = NSHostingView(rootView: IslandView(model: model))
        self.panel = panel
    }

    private func trackHover(_ point: NSPoint) {
        guard let panel, panel.isVisible else { return }
        let inside = panel.frame.contains(NSEvent.mouseLocation)
        if inside == model.hover { pendingHover = nil; return }
        if pendingHover != inside {
            pendingHover = inside
            hoverChangeAt = Date()
        }
        let delay = inside ? Self.hoverOpenDelay : Self.hoverCloseDelay
        if Date().timeIntervalSince(hoverChangeAt) >= delay {
            model.hover = inside
            pendingHover = nil
        }
        _ = point
    }
}
