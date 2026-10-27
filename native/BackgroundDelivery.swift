import Foundation

/// Ephemeral delivery policy. No transcript persistence or inferred call/DND state.
struct BackgroundDelivery {
    static let expiry: TimeInterval = 30
    static let maximumPending = 16
    static let maximumRemembered = 128
    static let maximumCharacters = 200

    struct Update {
        let id: String
        let text: String
        let createdAt: Date
    }
    var paused = false
    var sessionActive = true
    var displayAwake = true
    private(set) var pending: [Update] = []
    private var remembered: [String] = []
    var eligible: Bool { !paused && sessionActive && displayAwake }

    mutating func enqueue(id: String, text: String, now: Date = Date()) {
        discardExpired(now: now)
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty, !value.isEmpty, !remembered.contains(id) else { return }
        remembered.append(id)
        if remembered.count > Self.maximumRemembered {
            remembered.removeFirst(remembered.count - Self.maximumRemembered)
        }
        pending.append(Update(id: id, text: String(value.prefix(Self.maximumCharacters)), createdAt: now))
        if pending.count > Self.maximumPending { pending.removeFirst(pending.count - Self.maximumPending) }
    }

    mutating func next(now: Date = Date()) -> Update? {
        discardExpired(now: now)
        guard eligible, !pending.isEmpty else { return nil }
        return pending.removeFirst()
    }

    mutating func discardExpired(now: Date = Date()) {
        pending.removeAll { now.timeIntervalSince($0.createdAt) >= Self.expiry }
    }
}
