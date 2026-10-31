import AppKit
import AVFoundation
import Foundation
import MetalKit
import OSLog
import Speech

enum VoicePermissions {
    struct Status {
        let microphone: String
        let speech: String
        let ready: Bool
        var detail: String { "Microphone: \(microphone) · Speech: \(speech)" }
    }

    static func current() async -> Status {
        let modernReady = await ModernCapture.canUseEnglish()
        let microphone = Self.microphone
        let legacySpeech = Self.speech
        return Status(microphone: microphone,
                      speech: modernReady ? "on-device model ready" : legacySpeech,
                      ready: microphone == "allowed" && (modernReady || legacySpeech == "allowed"))
    }

    static var microphone: String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "allowed"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not requested"
        @unknown default: return "unknown"
        }
    }

    static var speech: String {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: return "allowed"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not requested"
        @unknown default: return "unknown"
        }
    }
}

enum LaunchMode {
    case normal, ask(String), demo
}

@MainActor
final class JarvisAppDelegate: NSObject, NSApplicationDelegate {
    private let diagnostics = Logger(subsystem: "local.fwnivaen.jarvis.desktop", category: "voice")
    private let mode: LaunchMode
    private let island: IslandController
    private var conversation: Conversation!
    private var hotkeys: Hotkeys?
    private var statusItem: NSStatusItem?
    private var listenItem: NSMenuItem?
    private var muteItem: NSMenuItem?
    private var backgroundSpeechItem: NSMenuItem?
    private var backgroundPauseItem: NSMenuItem?
    private var handsFreeItem: NSMenuItem?
    private var handsFreeStatusItem: NSMenuItem?
    private var handsFree: HandsFreeController?
    private var pushToTalkKeyHeld = false
    private var voiceStartGeneration = 0
    private var manualCapturePending = false { didSet { refreshInputDeliveryGate() } }
    private var workspaceObservers: [NSObjectProtocol] = []
    private var islandItem: NSMenuItem?
    private var askStarted = false
    private var taskWatcher: TaskWatcher?
    private let speechModelInstaller = SpeechModelInstaller()
    private let deliveryPreferences = OperationsSpeechPreferences()

    init(mode: LaunchMode) {
        self.mode = mode
        if case .demo = mode { island = IslandController(announcementsEnabled: false) }
        else { island = IslandController() }
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        conversation = Conversation(island: island)
        island.enabled = UserDefaults.standard.object(forKey: "showJarvisIsland") as? Bool ?? true
        conversation.onPhase = { [weak self] phase in
            guard let self else { return }
            self.handsFree?.conversationChanged(phase)
            self.refreshEscapeBorrowing()
            if let listenItem = self.listenItem {
                let listening = phase == .preparing || phase == .capturing
                listenItem.title = listening ? "Finish listening" : phase == .idle ? "Start listening" : "Cancel current turn"
                listenItem.isEnabled = true
            }
            self.muteItem?.title = self.conversation.muted ? "Unmute replies" : "Mute replies"
            if case .ask = self.mode, self.askStarted, phase == .idle {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { NSApp.terminate(nil) }
            }
        }
        switch mode {
        case .normal:
            conversation.restoreBackgroundPause()
            installMenu()
            let handsFree = HandsFreeController(island: island,
                conversationIsIdle: { [weak self] in self?.conversation.phase == .idle },
                awaitConversationCleanup: { [weak self] in await self?.conversation.waitForCaptureCleanup() },
                submit: { [weak self] question in self?.conversation.ask(question) })
            handsFree.onStatus = { [weak self] enabled, status in
                self?.handsFreeItem?.title = enabled ? "Disable hands-free listening" : "Try hands-free (preview)…"
                self?.handsFreeItem?.state = enabled ? .on : .off
                self?.handsFreeStatusItem?.title = status
                self?.statusItem?.button?.title = enabled ? "✦ 🎙" : "✦"
                self?.statusItem?.button?.toolTip = enabled ? status : "Jarvis · Hold Control–Shift–Space to talk"
                self?.refreshInputDeliveryGate()
                self?.refreshEscapeBorrowing()
            }
            self.handsFree = handsFree
            observeDeliveryAvailability()
            let watcher = TaskWatcher()
            watcher.onUpdate = { [weak self] update in
                self?.conversation.backgroundUpdate(update.summary, id: update.id + ":" + update.status)
            }
            watcher.onActivity = { [weak self] active in
                self?.island.backgroundWork(active)
            }
            watcher.onApproval = { [weak self] title in self?.island.approval(title) }
            watcher.deliveryGeneration = { [weak self] in self?.deliveryPreferences.generation ?? 0 }
            watcher.onDeliveryPolicy = { [weak self] policy, generation in
                guard let self, self.deliveryPreferences.accept(policy, requestGeneration: generation) else { return }
                self.conversation.applyDeliveryPolicy(policy)
                self.conversation.setBackgroundPreferencePending(self.deliveryPreferences.awaitingDeliveryPolicy)
                self.backgroundSpeechItem?.state = self.conversation.backgroundSpeechEnabled ? .on : .off
            }
            watcher.start()
            taskWatcher = watcher
            let keys = Hotkeys()
            keys.pressed = { [weak self] id in
                guard let self else { return }
                self.diagnostics.notice("Hotkey pressed: \(id, privacy: .public)")
                switch id {
                case Hotkeys.pushToTalk:
                    self.pushToTalkKeyHeld = true
                    self.beginManualCapture(fromHotkey: true)
                case Hotkeys.cancel, Hotkeys.plainEscape:
                    self.cancelCurrentTurn()
                case Hotkeys.mute: self.toggleMute()
                default: break
                }
            }
            keys.released = { [weak self] id in
                self?.diagnostics.notice("Hotkey released: \(id, privacy: .public)")
                if id == Hotkeys.pushToTalk, let self {
                    self.pushToTalkKeyHeld = false
                    self.voiceStartGeneration += 1
                    self.manualCapturePending = false
                    self.conversation.releaseToTalk()
                    if self.conversation.phase == .idle { self.handsFree?.resumeAfterUnstartedPushToTalk() }
                }
            }
            hotkeys = keys
            diagnostics.notice("Push-to-talk registration status: \(keys.pushToTalkStatus, privacy: .public)")
            diagnostics.notice("Cancel registration status: \(keys.cancelStatus, privacy: .public)")
            diagnostics.notice("Mute registration status: \(keys.muteStatus, privacy: .public)")
            if !keys.isPushToTalkAvailable {
                island.show(.error, title: "Push-to-talk shortcut unavailable",
                            detail: "Check for another app using Control–Shift–Space")
            } else if !keys.isMuteAvailable {
                island.show(.error, title: "Mute shortcut unavailable",
                            detail: "Check for another app using Option–Command–M")
            } else {
                island.notice("Jarvis voice is ready")
            }
            diagnostics.notice("Speech authorization status at launch: \(SFSpeechRecognizer.authorizationStatus().rawValue, privacy: .public)")
        case .ask(let question):
            askStarted = true
            conversation.ask(question)
        case .demo:
            IslandDemo.run(island)
            if let value = ProcessInfo.processInfo.environment["JARVIS_ISLAND_DEMO_HOLD"],
               let seconds = TimeInterval(value), seconds > 0 {
                Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { _ in
                    NSApp.terminate(nil)
                }
            }
        }
    }

    private func installMenu() {
        let status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.title = "✦"
        status.button?.toolTip = "Jarvis · Hold Control–Shift–Space to talk"
        let menu = NSMenu()
        let listen = NSMenuItem(title: "Start listening", action: #selector(toggleListening), keyEquivalent: "")
        listen.target = self
        menu.addItem(listen)
        listenItem = listen
        let handsFree = NSMenuItem(title: "Try hands-free (preview)…",
                                  action: #selector(toggleHandsFree), keyEquivalent: "")
        handsFree.target = self
        menu.addItem(handsFree)
        handsFreeItem = handsFree
        let handsFreeStatus = NSMenuItem(title: "Hands-free off", action: nil, keyEquivalent: "")
        handsFreeStatus.isEnabled = false
        menu.addItem(handsFreeStatus)
        handsFreeStatusItem = handsFreeStatus
        let ask = NSMenuItem(title: "Ask Jarvis…", action: #selector(askFromMenu), keyEquivalent: "")
        ask.target = self
        menu.addItem(ask)
        let mute = NSMenuItem(title: "Mute replies", action: #selector(toggleMute), keyEquivalent: "")
        mute.target = self
        menu.addItem(mute)
        muteItem = mute
        let backgroundSpeech = NSMenuItem(title: "Speak background updates",
                                          action: #selector(toggleBackgroundSpeech), keyEquivalent: "")
        backgroundSpeech.target = self
        backgroundSpeech.state = conversation.backgroundSpeechEnabled ? .on : .off
        menu.addItem(backgroundSpeech)
        backgroundSpeechItem = backgroundSpeech
        let pause = NSMenuItem(title: "Pause background updates",
                               action: #selector(toggleBackgroundPause), keyEquivalent: "")
        pause.target = self
        pause.state = conversation.backgroundPaused ? .on : .off
        menu.addItem(pause)
        backgroundPauseItem = pause
        let show = NSMenuItem(title: "Show Jarvis Island", action: #selector(toggleIsland), keyEquivalent: "")
        show.target = self
        show.state = island.enabled ? .on : .off
        menu.addItem(show)
        islandItem = show
        let enhanced = NSMenuItem(title: "Install enhanced on-device speech…",
                                  action: #selector(installSpeechModel), keyEquivalent: "")
        enhanced.target = self
        menu.addItem(enhanced)
        let permissions = NSMenuItem(title: "Check voice permissions",
                                     action: #selector(checkPermissions), keyEquivalent: "")
        permissions.target = self
        menu.addItem(permissions)
        menu.addItem(.separator())
        let dashboard = NSMenuItem(title: "Open dashboard", action: #selector(openDashboard), keyEquivalent: "")
        dashboard.target = self
        menu.addItem(dashboard)
        let quit = NSMenuItem(title: "Quit Jarvis Voice", action: #selector(quit), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
        status.menu = menu
        statusItem = status
    }

    @objc private func askFromMenu() {
        handsFree?.disable(reason: "Hands-free off for typed request")
        let alert = NSAlert()
        alert.messageText = "Ask Jarvis"
        alert.informativeText = "Your question is sent only to the local Jarvis server."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        alert.accessoryView = field
        alert.addButton(withTitle: "Ask")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { conversation.ask(field.stringValue) }
    }

    @objc private func toggleListening() {
        switch conversation.phase {
        case .idle:
            if manualCapturePending {
                cancelCurrentTurn()
            } else { beginManualCapture(fromHotkey: false) }
        case .preparing, .capturing:
            conversation.releaseToTalk()
        case .finishing, .thinking, .renderingSpeech, .speaking, .mutedReply:
            cancelCurrentTurn()
        }
    }

    private func refreshEscapeBorrowing() {
        let active = conversation.phase != .idle && conversation.phase != .preparing
        hotkeys?.borrowPlainEscape(active || handsFree?.hasActiveTurn == true)
    }

    private func refreshInputDeliveryGate() {
        conversation?.setForegroundInputActive(manualCapturePending || handsFree?.hasActiveTurn == true)
    }

    private func cancelCurrentTurn() {
        voiceStartGeneration += 1
        manualCapturePending = false
        handsFree?.disable()
        conversation.cancel()
    }

    private func beginManualCapture(fromHotkey: Bool) {
        guard conversation.phase == .idle, !manualCapturePending else { return }
        guard let handsFree, handsFree.enabled || handsFree.needsCaptureHandoff else {
            ReadySound.play()
            conversation.pressToTalk()
            return
        }
        voiceStartGeneration += 1
        let current = voiceStartGeneration
        manualCapturePending = true
        handsFree.yieldForPushToTalk { [weak self] in
            guard let self else { return }
            // An obsolete completion must not resume wake capture underneath
            // a newer PTT handoff. Key release already handles its own resume.
            guard self.voiceStartGeneration == current else { return }
            guard (!fromHotkey || self.pushToTalkKeyHeld), self.conversation.phase == .idle else {
                self.manualCapturePending = false
                if self.conversation.phase == .idle { self.handsFree?.resumeAfterUnstartedPushToTalk() }
                return
            }
            self.manualCapturePending = false
            ReadySound.play()
            self.conversation.pressToTalk()
        }
    }

    @objc private func toggleHandsFree() {
        guard let handsFree else { return }
        if handsFree.enabled { handsFree.disable(); return }
        let consent = HandsFreeConsent(generation: voiceStartGeneration)
        guard consent.allowsActivation(currentGeneration: voiceStartGeneration,
            conversationIdle: conversation.phase == .idle, muted: conversation.muted,
            manualCapturePending: manualCapturePending) else {
            island.notice("Finish the current turn and unmute replies before enabling hands-free")
            return
        }
        let alert = NSAlert()
        alert.messageText = "Try hands-free listening for this session?"
        alert.informativeText = "Jarvis will continuously transcribe your microphone on this Mac. Say “Hey Jarvis” and your question; only that question is sent to your local Jarvis server for a reply or action. After a reply, Jarvis listens for a follow-up for 6 seconds. The 🎙 menu-bar indicator stays visible. Cancel, mute, switching users, display or system sleep, or quitting turns hands-free off. Turn it off before locking your Mac; immediate screen-lock detection is not verified. It stays off after relaunch."
        alert.addButton(withTitle: "Enable for this session")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        guard consent.allowsActivation(currentGeneration: voiceStartGeneration,
            conversationIdle: conversation.phase == .idle, muted: conversation.muted,
            manualCapturePending: manualCapturePending) else {
            island.notice("Hands-free stayed off because the voice controls changed. Try again when ready.")
            return
        }
        handsFree.enable()
    }

    @objc private func toggleMute() {
        handsFree?.disable()
        conversation.toggleMute()
        muteItem?.title = conversation.muted ? "Unmute replies" : "Mute replies"
        diagnostics.notice("Replies muted: \(self.conversation.muted, privacy: .public)")
        island.notice(conversation.muted ? "Replies muted" : "Replies unmuted")
    }
    @objc private func toggleBackgroundSpeech() {
        guard !deliveryPreferences.updating else { return }
        let enabled = !conversation.backgroundSpeechEnabled
        backgroundSpeechItem?.isEnabled = false
        conversation.setBackgroundPreferencePending(true)
        deliveryPreferences.setSpeech(enabled) { [weak self] result in
            guard let self else { return }
            switch result {
            case .saved(let snapshot):
                self.conversation.setBackgroundSpeech(snapshot.settings.speakBackgroundUpdates)
            case .legacy:
                self.conversation.setBackgroundSpeech(enabled)
            case .conflict(let current):
                if let current, current.managed {
                    self.conversation.setBackgroundSpeech(current.settings.speakBackgroundUpdates)
                }
                self.island.notice("Background speech settings changed elsewhere. Review the current choice and try again.")
            case .failed:
                self.island.notice("Background speech settings could not be saved. Your previous choice is unchanged.")
            }
            self.conversation.setBackgroundPreferencePending(self.deliveryPreferences.awaitingDeliveryPolicy)
            self.backgroundSpeechItem?.isEnabled = true
            self.backgroundSpeechItem?.state = self.conversation.backgroundSpeechEnabled ? .on : .off
            self.taskWatcher?.poll()
        }
    }
    @objc private func toggleBackgroundPause() {
        conversation.toggleBackgroundPause()
        backgroundPauseItem?.state = conversation.backgroundPaused ? .on : .off
    }
    private func observeDeliveryAvailability() {
        let center = NSWorkspace.shared.notificationCenter
        let signals: [(Notification.Name, Bool?, Bool?)] = [
            (NSWorkspace.sessionDidResignActiveNotification, false, nil),
            (NSWorkspace.sessionDidBecomeActiveNotification, true, nil),
            (NSWorkspace.screensDidSleepNotification, nil, false),
            (NSWorkspace.screensDidWakeNotification, nil, true),
            (NSWorkspace.willSleepNotification, nil, false),
            (NSWorkspace.didWakeNotification, nil, true),
        ]
        workspaceObservers = signals.map { name, sessionActive, displayAwake in
            center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    if sessionActive == false || displayAwake == false {
                        self?.voiceStartGeneration += 1
                        self?.manualCapturePending = false
                        self?.handsFree?.disable(reason: "Hands-free off while the session is unavailable")
                        self?.conversation.cancel()
                    }
                    self?.conversation.setBackgroundAvailability(sessionActive: sessionActive, displayAwake: displayAwake)
                }
            }
        }
    }
    @objc private func toggleIsland() {
        island.enabled.toggle()
        islandItem?.state = island.enabled ? .on : .off
        UserDefaults.standard.set(island.enabled, forKey: "showJarvisIsland")
    }
    @objc private func installSpeechModel() { speechModelInstaller.install(island: island) }
    @objc private func checkPermissions() {
        Task { @MainActor [weak self] in
            let status = await VoicePermissions.current()
            guard let self else { return }
            if status.ready || self.island.state.activePhase != nil {
                self.island.notice(status.detail)
            } else {
                self.island.show(.permissionDenied, title: "Voice permissions", detail: status.detail)
            }
        }
    }
    @objc private func openDashboard() {
        if let url = URL(string: "http://127.0.0.1:4700/") { NSWorkspace.shared.open(url) }
    }
    @objc private func quit() {
        taskWatcher?.stop()
        for observer in workspaceObservers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        workspaceObservers.removeAll()
        handsFree?.disable()
        conversation.cancel()
        NSApp.terminate(nil)
    }
}

@main
enum JarvisDesktop {
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first == "--write-ready-sound", arguments.count == 2 {
            do {
                try ReadySound.data().write(to: URL(fileURLWithPath: arguments[1]))
                print("Ready sound written")
            } catch { fputs("Could not write ready sound\n", stderr); exit(1) }
            return
        }
        if arguments.first == "--check-voice-lock" {
            guard let lock = VoiceLock() else {
                print("Microphone lock busy")
                exit(1)
            }
            withExtendedLifetime(lock) { print("Microphone lock available") }
            return
        }
        if arguments.first == "--check-permissions" {
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            Task { @MainActor in
                let status = await VoicePermissions.current()
                print(status.detail)
                app.terminate(nil)
            }
            app.run()
            return
        }
        if arguments.first == "--check-speech-capability" {
            let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
            print("English recognizer available: \(recognizer?.isAvailable == true); on-device recognition: \(recognizer?.supportsOnDeviceRecognition == true)")
            return
        }
        if arguments.first == "--install-speech-model" {
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            Task { @MainActor in
                do {
                    guard SpeechTranscriber.isAvailable else {
                        fputs("Enhanced English speech is unavailable on this Mac\n", stderr)
                        exit(1)
                    }
                    let module = SpeechTranscriber(locale: Locale(identifier: "en-US"),
                                                   preset: .progressiveTranscription)
                    let status = await AssetInventory.status(forModules: [module])
                    print("Enhanced English model status: \(status)")
                    if status == .installed {
                        print("Enhanced English speech model already installed")
                        app.terminate(nil)
                        return
                    }
                    guard status != .unsupported else {
                        fputs("Enhanced English speech model unavailable\n", stderr)
                        exit(1)
                    }
                    guard let request = try await AssetInventory.assetInstallationRequest(
                        supporting: [module]) else {
                        let refreshed = await AssetInventory.status(forModules: [module])
                        guard refreshed == .installed else {
                            fputs("Enhanced English speech model is not installed yet\n", stderr)
                            exit(1)
                        }
                        print("Enhanced English speech model already installed")
                        app.terminate(nil)
                        return
                    }
                    print("Downloading Apple on-device English speech model")
                    fflush(stdout)
                    try await request.downloadAndInstall()
                    guard await AssetInventory.status(forModules: [module]) == .installed else {
                        fputs("Enhanced English speech model download is still pending\n", stderr)
                        exit(1)
                    }
                    print("Enhanced English speech model installed")
                    app.terminate(nil)
                } catch {
                    fputs("Enhanced English speech model installation failed\n", stderr)
                    exit(1)
                }
            }
            app.run()
            return
        }
        if arguments.first == "--check-speech-fixture", arguments.count == 2 {
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            Task { @MainActor in
                guard await TranscriberSmoke.checkFile(arguments[1]) else {
                    fputs("On-device file transcription failed\n", stderr)
                    exit(1)
                }
                print("On-device file transcription passed")
                app.terminate(nil)
            }
            app.run()
            return
        }
        if arguments.first == "--measure-mic" {
            exit(MicrophoneCheck.run())
        }
        if arguments.first == "--check-hotkeys" {
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            let keys = Hotkeys()
            print("Push-to-talk registered: \(keys.isPushToTalkAvailable); status: \(keys.pushToTalkStatus)")
            print("Cancel registration status: \(keys.cancelStatus)")
            print("Mute registered: \(keys.isMuteAvailable); status: \(keys.muteStatus)")
            keys.pressed = { id in print("Hotkey pressed: \(id)"); fflush(stdout) }
            keys.released = { id in print("Hotkey released: \(id)"); fflush(stdout) }
            fflush(stdout)
            if arguments.dropFirst().first == "--self-event" {
                print("Handler self-event status: \(keys.checkEventHandler())")
                fflush(stdout)
            }
            Timer.scheduledTimer(withTimeInterval: 10, repeats: false) { _ in app.terminate(nil) }
            app.run()
            withExtendedLifetime(keys) {}
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        if arguments.first == "--render-island-layers", arguments.count == 2 {
            do {
                let count = try IslandLayerEvidence.render(directory: URL(fileURLWithPath: arguments[1]))
                print("Island CALayer frames: \(count); deterministic layer evidence, no live microphone or animation proof")
            } catch { fputs("Could not render Island layer evidence\n", stderr); exit(1) }
            return
        }
        if arguments.first == "--render-island-transitions", arguments.count == 2 {
            do {
                let result = try LiveTextEvidence.renderTransitions(directory: URL(fileURLWithPath: arguments[1]))
                print("Offscreen Island frames: \(result.frames); expansion intermediate: \(result.expandingIntermediateFrames); collapse intermediate: \(result.collapsingIntermediateFrames). Text/Canvas only; no Metal or audio proof")
            } catch { fputs("Could not render Island transition evidence\n", stderr); exit(1) }
            return
        }
        if arguments.first == "--render-live-text", arguments.count == 2 {
            do {
                try LiveTextEvidence.render(to: URL(fileURLWithPath: arguments[1]))
                print("Text view rendering written; static frame, no animation or Metal capture")
            } catch { fputs("Could not render text view evidence\n", stderr); exit(1) }
            return
        }
        if arguments.first == "--snapshot-island", arguments.count == 2 {
            do {
                let count = try IslandDemo.snapshot(directory: URL(fileURLWithPath: arguments[1]))
                print("Island snapshots: \(count)")
            } catch { fputs("Could not render Island snapshots\n", stderr); exit(1) }
            return
        }
        if arguments.first == "--check-island" {
            var state = IslandState()
            state.enqueueNotice("Task complete")
            state.setApproval("Review")
            state.begin(.listening, title: "Listening")
            precondition(state.resolve().phase == .listening)
            state.end(.status, text: "Status")
            precondition(state.resolve().phase == .approval)
            state.setApproval(nil)
            precondition(state.resolve().phase == .notification)
            let metalView = MTKView(frame: NSRect(x: 0, y: 0, width: 44, height: 44),
                                    device: MTLCreateSystemDefaultDevice())
            metalView.colorPixelFormat = .bgra8Unorm
            precondition(OrbRenderer(view: metalView) != nil, "Metal orb shader unavailable")
            print("Island checks passed")
            return
        }
        if arguments.first == "say" || arguments.first == "--say" {
            let text = arguments.dropFirst().joined(separator: " ")
            guard !text.isEmpty else { exit(2) }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/say")
            process.arguments = ["-v", "Daniel", text]
            do { try process.run(); process.waitUntilExit(); exit(process.terminationStatus) }
            catch { exit(1) }
        }
        let mode: LaunchMode
        if arguments.first == "--ask" {
            let question = arguments.dropFirst().joined(separator: " ")
            guard !question.isEmpty else { exit(2) }
            mode = .ask(question)
        } else if arguments.first == "--demo-island" {
            mode = .demo
        } else if arguments.isEmpty {
            mode = .normal
        } else {
            fputs("Unknown option\n", stderr)
            exit(2)
        }
        let delegate = JarvisAppDelegate(mode: mode)
        app.delegate = delegate
        app.run()
    }
}
