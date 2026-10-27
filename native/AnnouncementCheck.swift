import AppKit

@main
enum AnnouncementCheck {
    static func main() {
        var passed = 0
        func check(_ condition: Bool, _ message: String) {
            precondition(condition, message)
            passed += 1
        }
        func display(_ phase: IslandPhase, title: String = "private question",
                     detail: String = "private answer", word: Int? = nil) -> IslandDisplay {
            IslandDisplay(phase: phase, title: title, detail: detail, wordIndex: word,
                          microphoneLevel: 0.5)
        }
        var announcements = IslandAnnouncements()
        check(announcements.next(for: display(.listening))?.text == "Jarvis is listening.",
              "listening status is sanitized")
        check(announcements.next(for: display(.listening, title: "different transcript")) == nil,
              "streaming transcript is not announced")
        check(announcements.next(for: display(.speaking, word: 1))?.text == "Jarvis is speaking.",
              "answer body is not announced")
        check(announcements.next(for: display(.speaking, word: 7)) == nil,
              "word ranges are not announced")
        check(announcements.next(for: display(.modelDownload, detail: "10%")) != nil,
              "download start is announced")
        check(announcements.next(for: display(.modelDownload, detail: "40%")) == nil,
              "download progress is not repeated")
        check(announcements.next(for: display(.error))?.text == "Jarvis needs attention.",
              "error details are not announced")
        check(announcements.next(for: display(.error)) == nil, "same error is deduplicated")
        check(announcements.next(for: display(.approval))?.priority == .high,
              "approval is prioritized")
        check(announcements.next(for: display(.notification, detail: "Replies muted"))?.text
              == "Jarvis replies muted.", "mute status is announced")
        check(announcements.next(for: display(.notification, detail: "new private notification"))?.text
              == "Jarvis has an update.", "new notification is sanitized")
        check(announcements.next(for: display(.hidden)) == nil, "hidden produces no speech")
        check(announcements.next(for: display(.notification, detail: "new private notification")) != nil,
              "new visibility allows an announcement")
        let append = IslandTextParts.changed(from: "Hello", to: "Hello Sir")
        check(append.stable == "Hello" && append.incoming == " Sir", "new words preserve settled text")
        let correction = IslandTextParts.changed(from: "Hello worl", to: "Hello world")
        check(correction.stable == "Hello " && correction.incoming == "world", "a corrected word arrives together")
        let deletion = IslandTextParts.changed(from: "Hello Sir", to: "Hello")
        check(deletion.incoming.isEmpty, "deleting words does not animate an arrival")
        let samples = ["", "Café", "Café 👩🏽‍💻", "Café 👩🏽‍💻 résumé", "Sir, ready."]
        for before in samples {
            for after in samples {
                let parts = IslandTextParts.changed(from: before, to: after)
                check(parts.stable + parts.incoming == after, "word splitting preserves Unicode text")
            }
        }
        print("Island announcement checks: \(passed) passed")
    }
}
