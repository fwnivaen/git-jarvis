import AppKit
import AVFoundation
import Speech

struct JarvisAnswer: Decodable {
    let answer: String
    let error: Bool?
}

final class MicrophoneMeter {
    private let lock = NSLock()
    private var level = 0.0
    private var lastAt = Date()

    func measure(_ buffer: AVReadOnlyAudioPCMBuffer) -> Double {
        guard case .float(let samples) = buffer.channelData(0) else { return 0 }
        let count = min(buffer.frameLength, Conversation.meterSamples)
        guard count > 0 else { return 0 }
        var sum = 0.0
        for index in (buffer.frameLength - count)..<buffer.frameLength {
            let sample = Double(samples[index])
            sum += sample * sample
        }
        let rms = sqrt(sum / Double(count))
        let db = rms > 0 ? 20 * log10(rms) : Conversation.initialSilenceFloorDB
        let target = min(1, max(0, (db - IslandDesign.meterFloorDB) /
            (IslandDesign.meterCeilingDB - IslandDesign.meterFloorDB)))
        lock.lock()
        defer { lock.unlock() }
        let now = Date()
        let interval = max(0, now.timeIntervalSince(lastAt))
        let constant = target > level ? IslandDesign.meterAttack : IslandDesign.meterRelease
        level += (target - level) * (1 - exp(-interval / constant))
        lastAt = now
        return level
    }
}

@MainActor
final class Conversation: NSObject {
    static let serverURL = URL(string: "http://127.0.0.1:4700/chat")!
    nonisolated static let postRollSeconds = 0.25
    nonisolated static let finalPhraseWaitSeconds = 1.5
    static let requestBudgetSeconds = 110.0
    nonisolated static let meterSamples = 1024
    static let mutedWordsPerMinute = 180.0
    nonisolated static let initialSilenceFloorDB = -90.0

    enum Phase { case idle, preparing, capturing, finishing, thinking, renderingSpeech, speaking, mutedReply }
    private(set) var phase = Phase.idle { didSet { onPhase?(phase) } }
    private(set) var muted = false
    private(set) var backgroundSpeechEnabled = UserDefaults.standard.bool(forKey: "speakBackgroundUpdates")
    var onPhase: ((Phase) -> Void)?
    private let island: IslandController
    private let speaker = Speaker(base: URL(string: "http://127.0.0.1:4700")!)
    private var speechTask: Task<Void, Never>?
    private var recognizer: SFSpeechRecognizer?
    private var engine: AVAudioEngine?
    private var speechRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var modernCapture: ModernCapture?
    private var captureStartup: Task<Void, Never>?
    private var captureCleanup: Task<Void, Never>?
    private var networkTask: URLSessionDataTask?
    private var capturedPhrase = ""
    private var latestPhrase = ""
    private var meter = MicrophoneMeter()
    private var tapInstalled = false
    private var buttonHeld = false
    private var voiceLock: VoiceLock?
    private var spokenAnswer = ""
    private var spokenKind: IslandReplyKind = .status
    private var mutedTimer: Timer?
    private var background = BackgroundDelivery()
    private var backgroundWork: DispatchWorkItem?
    private var deliveringBackground = false
    var backgroundPaused: Bool { background.paused }
    private var generation = 0

    init(island: IslandController) {
        self.island = island
        super.init()
        speaker.onPlayback = { [weak self] playing in
            guard let self, playing, self.phase == .renderingSpeech else { return }
            self.phase = .speaking
            self.island.begin(.speaking, title: "Jarvis · M1 voice preview", detail: self.spokenAnswer)
        }
        speaker.onWord = { [weak self] range in
            guard let self, self.phase == .speaking,
                  let index = SpokenWords.index(for: range, in: self.spokenAnswer) else { return }
            self.island.update(wordIndex: index)
        }
        speaker.onFailure = { [weak self] _ in
            self?.island.notice("Voice stopped · the full reply is shown")
        }
    }

    func pressToTalk() {
        guard phase == .idle, !buttonHeld else { return }
        buttonHeld = true
        if !muted { speaker.warm() }
        latestPhrase = ""
        generation += 1
        let current = generation
        phase = .preparing
        island.begin(.waking, title: "Preparing microphone")
        let microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        if microphoneStatus == .denied || microphoneStatus == .restricted {
            fail(.permissionDenied, "Microphone permission is off")
            return
        }
        AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                guard allowed else { self.fail(.permissionDenied, "Allow microphone access in System Settings"); return }
                self.startCapture(current)
            }
        }
    }

    private func authorizeSpeech(_ current: Int) {
        let status = SFSpeechRecognizer.authorizationStatus()
        if status == .denied || status == .restricted {
            fail(.permissionDenied, "Allow speech recognition in System Settings")
            return
        }
        SFSpeechRecognizer.requestAuthorization { [weak self] status in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                guard status == .authorized else {
                    self.fail(.permissionDenied, "Allow speech recognition in System Settings")
                    return
                }
                self.startLegacyCapture(current)
            }
        }
    }

    private func startCapture(_ current: Int) {
        guard phase == .preparing, buttonHeld else { return }
        let previousCleanup = captureCleanup
        captureStartup = Task { @MainActor [weak self] in
            await previousCleanup?.value
            guard let self else { return }
            guard !Task.isCancelled, self.generation == current, self.phase == .preparing, self.buttonHeld else { return }
            guard let voiceLock = VoiceLock() else {
                self.fail(.microphoneFailure, "Another Jarvis voice session is using the microphone")
                return
            }
            self.voiceLock = voiceLock
            defer {
                // A cancelled startup retains ownership until it has stopped,
                // so the next turn cannot open a second audio engine.
                withExtendedLifetime(voiceLock) {}
                if self.generation == current { self.captureStartup = nil }
            }
            let modernAvailable = await ModernCapture.canUseEnglish()
            guard !Task.isCancelled, self.generation == current,
                  self.phase == .preparing, self.buttonHeld else { return }
            if modernAvailable {
                do {
                    let capture = try await ModernCapture.start(
                        onPhrase: { [weak self] phrase in
                            guard let self, self.generation == current,
                                  self.phase == .capturing || self.phase == .finishing else { return }
                            self.latestPhrase = phrase
                            if self.phase == .capturing {
                                self.island.transcript(phrase)
                            }
                        },
                        onLevel: { [weak self] level in
                            guard let self, self.generation == current, self.phase == .capturing else { return }
                            self.island.update(microphoneLevel: level)
                        },
                        onFinished: { [weak self] phrase in
                            guard let self, self.generation == current, self.phase == .finishing else { return }
                            self.submit(phrase)
                        },
                        onError: { [weak self] in
                            guard let self, self.generation == current,
                                  self.phase == .capturing || self.phase == .finishing else { return }
                            self.fail(.microphoneFailure, "On-device speech recognition stopped")
                        })
                    guard !Task.isCancelled, self.generation == current,
                          self.phase == .preparing, self.buttonHeld else {
                        await capture.cancel().value
                        return
                    }
                    self.modernCapture = capture
                    self.phase = .capturing
                    self.island.begin(.listening, title: "Listening")
                    return
                } catch is CancellationError {
                    return
                } catch {
                    // The installed model may be temporarily unavailable; use
                    // the existing on-device recognizer for this turn.
                }
            }
            guard !Task.isCancelled, self.generation == current,
                  self.phase == .preparing, self.buttonHeld else { return }
            self.authorizeSpeech(current)
        }
    }

    private func startLegacyCapture(_ current: Int) {
        guard phase == .preparing, buttonHeld, generation == current else { return }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
              recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
            fail(.microphoneFailure, "On-device English speech is unavailable")
            return
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            fail(.microphoneFailure, "No microphone input is available")
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        capturedPhrase = ""
        latestPhrase = ""
        meter = MicrophoneMeter()
        let meter = self.meter
        do {
            try input.installAudioTap(onBus: 0, bufferSize: AVAudioFrameCount(Self.meterSamples),
                                      format: format) { [weak self] buffer, _ in
                request.append(AVAudioPCMBuffer(copying: buffer))
                let level = meter.measure(buffer)
                DispatchQueue.main.async {
                    guard let self, self.generation == current, self.phase == .capturing else { return }
                    self.island.update(microphoneLevel: level)
                }
            }
            tapInstalled = true
        } catch {
            fail(.microphoneFailure, "The microphone tap could not start")
            return
        }
        self.engine = engine
        self.recognizer = recognizer
        self.speechRequest = request
        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                if let result {
                    let phrase = result.bestTranscription.formattedString
                    self.latestPhrase = phrase
                    if self.phase == .capturing {
                        self.island.transcript(phrase)
                    } else if self.phase == .finishing, result.isFinal {
                        self.submit(phrase)
                    }
                } else if error != nil, self.phase == .capturing || self.phase == .finishing {
                    self.fail(.microphoneFailure, "Speech recognition stopped")
                }
            }
        }
        do {
            try engine.start()
            phase = .capturing
            island.begin(.listening, title: "Listening")
        } catch {
            fail(.microphoneFailure, "The microphone could not start")
        }
    }

    func releaseToTalk() {
        buttonHeld = false
        if phase == .preparing {
            generation += 1
            stopCapture()
            phase = .idle
            island.show(.releasedEarly, title: "Hold to speak", detail: "The microphone was still preparing")
            scheduleBackground()
            return
        }
        guard phase == .capturing else { return }
        phase = .finishing
        let current = generation
        island.update(title: latestPhrase.isEmpty ? "Finishing…" : latestPhrase)
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.postRollSeconds) { [weak self] in
            guard let self, self.generation == current, self.phase == .finishing else { return }
            if self.tapInstalled {
                self.engine?.inputNode.removeTap(onBus: 0)
                self.tapInstalled = false
            }
            self.engine?.stop()
            if let modernCapture = self.modernCapture {
                modernCapture.finish()
                return
            }
            self.speechRequest?.endAudio()
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.finalPhraseWaitSeconds) { [weak self] in
                guard let self, self.generation == current, self.phase == .finishing else { return }
                self.submit(self.latestPhrase)
            }
        }
    }

    func ask(_ question: String) {
        if !muted { speaker.warm() }
        guard phase == .idle else { return }
        let value = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        generation += 1
        submit(value)
    }

    private func submit(_ phrase: String) {
        guard phase == .finishing || phase == .idle else { return }
        stopCapture()
        let value = phrase.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            phase = .idle
            island.show(.releasedEarly, title: "I didn't catch that", detail: "Hold the keys while speaking")
            scheduleBackground()
            return
        }
        capturedPhrase = value
        phase = .thinking
        island.begin(.thinking, title: "Thinking", detail: value)
        var request = URLRequest(url: Self.serverURL)
        request.httpMethod = "POST"
        request.timeoutInterval = Self.requestBudgetSeconds
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["question": value])
        let current = generation
        networkTask = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self, self.generation == current, self.phase == .thinking else { return }
                if error != nil {
                    self.deliver("The local Jarvis server is unavailable", kind: .failure)
                    return
                }
                guard let http = response as? HTTPURLResponse, let data,
                      let answer = try? JSONDecoder().decode(JarvisAnswer.self, from: data),
                      !answer.answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    self.deliver("Jarvis returned an unreadable reply", kind: .failure)
                    return
                }
                let kind: IslandReplyKind = http.statusCode == 200 && answer.error != true ? .answer :
                    http.statusCode == 429 ? .status : .failure
                self.deliver(answer.answer, kind: kind)
            }
        }
        networkTask?.resume()
    }

    private func deliver(_ answer: String, kind: IslandReplyKind) {
        spokenAnswer = answer
        spokenKind = kind
        if muted {
            phase = .mutedReply
            island.begin(.mutedReply, title: "Jarvis", detail: answer)
            paceMutedText(answer)
            return
        }
        let current = generation
        phase = .renderingSpeech
        island.begin(.thinking, title: "Preparing M1 voice preview", detail: answer)
        speechTask = Task { [weak self] in
            guard let self else { return }
            await self.speaker.say(answer)
            guard !Task.isCancelled, self.generation == current,
                  self.phase == .renderingSpeech || self.phase == .speaking else { return }
            self.finishReply()
        }
    }

    private func paceMutedText(_ answer: String) {
        let wordCount = SpokenWords.expression.matches(in: answer,
            range: NSRange(location: 0, length: (answer as NSString).length)).count
        var index = 0
        mutedTimer?.invalidate()
        mutedTimer = Timer.scheduledTimer(withTimeInterval: 60 / Self.mutedWordsPerMinute,
                                          repeats: true) { [weak self] timer in
            MainActor.assumeIsolated {
                guard let self, self.phase == .mutedReply else { timer.invalidate(); return }
                if index >= wordCount {
                    timer.invalidate()
                    self.finishReply()
                } else {
                    self.island.update(wordIndex: index)
                    index += 1
                }
            }
        }
    }

    private func finishReply() {
        speechTask?.cancel(); speechTask = nil
        deliveringBackground = false
        phase = .idle
        island.end(spokenKind, text: spokenAnswer)
        spokenAnswer = ""
        capturedPhrase = ""
        scheduleBackground()
    }

    func cancel() {
        guard phase != .idle else { return }
        let requestWasRunning = phase == .thinking
        generation += 1
        buttonHeld = false
        mutedTimer?.invalidate()
        networkTask?.cancel()
        speechTask?.cancel(); speechTask = nil
        deliveringBackground = false
        speaker.stop()
        stopCapture()
        phase = .idle
        island.end(.status, text: requestWasRunning ? "Reply dismissed" : "Cancelled")
        if requestWasRunning {
            island.notice("Reply dismissed. The request may still finish on the server.")
        }
        scheduleBackground()
    }

    func toggleMute() {
        muted.toggle()
        if (phase == .speaking || phase == .renderingSpeech) && muted {
            generation += 1
            speechTask?.cancel(); speechTask = nil
            speaker.stop()
            phase = .mutedReply
            island.begin(.mutedReply, title: "Jarvis", detail: spokenAnswer)
            paceMutedText(spokenAnswer)
        }
        if !muted { scheduleBackground() }
    }

    func toggleBackgroundSpeech() {
        setBackgroundSpeech(!backgroundSpeechEnabled)
    }

    func setBackgroundSpeech(_ enabled: Bool) {
        backgroundSpeechEnabled = enabled
        UserDefaults.standard.set(backgroundSpeechEnabled, forKey: "speakBackgroundUpdates")
        if !backgroundSpeechEnabled { stopBackgroundSpeech() }
        scheduleBackground()
    }

    func applyDeliveryPolicy(_ policy: TaskDeliveryPolicy) {
        background.quietHoursActive = policy.quietHoursActive
        if policy.managed { setBackgroundSpeech(policy.speakBackgroundUpdates) }
        refreshBackgroundEligibility()
    }

    func setBackgroundPreferencePending(_ pending: Bool) {
        background.preferenceUpdatePending = pending
        refreshBackgroundEligibility()
    }

    func setForegroundInputActive(_ active: Bool) {
        guard background.foregroundInputActive != active else { return }
        background.foregroundInputActive = active
        refreshBackgroundEligibility()
    }

    func backgroundUpdate(_ text: String, id: String = UUID().uuidString) {
        background.enqueue(id: id, text: text)
        scheduleBackground()
    }

    func setBackgroundAvailability(sessionActive: Bool? = nil, displayAwake: Bool? = nil) {
        if let sessionActive { background.sessionActive = sessionActive }
        if let displayAwake { background.displayAwake = displayAwake }
        refreshBackgroundEligibility()
    }

    func toggleBackgroundPause() {
        background.paused.toggle()
        UserDefaults.standard.set(background.paused, forKey: "pauseBackgroundUpdates")
        refreshBackgroundEligibility()
    }

    func restoreBackgroundPause() {
        background.paused = UserDefaults.standard.bool(forKey: "pauseBackgroundUpdates")
    }

    private func refreshBackgroundEligibility() {
        backgroundWork?.cancel()
        backgroundWork = nil
        if !background.eligible { stopBackgroundSpeech() }
        scheduleBackground()
    }

    private func stopBackgroundSpeech() {
        guard deliveringBackground else { return }
        speechTask?.cancel(); speechTask = nil
        deliveringBackground = false
        mutedTimer?.invalidate()
        speaker.stop()
        phase = .idle
        island.end(.status, text: "")
        spokenAnswer = ""
    }

    private func scheduleBackground() {
        background.discardExpired()
        guard backgroundWork == nil, phase == .idle, background.eligible, !background.pending.isEmpty else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.backgroundWork = nil
            guard self.phase == .idle, let next = self.background.next() else { return }
            if self.backgroundSpeechEnabled && !self.muted {
                self.deliveringBackground = true
                self.deliver(next.text, kind: .status)
            } else {
                self.island.notice(next.text)
                let nextWork = DispatchWorkItem { [weak self] in
                    self?.backgroundWork = nil
                    self?.scheduleBackground()
                }
                self.backgroundWork = nextWork
                DispatchQueue.main.asyncAfter(deadline: .now() + IslandState.notificationDuration, execute: nextWork)
            }
        }
        backgroundWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + IslandState.doneDuration, execute: work)
    }

    private func fail(_ phase: IslandPhase, _ message: String) {
        buttonHeld = false
        stopCapture()
        self.phase = .idle
        island.show(phase, title: "Jarvis needs attention", detail: message)
        scheduleBackground()
    }

    private func stopCapture() {
        let retainedLock = voiceLock
        let startup = captureStartup
        startup?.cancel()
        captureStartup = nil
        let analyzerCleanup = modernCapture?.cancel()
        modernCapture = nil
        if let engine {
            if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
            engine.stop()
        }
        speechRequest?.endAudio()
        recognitionTask?.cancel()
        engine = nil
        recognizer = nil
        speechRequest = nil
        recognitionTask = nil
        voiceLock = nil
        if startup != nil || analyzerCleanup != nil {
            captureCleanup = HandsFreeHandoff.join([captureCleanup, startup, analyzerCleanup], retaining: retainedLock)
        }
    }

    func waitForCaptureCleanup() async { await captureCleanup?.value }

}
