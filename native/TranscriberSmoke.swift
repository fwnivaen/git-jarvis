import AVFoundation
import Foundation
import Speech

// Explicit local smoke test using the signed app's model reservation.
enum TranscriberSmoke {
    static func checkFile(_ path: String) async -> Bool {
        do {
            let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
            let transcriber = SpeechTranscriber(locale: Locale(identifier: "en-US"),
                                                transcriptionOptions: [], reportingOptions: [],
                                                attributeOptions: [])
            guard await AssetInventory.status(forModules: [transcriber]) == .installed else { return false }
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            let results = Task { () throws -> String in
                var text = ""
                for try await result in transcriber.results {
                    text += " " + String(result.text.characters)
                }
                return text.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            _ = try await analyzer.analyzeSequence(from: file)
            try await analyzer.finalizeAndFinishThroughEndOfInput()
            return try await results.value.localizedCaseInsensitiveContains("test")
        } catch {
            return false
        }
    }
}
