import Foundation

enum HandsFreeLimits {
    static let wakePhrase = "Hey Jarvis"
    static let silence: TimeInterval = 1.1
    static let emptyCommand: TimeInterval = 8
    static let command: TimeInterval = 20
    static let followup: TimeInterval = 6
    static let wakeRestart: TimeInterval = 30
    static let speechLevel = 0.12
    static let maximumTranscriptCharacters = 4096
    static let maximumQuestionCharacters = 1000
    static let pollInterval: TimeInterval = 0.1
}

enum HandsFreeText {
    // Exact word boundaries avoid waking for "they Jarvis" or "Jarvison".
    // All matching and ambient transcription stay on the Mac.
    private static let expression = try! NSRegularExpression(
        pattern: "(?i)(?<![\\p{L}\\p{N}_])hey[\\s,!.?—–-]+jarvis(?![\\p{L}\\p{N}_])")

    static func command(in transcript: String) -> String? {
        let range = NSRange(transcript.startIndex..., in: transcript)
        guard let match = expression.matches(in: transcript, range: range).last,
              let end = Range(match.range, in: transcript)?.upperBound else { return nil }
        return clean(String(transcript[end...]))
    }

    static func clean(_ value: String) -> String {
        let trimmed = value.drop(while: { $0.isWhitespace || $0.isPunctuation })
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return String(trimmed.prefix(HandsFreeLimits.maximumQuestionCharacters))
    }
}

// A consent dialog runs a nested event loop. Global cancel, mute or PTT can
// change the voice state before its Enable button returns to the caller.
struct HandsFreeConsent {
    let generation: Int

    func allowsActivation(currentGeneration: Int, conversationIdle: Bool,
                          muted: Bool, manualCapturePending: Bool) -> Bool {
        generation == currentGeneration && conversationIdle && !muted && !manualCapturePending
    }
}

// Pure end-of-turn policy. Monotonic uptime is supplied by the controller;
// ambient speech cannot produce a request without the explicit wake phrase.
struct HandsFreeTurn {
    enum Stage { case wake, command, followup, finishing }
    enum Decision { case none, woke, finish, expired, restart }
    private(set) var stage: Stage
    private(set) var question = ""
    private let needsWake: Bool
    private var started: TimeInterval
    private var lastSpeech: TimeInterval
    private var lastText: TimeInterval

    init(followup: Bool = false, now: TimeInterval) {
        stage = followup ? .followup : .wake
        needsWake = !followup
        started = now
        lastSpeech = now
        lastText = now
    }

    mutating func transcript(_ text: String, now: TimeInterval) -> Decision {
        if stage == .wake {
            guard let command = HandsFreeText.command(in: text) else { return .none }
            stage = .command
            started = now
            lastSpeech = now
            lastText = now
            question = command
            return .woke
        }
        let updated = needsWake ? (HandsFreeText.command(in: text) ?? "") : HandsFreeText.clean(text)
        if updated != question { question = updated; lastText = now }
        if stage == .followup, !updated.isEmpty {
            stage = .command
            started = now
            lastSpeech = now
        }
        return .none
    }

    mutating func level(_ value: Double, now: TimeInterval) {
        if value.isFinite, value >= HandsFreeLimits.speechLevel { lastSpeech = now }
    }

    mutating func poll(now: TimeInterval) -> Decision {
        switch stage {
        case .wake:
            return now - started >= HandsFreeLimits.wakeRestart ? .restart : .none
        case .followup:
            return now - started >= HandsFreeLimits.followup ? .expired : .none
        case .command:
            if question.isEmpty {
                return now - started >= HandsFreeLimits.emptyCommand ? .expired : .none
            }
            if now - started >= HandsFreeLimits.command ||
                now - max(lastText, lastSpeech) >= HandsFreeLimits.silence {
                stage = .finishing
                return .finish
            }
            return .none
        case .finishing: return .none
        }
    }
}

// Awaiting cancelled startup is essential: the startup task retains VoiceLock
// until all asynchronous model/analyzer setup has stopped. PTT must wait for
// that ownership to end rather than racing a second engine against it.
enum HandsFreeHandoff {
    static func join(_ tasks: [Task<Void, Never>?], retaining resource: AnyObject? = nil) -> Task<Void, Never> {
        Task { @MainActor in
            for task in tasks { await task?.value }
            withExtendedLifetime(resource) {}
        }
    }

    static func cancelAndWait(_ pending: Task<Void, Never>?,
                              then completion: @escaping () -> Void) -> Task<Void, Never> {
        pending?.cancel()
        return Task { @MainActor in
            await pending?.value
            completion()
        }
    }
}
