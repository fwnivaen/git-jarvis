import AppKit
import SwiftUI

final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

// One main-run-loop driver owns panel geometry. Its explicit frame updates also
// work in an unshown evidence window, where NSWindow.animator does not advance.
final class IslandFrameDriver {
    private weak var window: NSWindow?
    private let from: NSRect
    private let to: NSRect
    private let duration: TimeInterval
    private let expanding: Bool
    private var timer: Timer?
    private var started = Date()

    init(window: NSWindow, to: NSRect, expanding: Bool, reduceMotion: Bool = false) {
        self.window = window
        from = window.frame
        self.to = to
        self.expanding = expanding
        duration = reduceMotion ? 0 : IslandDesign.motionDuration(expanding ?
            IslandDesign.expansionDuration : IslandDesign.collapseDuration)
    }

    func start() {
        stop()
        guard duration > 0 else { window?.setFrame(to, display: true); return }
        started = Date()
        let timer = Timer(timeInterval: 1 / IslandDesign.geometryFramesPerSecond,
                          repeats: true) { [weak self] _ in self?.step() }
        self.timer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func stop() { timer?.invalidate(); timer = nil }
    deinit { timer?.invalidate() }

    private func step() {
        guard let window else { stop(); return }
        let elapsed = Date().timeIntervalSince(started)
        let progress = IslandDesign.geometryProgress(elapsed / duration, expanding: expanding)
        func mix(_ a: CGFloat, _ b: CGFloat) -> CGFloat { a + (b - a) * progress }
        window.setFrame(NSRect(x: mix(from.minX, to.minX), y: mix(from.minY, to.minY),
                               width: mix(from.width, to.width), height: mix(from.height, to.height)),
                        display: true)
        if elapsed >= duration { window.setFrame(to, display: true); stop() }
    }
}

// Announcements use fixed status language. Transcripts, answer bodies, error
// details and approval contents remain available in the accessibility tree,
// but are never copied into an unsolicited announcement.
struct IslandAnnouncements {
    struct Message {
        let text: String
        let priority: NSAccessibilityPriorityLevel
    }

    private struct Key: Equatable {
        let phase: IslandPhase
        let event: String
    }
    private var previous: Key?

    mutating func reset() { previous = nil }

    mutating func next(for display: IslandDisplay) -> Message? {
        guard display.phase != .hidden else { reset(); return nil }
        let event: String
        switch display.phase {
        case .notification, .approval, .releasedEarly, .offline, .permissionDenied,
             .microphoneFailure, .error:
            // Only compare this in memory to distinguish separate queued
            // events. It is never returned, logged, or posted to VoiceOver.
            event = display.title + "\u{0}" + display.detail
        default:
            // Streaming titles, download percentages and word ranges must not
            // generate repeated announcements within one phase.
            event = ""
        }
        let key = Key(phase: display.phase, event: event)
        guard key != previous else { return nil }
        previous = key
        let text: String
        let priority: NSAccessibilityPriorityLevel
        switch display.phase {
        case .hidden: return nil
        case .ready: text = "Jarvis is ready."; priority = .low
        case .waking: text = "Jarvis is preparing the microphone."; priority = .low
        case .listening: text = "Jarvis is listening."; priority = .low
        case .thinking: text = "Jarvis is thinking."; priority = .low
        case .working: text = "Jarvis is working."; priority = .low
        case .speaking: text = "Jarvis is speaking."; priority = .low
        case .mutedReply: text = "Jarvis reply is ready. Replies are muted."; priority = .low
        case .done: text = "Jarvis finished its reply."; priority = .low
        case .notification:
            if display.detail == "Replies muted" { text = "Jarvis replies muted." }
            else if display.detail == "Replies unmuted" { text = "Jarvis replies unmuted." }
            else { text = "Jarvis has an update." }
            priority = .low
        case .approval:
            text = "A Jarvis task needs your approval. Review in the dashboard."
            priority = .high
        case .releasedEarly:
            text = "Jarvis did not capture a request. Hold the push-to-talk keys while speaking."
            priority = .low
        case .offline: text = "The local Jarvis server is unavailable."; priority = .high
        case .permissionDenied: text = "Jarvis needs voice permission."; priority = .high
        case .microphoneFailure: text = "Jarvis could not use the microphone."; priority = .high
        case .error: text = "Jarvis needs attention."; priority = .high
        case .modelDownload:
            text = "Jarvis is preparing the on-device speech model."
            priority = .low
        }
        return Message(text: text, priority: priority)
    }
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
    private var localMouseMonitor: Any?
    private var sessionScreen: NSScreen?
    private var pendingHover: Bool?
    private var hoverWork: DispatchWorkItem?
    private var geometryWork: DispatchWorkItem?
    private var geometryTarget: NSRect?
    private var frameDriver: IslandFrameDriver?
    private let announcementsEnabled: Bool
    private var announcements = IslandAnnouncements()
    var enabled = true { didSet { render() } }

    init(announcementsEnabled: Bool = true) {
        self.announcementsEnabled = announcementsEnabled
        model.review = {
            guard let url = URL(string: "http://127.0.0.1:4700/#approvals") else { return }
            NSWorkspace.shared.open(url)
        }
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval, repeats: true) { [weak self] _ in
            self?.render()
        }
        mouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: .mouseMoved) { [weak self] event in
            self?.trackHover(event.locationInWindow)
        }
        localMouseMonitor = NSEvent.addLocalMonitorForEvents(matching: .mouseMoved) { [weak self] event in
            self?.trackHover(event.locationInWindow)
            return event
        }
    }

    deinit {
        timer?.invalidate()
        hoverWork?.cancel()
        geometryWork?.cancel()
        frameDriver?.stop()
        if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
        if let localMouseMonitor { NSEvent.removeMonitor(localMouseMonitor) }
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
        if let microphoneLevel { model.layerMeter.store(microphoneLevel) }
        // Audio callbacks feed the independent layer clock. They do not cause
        // a SwiftUI layout for every audio buffer; the existing controller
        // refresh supplies the orb's lower-frequency level updates.
        if title == nil, detail == nil, wordIndex == nil, microphoneLevel != nil { return }
        render()
    }

    func transcript(_ phrase: String) {
        state.transcript(phrase)
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
            hoverWork?.cancel()
            geometryWork?.cancel()
            frameDriver?.stop()
            geometryTarget = nil
            pendingHover = nil
            model.hover = false
            model.effectsActive = false
            announcements.reset()
            return
        }
        let notchHeight = max(0, screen.safeAreaInsets.top)
        let hasNotch = notchHeight >= IslandDesign.compactPillHeight
        let notchWidth = hasNotch ? max(0, (screen.auxiliaryTopRightArea?.minX ?? screen.frame.midX)
            - (screen.auxiliaryTopLeftArea?.maxX ?? screen.frame.midX)) : 0
        let size = IslandLayout.size(for: display, hasNotch: hasNotch, notchHeight: notchHeight,
                                     notchWidth: notchWidth,
                                     availableWidth: screen.frame.width - 2 * Self.maximumScreenInset)
        let width = size.width
        let height = size.height
        let shapeFrame = NSRect(x: screen.frame.midX - width / 2,
                                y: screen.frame.maxY - height, width: width, height: height)
        let frame = shapeFrame.insetBy(dx: -IslandDesign.panelMargin, dy: -IslandDesign.panelMargin)
        if panel == nil { makePanel(frame: frame) }
        guard let panel else { return }
        model.hasNotch = hasNotch
        model.notchHeight = hasNotch ? notchHeight : 0
        model.effectsActive = true
        model.layerMeter.store(display.microphoneLevel)
        let collapsing = model.display.isExpanded && !display.isExpanded
        if model.display != display { model.display = display }
        panel.ignoresMouseEvents = display.phase != .approval || !pointerInsideIsland()
        if geometryTarget != frame {
            geometryWork?.cancel()
            frameDriver?.stop()
            geometryTarget = frame
            let reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
            let work = DispatchWorkItem { [weak self, weak panel] in
                guard let self, let panel, self.geometryTarget == frame else { return }
                self.frameDriver = IslandFrameDriver(window: panel, to: frame,
                    expanding: display.isExpanded, reduceMotion: reduceMotion)
                self.frameDriver?.start()
            }
            geometryWork = work
            let delay = collapsing && !reduceMotion ?
                IslandDesign.motionDuration(IslandDesign.collapseContentFade) : 0
            if delay > 0 { DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work) }
            else { work.perform() }
        }
        if !panel.isVisible { panel.orderFrontRegardless() }
        if announcementsEnabled, let application = NSApp,
           let message = announcements.next(for: display) {
            // The SDK specifies posting announcementRequested on the
            // application element with announcement and priority user info.
            NSAccessibility.post(element: application, notification: .announcementRequested,
                                 userInfo: [.announcement: message.text,
                                            .priority: message.priority.rawValue])
        }
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
        let host = NSHostingView(rootView: IslandView(model: model).padding(IslandDesign.panelMargin))
        // Geometry is owned by IslandFrameDriver. Automatic window minimum-size
        // negotiation would jump directly to the expanded row's intrinsic height.
        host.sizingOptions = []
        panel.contentView = host
        panel.acceptsMouseMovedEvents = true
        self.panel = panel
    }

    private func trackHover(_ point: NSPoint) {
        guard let panel, panel.isVisible else { return }
        let inside = pointerInsideIsland()
        panel.ignoresMouseEvents = model.display.phase != .approval || !inside
        if inside == model.hover {
            hoverWork?.cancel()
            pendingHover = nil
            return
        }
        guard pendingHover != inside else { return }
        hoverWork?.cancel()
        pendingHover = inside
        let delay = IslandDesign.motionDuration(inside ? Self.hoverOpenDelay : Self.hoverCloseDelay)
        let work = DispatchWorkItem { [weak self] in
            guard let self, let panel = self.panel, panel.isVisible,
                  self.pendingHover == inside else { return }
            self.pendingHover = nil
            if self.pointerInsideIsland() == inside {
                self.model.hover = inside
            }
        }
        hoverWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
        _ = point
    }

    private func pointerInsideIsland() -> Bool {
        guard let panel else { return false }
        var frame = panel.frame.insetBy(dx: IslandDesign.panelMargin, dy: IslandDesign.panelMargin)
        if model.hover, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            let size = NSSize(width: frame.width * IslandDesign.hoverScale,
                              height: frame.height * IslandDesign.hoverScale)
            frame = NSRect(x: frame.midX - size.width / 2, y: frame.maxY - size.height,
                           width: size.width, height: size.height)
        }
        let point = NSEvent.mouseLocation
        let local = CGPoint(x: point.x - frame.minX, y: frame.maxY - point.y)
        let radius = model.display.isExpanded ? IslandDesign.expandedBottomRadius :
            model.display.isPeek ? IslandDesign.peekBottomRadius : IslandDesign.compactBottomRadius
        return IslandShape(bottomRadius: radius, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet)
            .path(in: CGRect(origin: .zero, size: frame.size)).contains(local)
    }
}
