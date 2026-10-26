import AppKit
import AVFoundation
import Foundation
import MetalKit
import Speech

enum VoicePermissions {
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

final class JarvisAppDelegate: NSObject, NSApplicationDelegate {
    private let mode: LaunchMode
    private let island = IslandController()
    private var conversation: Conversation!
    private var hotkeys: Hotkeys?
    private var statusItem: NSStatusItem?
    private var listenItem: NSMenuItem?
    private var muteItem: NSMenuItem?
    private var backgroundSpeechItem: NSMenuItem?
    private var islandItem: NSMenuItem?
    private var askStarted = false
    private var taskWatcher: TaskWatcher?
    private let speechModelInstaller = SpeechModelInstaller()

    init(mode: LaunchMode) { self.mode = mode }

    func applicationDidFinishLaunching(_ notification: Notification) {
        conversation = Conversation(island: island)
        island.enabled = UserDefaults.standard.object(forKey: "showJarvisIsland") as? Bool ?? true
        conversation.onPhase = { [weak self] phase in
            guard let self else { return }
            let active = phase != .idle && phase != .preparing
            self.hotkeys?.borrowPlainEscape(active)
            if let listenItem = self.listenItem {
                let listening = phase == .preparing || phase == .capturing
                listenItem.title = listening ? "Finish listening" : "Start listening"
                listenItem.isEnabled = listening || phase == .idle
            }
            self.muteItem?.title = self.conversation.muted ? "Unmute replies" : "Mute replies"
            if case .ask = self.mode, self.askStarted, phase == .idle {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { NSApp.terminate(nil) }
            }
        }
        switch mode {
        case .normal:
            installMenu()
            let watcher = TaskWatcher()
            watcher.onUpdate = { [weak self] update in
                self?.conversation.backgroundUpdate(update.summary)
            }
            watcher.onActivity = { [weak self] active in
                self?.island.backgroundWork(active)
            }
            watcher.start()
            taskWatcher = watcher
            let keys = Hotkeys()
            keys.pressed = { [weak self] id in
                guard let self else { return }
                switch id {
                case Hotkeys.pushToTalk:
                    ReadySound.play()
                    self.conversation.pressToTalk()
                case Hotkeys.cancel, Hotkeys.plainEscape: self.conversation.cancel()
                case Hotkeys.mute: self.conversation.toggleMute()
                default: break
                }
            }
            keys.released = { [weak self] id in
                if id == Hotkeys.pushToTalk { self?.conversation.releaseToTalk() }
            }
            hotkeys = keys
            if !keys.isPushToTalkAvailable {
                island.show(.error, title: "Push-to-talk shortcut unavailable",
                            detail: "Check for another app using Control–Shift–Space")
            } else {
                island.notice("Jarvis voice is ready")
            }
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
            ReadySound.play()
            conversation.pressToTalk()
        case .preparing, .capturing:
            conversation.releaseToTalk()
        case .finishing, .thinking, .speaking, .mutedReply:
            break
        }
    }

    @objc private func toggleMute() { conversation.toggleMute() }
    @objc private func toggleBackgroundSpeech() {
        conversation.toggleBackgroundSpeech()
        backgroundSpeechItem?.state = conversation.backgroundSpeechEnabled ? .on : .off
    }
    @objc private func toggleIsland() {
        island.enabled.toggle()
        islandItem?.state = island.enabled ? .on : .off
        UserDefaults.standard.set(island.enabled, forKey: "showJarvisIsland")
    }
    @objc private func installSpeechModel() { speechModelInstaller.install(island: island) }
    @objc private func checkPermissions() {
        let microphone = VoicePermissions.microphone
        let speech = VoicePermissions.speech
        let detail = "Microphone: \(microphone) · Speech: \(speech)"
        if microphone == "allowed" && speech == "allowed" {
            island.notice(detail)
        } else {
            island.show(.permissionDenied, title: "Voice permissions", detail: detail)
        }
    }
    @objc private func openDashboard() {
        if let url = URL(string: "http://127.0.0.1:4700/") { NSWorkspace.shared.open(url) }
    }
    @objc private func quit() {
        taskWatcher?.stop()
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
            print("Microphone: \(VoicePermissions.microphone); Speech: \(VoicePermissions.speech)")
            return
        }
        if arguments.first == "--check-speech-capability" {
            let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
            print("English recognizer available: \(recognizer?.isAvailable == true); on-device recognition: \(recognizer?.supportsOnDeviceRecognition == true)")
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
