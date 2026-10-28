import Foundation

@main
enum HandsFreeCheck {
    @MainActor static func main() async {
        var passed = 0
        func check(_ value: Bool, _ message: String) {
            precondition(value, message)
            passed += 1
        }
        check(HandsFreeText.command(in: "We should plan tomorrow") == nil, "ambient speech cannot submit")
        check(HandsFreeText.command(in: "Hey Jarvison write a note") == nil, "wake suffix is rejected")
        check(HandsFreeText.command(in: "They Jarvis write a note") == nil, "near wake phrase is rejected")
        check(HandsFreeText.command(in: "HEY, JARVIS! Café 👩🏽‍💻 résumé") == "Café 👩🏽‍💻 résumé", "wake parsing preserves Unicode")
        check(HandsFreeText.command(in: "Hey Jarvis") == "", "wake alone is not a command")
        check(HandsFreeText.clean(String(repeating: "x", count: 2000)).count == 1000, "questions are bounded")

        var turn = HandsFreeTurn(now: 0)
        check(turn.transcript("Ambient private conversation", now: 1) == .none, "ambient transcript does not wake")
        check(turn.question.isEmpty && turn.stage == .wake, "ambient text is excluded from questions")
        check(turn.transcript("Hey Jarvis what time is it", now: 2) == .woke, "exact phrase wakes")
        check(turn.question == "what time is it", "wake prefix is excluded from requests")
        turn.level(0.6, now: 2.8)
        check(turn.poll(now: 3.1) == .none, "voice energy prevents premature end")
        check(turn.poll(now: 4) == .finish, "silence ends a nonempty command")
        check(turn.poll(now: 50) == .none, "one turn finishes only once")
        _ = turn.transcript("Hey Jarvis what time is it now", now: 4.1)
        check(turn.question == "what time is it now", "post-roll can preserve final words")

        var empty = HandsFreeTurn(now: 0)
        _ = empty.transcript("Hey Jarvis", now: 1)
        check(empty.poll(now: 8) == .none, "wake alone gets a bounded chance to speak")
        check(empty.poll(now: 9) == .expired && empty.question.isEmpty, "empty command expires without submitting")
        var revised = HandsFreeTurn(now: 0)
        _ = revised.transcript("Hey Jarvis read notes", now: 1)
        _ = revised.transcript("Actually ambient talk", now: 1.2)
        check(revised.question.isEmpty, "recognizer correction removing wake prevents submission")
        var bounded = HandsFreeTurn(now: 0)
        _ = bounded.transcript("Hey Jarvis take a note", now: 0)
        bounded.level(1, now: 20)
        check(bounded.poll(now: 20) == .finish, "command duration remains bounded during noise")
        var armed = HandsFreeTurn(now: 0)
        check(armed.poll(now: 30) == .restart, "wake capture periodically restarts bounded transcript history")

        var followup = HandsFreeTurn(followup: true, now: 0)
        check(followup.poll(now: 5.9) == .none, "follow-up window is available without a new wake phrase")
        check(followup.poll(now: 6) == .expired, "empty follow-up ends at six seconds")
        var spokenFollowup = HandsFreeTurn(followup: true, now: 0)
        _ = spokenFollowup.transcript("and tomorrow?", now: 5)
        check(spokenFollowup.stage == .command && spokenFollowup.question == "and tomorrow?", "follow-up starts a real command")
        check(spokenFollowup.poll(now: 5.5) == .none, "started follow-up has time to finish")
        check(spokenFollowup.poll(now: 6.2) == .finish, "follow-up uses the same silence detector")

        var order: [String] = []
        let startup = Task { @MainActor in
            order.append("startup")
            do { try await Task.sleep(for: .seconds(10)) } catch {}
            order.append("released")
        }
        await Task.yield()
        let handoff = HandsFreeHandoff.cancelAndWait(startup) { order.append("ptt") }
        await handoff.value
        check(order == ["startup", "released", "ptt"], "PTT waits for cancelled startup ownership to release")
        let immediate = HandsFreeHandoff.cancelAndWait(nil) { order.append("idle") }
        await immediate.value
        check(order.last == "idle", "handoff without a pending startup completes")
        let analyzer = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(20))
            order.append("analyzer stopped")
        }
        let barrier = HandsFreeHandoff.join([startup, analyzer])
        let next = HandsFreeHandoff.cancelAndWait(barrier) { order.append("next recognizer") }
        await next.value
        check(order.suffix(2) == ["analyzer stopped", "next recognizer"], "next recognizer waits for analyzer teardown")
        weak var retained: NSObject?
        var retainingTask: Task<Void, Never>?
        do {
            let lease = NSObject()
            retained = lease
            let cleanup = Task<Void, Never> { try? await Task.sleep(for: .milliseconds(20)) }
            retainingTask = HandsFreeHandoff.join([cleanup], retaining: lease)
        }
        check(retained != nil, "cleanup barrier retains the ownership lease")
        await retainingTask?.value
        check(retained == nil, "ownership lease releases when cleanup settles")
        print("Hands-free policy and handoff checks: \(passed) passed")
    }
}
