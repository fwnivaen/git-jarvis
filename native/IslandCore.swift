import Foundation

enum IslandPhase: String, CaseIterable {
    case hidden, ready, waking, listening, thinking, working, speaking, mutedReply
    case done, notification, approval, releasedEarly, offline, permissionDenied
    case microphoneFailure, error, modelDownload
}

enum IslandReplyKind {
    case answer, failure, status
}

struct IslandDisplay: Equatable {
    let phase: IslandPhase
    let title: String
    let detail: String
    let wordIndex: Int?
    let microphoneLevel: Double
    var listeningExpanded = false

    var isExpanded: Bool {
        switch phase {
        case .listening:
            return listeningExpanded
        case .thinking, .working, .speaking, .mutedReply, .approval,
             .permissionDenied, .microphoneFailure, .error, .modelDownload:
            return true
        default:
            return false
        }
    }
}

struct IslandNotice {
    let title: String
    let createdAt: Date
}

struct IslandState {
    static let doneDuration: TimeInterval = 1.6
    static let errorDuration: TimeInterval = 4.0
    static let permissionDuration: TimeInterval = 6.0
    static let notificationDuration: TimeInterval = 4.0
    static let notificationExpiry: TimeInterval = 30.0
    static let earlyReleaseDuration: TimeInterval = 1.6
    static let maximumNotices = 32
    static let listeningExpandDelay: TimeInterval = 0.3

    private(set) var activePhase: IslandPhase?
    private(set) var activeTitle = ""
    private(set) var activeDetail = ""
    private(set) var activeWord: Int?
    private(set) var microphoneLevel = 0.0
    private(set) var firstListeningWordsAt: Date?
    private(set) var approvalTitle: String?
    private(set) var backgroundWorkTitle: String?
    private(set) var transient: IslandDisplay?
    private(set) var transientUntil: Date?
    private(set) var notices: [IslandNotice] = []
    var readyEnabled = false

    mutating func begin(_ phase: IslandPhase, title: String, detail: String = "") {
        precondition(Self.activePhases.contains(phase))
        activePhase = phase
        activeTitle = title
        activeDetail = detail
        activeWord = nil
        firstListeningWordsAt = nil
        microphoneLevel = 0
        transient = nil
        transientUntil = nil
    }

    mutating func update(title: String? = nil, detail: String? = nil,
                         wordIndex: Int? = nil, microphoneLevel: Double? = nil) {
        if let title { activeTitle = title }
        if let detail { activeDetail = detail }
        if let wordIndex { activeWord = wordIndex }
        if let microphoneLevel { self.microphoneLevel = min(1, max(0, microphoneLevel)) }
    }

    mutating func transcript(_ phrase: String, at now: Date = Date()) {
        guard activePhase == .listening else { return }
        let hasWords = !phrase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        activeTitle = hasWords ? phrase : "Listening"
        if hasWords && firstListeningWordsAt == nil { firstListeningWordsAt = now }
    }

    mutating func end(_ kind: IslandReplyKind, text: String, at now: Date = Date()) {
        activePhase = nil
        activeTitle = ""
        activeDetail = ""
        activeWord = nil
        microphoneLevel = 0
        switch kind {
        case .answer:
            setTransient(.done, title: "Done", detail: text, duration: Self.doneDuration, at: now)
        case .failure:
            setTransient(.error, title: "Jarvis needs attention", detail: text,
                         duration: Self.errorDuration, at: now)
        case .status:
            // Status speech is not evidence that an answer was delivered.
            transient = nil
            transientUntil = nil
        }
    }

    mutating func show(_ phase: IslandPhase, title: String, detail: String = "", at now: Date = Date()) {
        precondition(Self.transientPhases.contains(phase))
        activePhase = nil
        activeTitle = ""
        activeDetail = ""
        activeWord = nil
        microphoneLevel = 0
        let duration = phase == .permissionDenied ? Self.permissionDuration :
            phase == .releasedEarly ? Self.earlyReleaseDuration : Self.errorDuration
        setTransient(phase, title: title, detail: detail, duration: duration, at: now)
    }

    mutating func setApproval(_ title: String?) {
        approvalTitle = title?.isEmpty == false ? title : nil
    }

    mutating func setBackgroundWork(_ active: Bool) {
        backgroundWorkTitle = active ? "A background workflow is running" : nil
    }

    mutating func enqueueNotice(_ title: String, at now: Date = Date()) {
        guard !title.isEmpty else { return }
        notices.append(IslandNotice(title: title, createdAt: now))
        if notices.count > Self.maximumNotices { notices.removeFirst(notices.count - Self.maximumNotices) }
    }

    mutating func resolve(at now: Date = Date()) -> IslandDisplay {
        notices.removeAll { now.timeIntervalSince($0.createdAt) >= Self.notificationExpiry }
        if let phase = activePhase {
            return IslandDisplay(phase: phase, title: activeTitle, detail: activeDetail,
                                 wordIndex: activeWord, microphoneLevel: microphoneLevel,
                                 listeningExpanded: firstListeningWordsAt.map {
                                     now.timeIntervalSince($0) >= Self.listeningExpandDelay
                                 } ?? false)
        }
        if let approvalTitle {
            return IslandDisplay(phase: .approval, title: "Review ↗", detail: approvalTitle,
                                 wordIndex: nil, microphoneLevel: 0)
        }
        if let transient, let until = transientUntil, now < until { return transient }
        transient = nil
        transientUntil = nil
        if let first = notices.first {
            notices.removeFirst()
            setTransient(.notification, title: "Jarvis", detail: first.title,
                         duration: Self.notificationDuration, at: now)
            return transient!
        }
        if let backgroundWorkTitle {
            return IslandDisplay(phase: .working, title: "Working", detail: backgroundWorkTitle,
                                 wordIndex: nil, microphoneLevel: 0)
        }
        return IslandDisplay(phase: readyEnabled ? .ready : .hidden, title: "Jarvis",
                             detail: "", wordIndex: nil, microphoneLevel: 0)
    }

    private mutating func setTransient(_ phase: IslandPhase, title: String, detail: String,
                                       duration: TimeInterval, at now: Date) {
        transient = IslandDisplay(phase: phase, title: title, detail: detail,
                                  wordIndex: nil, microphoneLevel: 0)
        transientUntil = now.addingTimeInterval(duration)
    }

    private static let activePhases: Set<IslandPhase> = [
        .waking, .listening, .thinking, .working, .speaking, .mutedReply, .modelDownload
    ]
    private static let transientPhases: Set<IslandPhase> = [
        .releasedEarly, .offline, .permissionDenied, .microphoneFailure, .error
    ]
}

enum SpokenWords {
    static let expression = try! NSRegularExpression(pattern: #"\S+"#)

    static func index(for spokenRange: NSRange, in text: String) -> Int? {
        let full = NSRange(location: 0, length: (text as NSString).length)
        guard spokenRange.location != NSNotFound,
              spokenRange.location >= 0,
              spokenRange.length > 0,
              NSMaxRange(spokenRange) <= full.length else { return nil }
        let matches = expression.matches(in: text, range: full)
        return matches.firstIndex { NSIntersectionRange($0.range, spokenRange).length > 0 }
    }

    static func centeredText(_ text: String, activeWord: Int?, radius: Int = 6) -> String {
        let full = NSRange(location: 0, length: (text as NSString).length)
        let matches = expression.matches(in: text, range: full)
        guard let activeWord, matches.indices.contains(activeWord) else { return text }
        let first = max(0, activeWord - radius)
        let last = min(matches.count - 1, activeWord + radius)
        let source = text as NSString
        let excerpt = (first...last).map { source.substring(with: matches[$0].range) }.joined(separator: " ")
        return (first > 0 ? "… " : "") + excerpt + (last < matches.count - 1 ? " …" : "")
    }
}
