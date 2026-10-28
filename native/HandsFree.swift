import AppKit
import AVFoundation

// All entry points and callbacks run on the main run loop. The opt-in is
// deliberately ephemeral: constructing or relaunching this object never opens
// a microphone. Only the explicit menu privacy choice may call enable().
final class HandsFreeController: @unchecked Sendable {
    private let island: IslandController
    private let conversationIsIdle: () -> Bool
    private let submit: (String) -> Void
    private let awaitConversationCleanup: () async -> Void
    var onStatus: (Bool, String) -> Void = { _, _ in }
    private(set) var enabled = false
    private var status = "Hands-free off"
    private var generation = 0
    private var startup: Task<Void, Never>?
    private var handoff: Task<Void, Never>?
    private var capture: ModernCapture?
    private var voiceLock: VoiceLock?
    private var turn: HandsFreeTurn?
    private var timer: Timer?
    private var postRollWork: DispatchWorkItem?
    private var finalWork: DispatchWorkItem?
    private var suspendedForPushToTalk = false
    private var waitingForReply = false
    private var announcedEnable = false
    private var islandOwned = false
    var hasActiveTurn: Bool { islandOwned }
    var needsCaptureHandoff: Bool { startup != nil || capture != nil }

    init(island: IslandController, conversationIsIdle: @escaping () -> Bool,
         awaitConversationCleanup: @escaping () async -> Void = {}, submit: @escaping (String) -> Void) {
        self.island = island
        self.conversationIsIdle = conversationIsIdle
        self.submit = submit
        self.awaitConversationCleanup = awaitConversationCleanup
    }

    func enable() {
        guard !enabled else { return }
        enabled = true
        suspendedForPushToTalk = false
        waitingForReply = false
        announcedEnable = false
        arm(followup: false)
    }

    func disable(reason: String = "Hands-free off") {
        let ownedIsland = islandOwned
        islandOwned = false
        enabled = false
        suspendedForPushToTalk = false
        waitingForReply = false
        _ = invalidateCapture()
        setStatus(reason)
        if ownedIsland, conversationIsIdle() { island.end(.status, text: reason) }
    }

    // PTT keeps its existing capture pipeline. Wait for a cancelled startup to
    // unwind before handing over the shared recording-owner-v1 microphone lock.
    func yieldForPushToTalk(then completion: @escaping () -> Void) {
        if islandOwned {
            islandOwned = false
            island.end(.status, text: "")
        }
        suspendedForPushToTalk = true
        waitingForReply = false
        let previous = invalidateCapture()
        setStatus("Hands-free paused for push-to-talk")
        handoff = HandsFreeHandoff.cancelAndWait(previous, then: completion)
    }

    func resumeAfterUnstartedPushToTalk() {
        suspendedForPushToTalk = false
        guard enabled, conversationIsIdle() else { return }
        arm(followup: false)
    }

    func conversationChanged(_ phase: Conversation.Phase) {
        guard enabled else { return }
        if phase == .idle {
            suspendedForPushToTalk = false
            let followup = waitingForReply
            waitingForReply = false
            arm(followup: followup)
        } else {
            islandOwned = false
            _ = invalidateCapture()
            setStatus("Hands-free paused while Jarvis responds")
        }
    }

    private func setStatus(_ value: String) {
        status = value
        onStatus(enabled, value)
    }

    private func arm(followup: Bool) {
        guard enabled, !suspendedForPushToTalk, conversationIsIdle() else { return }
        let previous = invalidateCapture()
        let current = generation
        // The promised follow-up owns the conversation even while its analyzer
        // prepares. A queued background reply must not take over this window.
        islandOwned = followup
        setStatus("Hands-free preparing")
        startup = Task { @MainActor [weak self] in
            await previous?.value
            guard let self, self.isCurrent(current) else { return }
            await self.awaitConversationCleanup()
            guard self.isCurrent(current) else { return }
            defer { if self.generation == current { self.startup = nil } }
            guard await ModernCapture.canUseEnglish(), self.isCurrent(current) else {
                if self.isCurrent(current) { self.fail("Install the on-device English speech model before enabling hands-free") }
                return
            }
            let allowed = await Self.authorizeMicrophone()
            guard self.isCurrent(current) else { return }
            guard allowed else { self.fail("Allow microphone access before enabling hands-free"); return }
            guard let lock = VoiceLock() else {
                self.fail("Another Jarvis voice session is using the microphone")
                return
            }
            self.voiceLock = lock
            // A cancelled asynchronous start retains microphone ownership until
            // ModernCapture has stopped setup and removed its audio tap.
            defer { withExtendedLifetime(lock) {} }
            do {
                let input = try await ModernCapture.start(
                    onPhrase: { [weak self] text in self?.transcript(text, generation: current) },
                    onLevel: { [weak self] level in self?.level(level, generation: current) },
                    onFinished: { [weak self] text in self?.finished(text, generation: current) },
                    onError: { [weak self] in
                        guard let self, self.isCurrent(current) else { return }
                        self.fail("Hands-free speech recognition stopped; enable it again when ready")
                    }, maximumTranscriptCharacters: HandsFreeLimits.maximumTranscriptCharacters)
                guard self.isCurrent(current) else { await input.cancel().value; return }
                self.capture = input
                self.turn = HandsFreeTurn(followup: followup, now: Self.now)
                self.islandOwned = followup
                self.setStatus(followup ? "Hands-free: follow-up listening" : "Hands-free: say “Hey Jarvis”")
                if followup {
                    self.islandOwned = true
                    self.island.begin(.listening, title: "Follow-up listening")
                } else if !self.announcedEnable {
                    self.announcedEnable = true
                    self.island.notice("Hands-free is on. Say “Hey Jarvis”.")
                }
                let timer = Timer(timeInterval: HandsFreeLimits.pollInterval, repeats: true) { [weak self] _ in
                    self?.poll(generation: current)
                }
                self.timer = timer
                RunLoop.main.add(timer, forMode: .common)
            } catch is CancellationError {
                return
            } catch {
                if self.isCurrent(current) { self.fail("Hands-free microphone could not start") }
            }
        }
    }

    private static var now: TimeInterval { ProcessInfo.processInfo.systemUptime }

    private static func authorizeMicrophone() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { continuation.resume(returning: $0) }
            }
        default: return false
        }
    }

    private func isCurrent(_ value: Int) -> Bool {
        enabled && generation == value && !suspendedForPushToTalk && conversationIsIdle()
    }

    private func transcript(_ text: String, generation current: Int) {
        guard isCurrent(current), var turn else { return }
        let decision = turn.transcript(text, now: Self.now)
        self.turn = turn
        if decision == .woke {
            islandOwned = true
            ReadySound.play()
            setStatus("Hands-free: listening")
            island.begin(.listening, title: "Listening")
        }
        if turn.stage == .command || turn.stage == .finishing {
            island.transcript(turn.question)
        }
    }

    private func level(_ level: Double, generation current: Int) {
        guard isCurrent(current), var turn else { return }
        turn.level(level, now: Self.now)
        self.turn = turn
        if turn.stage != .wake { island.update(microphoneLevel: level) }
    }

    private func poll(generation current: Int) {
        guard isCurrent(current), var turn else { return }
        let decision = turn.poll(now: Self.now)
        self.turn = turn
        switch decision {
        case .finish:
            timer?.invalidate()
            timer = nil
            setStatus("Hands-free: finishing")
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.isCurrent(current), self.turn?.stage == .finishing else { return }
                self.capture?.finish()
                let fallback = DispatchWorkItem { [weak self] in
                    guard let self, self.isCurrent(current), self.turn?.stage == .finishing else { return }
                    self.submitQuestion()
                }
                self.finalWork = fallback
                DispatchQueue.main.asyncAfter(deadline: .now() + Conversation.finalPhraseWaitSeconds, execute: fallback)
            }
            postRollWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + Conversation.postRollSeconds, execute: work)
        case .expired:
            islandOwned = false
            island.end(.status, text: "Listening window ended")
            arm(followup: false)
        case .restart: arm(followup: false)
        case .none, .woke: break
        }
    }

    private func finished(_ text: String, generation current: Int) {
        guard isCurrent(current), var turn, turn.stage == .finishing else { return }
        _ = turn.transcript(text, now: Self.now)
        self.turn = turn
        submitQuestion()
    }

    private func submitQuestion() {
        let question = turn?.question ?? ""
        let ownedIsland = islandOwned
        islandOwned = false
        _ = invalidateCapture()
        guard enabled, conversationIsIdle(), !question.isEmpty else {
            if ownedIsland, conversationIsIdle() { island.end(.status, text: "I didn't catch a request") }
            if enabled { arm(followup: false) }
            return
        }
        waitingForReply = true
        setStatus("Hands-free paused while Jarvis responds")
        submit(question)
    }

    @discardableResult
    private func invalidateCapture() -> Task<Void, Never>? {
        generation += 1
        let previous = startup
        let retainedLock = voiceLock
        previous?.cancel()
        let cleanup = capture?.cancel()
        capture = nil
        voiceLock = nil
        turn = nil
        timer?.invalidate()
        timer = nil
        postRollWork?.cancel()
        postRollWork = nil
        finalWork?.cancel()
        finalWork = nil
        if let cleanup {
            let barrier = HandsFreeHandoff.join([previous, cleanup], retaining: retainedLock)
            startup = barrier
            return barrier
        }
        return previous
    }

    private func fail(_ detail: String) {
        disable(reason: "Hands-free off")
        island.show(.microphoneFailure, title: "Hands-free unavailable", detail: detail)
    }

    deinit {
        startup?.cancel()
        capture?.cancel()
        timer?.invalidate()
        postRollWork?.cancel()
        finalWork?.cancel()
    }
}
