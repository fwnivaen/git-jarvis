import Foundation

@main
enum IslandCheck {
    static func main() {
        let start = Date(timeIntervalSince1970: 1_000)
        var checks = 0
        func check(_ condition: @autoclosure () -> Bool, _ message: String) {
            precondition(condition(), message)
            checks += 1
        }

        for ready in [false, true] {
            for phase in IslandPhase.allCases {
                var state = IslandState()
                state.readyEnabled = ready
                state.enqueueNotice("Finished a background task", at: start)
                state.setApproval("Review requested")
                if [.waking, .listening, .thinking, .working, .speaking,
                    .mutedReply, .modelDownload].contains(phase) {
                    state.begin(phase, title: "Active")
                    check(state.resolve(at: start).phase == phase, "active session priority")
                    state.end(.status, text: "Status", at: start)
                } else if [.releasedEarly, .offline, .permissionDenied,
                           .microphoneFailure, .error].contains(phase) {
                    state.show(phase, title: "Problem", at: start)
                } else if phase == .done {
                    state.end(.answer, text: "Answer", at: start)
                }
                check(state.resolve(at: start).phase == .approval, "approval priority")
                state.setApproval(nil)
                let display = state.resolve(at: start)
                let expected = phase == .done || [.releasedEarly, .offline, .permissionDenied,
                    .microphoneFailure, .error].contains(phase) ? phase : .notification
                check(display.phase == expected, "transient or queued notice")
                check(state.resolve(at: start.addingTimeInterval(31)).phase == (ready ? .ready : .hidden),
                      "expiry and ready priority")
            }
        }

        var state = IslandState()
        state.end(.failure, text: "No answer", at: start)
        check(state.resolve(at: start).phase == .error, "failure cannot become Done")
        state.end(.status, text: "Still working", at: start)
        check(state.resolve(at: start).phase == .hidden, "status cannot become Done")
        state.begin(.listening, title: "Listening")
        state.update(microphoneLevel: 12)
        check(state.resolve(at: start).microphoneLevel == 1, "meter clamp")
        check(!state.resolve(at: start).isExpanded, "listening begins compact")
        state.transcript("", at: start)
        check(!state.resolve(at: start.addingTimeInterval(1)).isExpanded,
              "silence does not expand listening")
        state.transcript("Jarvis", at: start)
        check(!state.resolve(at: start.addingTimeInterval(0.29)).isExpanded,
              "first words wait for expansion delay")
        state.transcript("Jarvis test", at: start.addingTimeInterval(0.2))
        check(state.resolve(at: start.addingTimeInterval(0.31)).isExpanded,
              "later words do not reset expansion delay")
        state.begin(.listening, title: "Listening")
        check(!state.resolve(at: start.addingTimeInterval(2)).isExpanded,
              "new listening turn resets expansion")

        let phrase = "Hello 👋🏽 café world"
        let source = phrase as NSString
        let emoji = source.range(of: "👋🏽")
        let accent = source.range(of: "café")
        check(SpokenWords.index(for: emoji, in: phrase) == 1, "emoji UTF-16 range")
        check(SpokenWords.index(for: accent, in: phrase) == 2, "accent UTF-16 range")
        check(SpokenWords.index(for: NSRange(location: 50, length: 1), in: phrase) == nil,
              "out-of-bounds speech range")
        check(SpokenWords.centeredText(phrase, activeWord: 2, radius: 1) == "… 👋🏽 café world",
              "centered speech text")

        let activePhases: [IslandPhase] = [
            .waking, .listening, .thinking, .working, .speaking, .mutedReply, .modelDownload
        ]
        let errorPhases: [IslandPhase] = [
            .releasedEarly, .offline, .permissionDenied, .microphoneFailure, .error
        ]
        for first in activePhases {
            for second in activePhases {
                var pair = IslandState()
                pair.begin(first, title: "First")
                pair.begin(second, title: "Second")
                check(pair.resolve(at: start).phase == second, "new active phase replaces old phase")
                check(pair.resolve(at: start).title == "Second", "new active title replaces old title")
            }
            for failure in errorPhases {
                var pair = IslandState()
                pair.begin(first, title: "Active")
                pair.show(failure, title: "Failed", at: start)
                check(pair.resolve(at: start).phase == failure, "error terminates active phase")
                check(pair.resolve(at: start).title == "Failed", "error title replaces active title")
            }
        }

        var queued = IslandState()
        queued.begin(.thinking, title: "Working")
        queued.enqueueNotice("First", at: start)
        queued.enqueueNotice("Second", at: start)
        check(queued.resolve(at: start).phase == .thinking, "notice waits for active session")
        queued.end(.status, text: "Status", at: start)
        check(queued.resolve(at: start).detail == "First", "oldest notice appears first")
        check(queued.resolve(at: start.addingTimeInterval(4.1)).detail == "Second", "second notice follows")
        check(queued.resolve(at: start.addingTimeInterval(31)).phase == .hidden,
              "expired notice does not reappear")

        var work = IslandState()
        work.setBackgroundWork(true)
        check(work.resolve(at: start).phase == .working, "verified background work is active")
        work.setApproval("Review")
        check(work.resolve(at: start).phase == .approval, "approval interrupts passive work")
        work.begin(.listening, title: "Listening")
        check(work.resolve(at: start).phase == .listening, "voice session takes priority over work")
        work.end(.status, text: "", at: start)
        check(work.resolve(at: start).phase == .approval, "approval reappears after voice")
        work.setApproval(nil)
        check(work.resolve(at: start).phase == .working, "work reappears after approval")
        work.show(.error, title: "Failed", at: start)
        check(work.resolve(at: start).phase == .error, "voice error interrupts passive work")
        check(work.resolve(at: start.addingTimeInterval(4.1)).phase == .working,
              "passive work resumes after error")
        work.setBackgroundWork(false)
        check(work.resolve(at: start.addingTimeInterval(4.1)).phase == .hidden,
              "idle follows finished background work")

        print("Island state checks: \(checks) passed")
    }
}
