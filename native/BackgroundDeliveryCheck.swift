import Foundation

@main struct BackgroundDeliveryCheck {
    static func main() {
        var checks = 0
        func check(_ value: Bool) { precondition(value); checks += 1 }
        let now = Date(timeIntervalSince1970: 1_000)
        var queue = BackgroundDelivery()
        check(queue.eligible)
        queue.enqueue(id: "one", text: "  A task finished.  ", now: now)
        queue.enqueue(id: "one", text: "Duplicate", now: now)
        check(queue.pending.count == 1)
        queue.paused = true
        check(queue.next(now: now) == nil && queue.pending.count == 1)
        queue.paused = false; queue.sessionActive = false
        check(queue.next(now: now) == nil)
        queue.sessionActive = true; queue.displayAwake = false
        check(queue.next(now: now) == nil)
        queue.displayAwake = true
        check(queue.next(now: now)?.text == "A task finished.")
        queue.enqueue(id: "one", text: "Replay after delivery", now: now)
        check(queue.next(now: now) == nil)
        queue.enqueue(id: "", text: "invalid", now: now)
        queue.enqueue(id: "blank", text: " \n", now: now)
        check(queue.pending.isEmpty)
        queue.enqueue(id: "stale", text: "Too late", now: now)
        check(queue.next(now: now.addingTimeInterval(BackgroundDelivery.expiry)) == nil)
        queue.enqueue(id: "long", text: String(repeating: "é", count: 500), now: now)
        check(queue.next(now: now)?.text.count == BackgroundDelivery.maximumCharacters)
        for i in 0..<100 { queue.enqueue(id: "bounded-\(i)", text: "Update", now: now) }
        check(queue.pending.count == BackgroundDelivery.maximumPending)
        check(queue.next(now: now)?.id == "bounded-84")
        queue.paused = true
        check(queue.next(now: now.addingTimeInterval(31)) == nil && queue.pending.isEmpty)
        check(!queue.eligible)
        var quiet = BackgroundDelivery()
        quiet.enqueue(id: "quiet", text: "Keep this private until quiet hours end", now: now)
        quiet.quietHoursActive = true
        check(quiet.next(now: now) == nil && quiet.pending.count == 1)
        quiet.quietHoursActive = false
        check(quiet.next(now: now.addingTimeInterval(1))?.id == "quiet")
        quiet.preferenceUpdatePending = true
        quiet.enqueue(id: "pending-save", text: "Waiting for settings acknowledgment", now: now)
        check(quiet.next(now: now) == nil)
        quiet.preferenceUpdatePending = false
        check(quiet.next(now: now)?.id == "pending-save")
        var input = BackgroundDelivery()
        input.foregroundInputActive = true
        input.enqueue(id: "during-dictation", text: "A background task completed", now: now)
        check(!input.eligible && input.next(now: now) == nil && input.pending.count == 1)
        // Releasing a follow-up/handing over PTT must not bypass another gate.
        input.preferenceUpdatePending = true
        input.foregroundInputActive = false
        check(input.next(now: now) == nil && input.pending.count == 1)
        input.preferenceUpdatePending = false
        check(input.next(now: now.addingTimeInterval(1))?.id == "during-dictation")
        check(input.next(now: now.addingTimeInterval(1)) == nil)
        input.foregroundInputActive = true
        input.enqueue(id: "expired-during-input", text: "An old update", now: now)
        input.foregroundInputActive = false
        check(input.next(now: now.addingTimeInterval(BackgroundDelivery.expiry)) == nil && input.pending.isEmpty)
        print("Background delivery policy checks: \(checks) passed")
    }
}
