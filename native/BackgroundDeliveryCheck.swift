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
        print("Background delivery policy checks: \(checks) passed")
    }
}
