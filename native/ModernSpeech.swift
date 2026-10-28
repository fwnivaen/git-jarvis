import AppKit
import AVFoundation
import Speech

private final class TranscriptAccumulator {
    private let lock = NSLock()
    private var finalized = ""
    private var volatile = ""
    private let maximumCharacters: Int?

    init(maximumCharacters: Int? = nil) { self.maximumCharacters = maximumCharacters.map { max(1, $0) } }

    private func bounded(_ value: String) -> String {
        guard let maximumCharacters else { return value }
        return String(value.suffix(maximumCharacters))
    }

    private func joined(_ prefix: String, _ suffix: String) -> String {
        guard !prefix.isEmpty, !suffix.isEmpty else { return prefix + suffix }
        if prefix.last?.isWhitespace == true || suffix.first?.isWhitespace == true ||
            suffix.first?.isPunctuation == true { return prefix + suffix }
        return prefix + " " + suffix
    }

    func accept(_ text: String, final: Bool) -> String {
        lock.lock()
        defer { lock.unlock() }
        if final {
            finalized = bounded(joined(finalized, text))
            volatile = ""
        } else {
            volatile = bounded(text)
        }
        return bounded(joined(finalized, volatile))
    }

    func current() -> String {
        lock.lock()
        defer { lock.unlock() }
        return bounded(joined(finalized, volatile))
    }
}

// The copy is immutable after the audio callback and is consumed on one queue.
private struct CopiedPCM: @unchecked Sendable { let buffer: AVAudioPCMBuffer }

@available(macOS 27.0, *)
final class ModernCapture: @unchecked Sendable {
    static let locale = Locale(identifier: "en-US")
    static let bufferFrames: AVAudioFrameCount = 1024

    private let engine: AVAudioEngine
    private let analyzer: SpeechAnalyzer
    private let converter: AnalyzerInputConverter
    private let continuation: AsyncStream<AnalyzerInput>.Continuation
    private let conversionQueue = DispatchQueue(label: "jarvis.speech.convert")
    private let resultTask: Task<Void, Never>
    private let transcript: TranscriptAccumulator
    private var tapInstalled = false
    private var finished = false
    private var cancelled = false
    private var cancellationTask: Task<Void, Never>?
    private let onPhrase: (String) -> Void
    private let onLevel: (Double) -> Void
    private let onFinished: (String) -> Void
    private let onError: () -> Void

    static func canUseEnglish() async -> Bool {
        guard SpeechTranscriber.isAvailable else { return false }
        let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
        return await AssetInventory.status(forModules: [transcriber]) == .installed
    }

    static func start(onPhrase: @escaping (String) -> Void,
                      onLevel: @escaping (Double) -> Void,
                      onFinished: @escaping (String) -> Void,
                      onError: @escaping () -> Void,
                      maximumTranscriptCharacters: Int? = nil) async throws -> ModernCapture {
        try Task.checkCancellation()
        let transcriber = SpeechTranscriber(locale: locale,
                                            transcriptionOptions: [],
                                            reportingOptions: [.volatileResults],
                                            attributeOptions: [])
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let converter = try await AnalyzerInputConverter.converter(compatibleWith: [transcriber])
        try Task.checkCancellation()
        let (stream, continuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
        do {
            try await analyzer.start(inputSequence: stream)
            try Task.checkCancellation()
        } catch {
            continuation.finish()
            await analyzer.cancelAndFinishNow()
            throw error
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            await analyzer.cancelAndFinishNow()
            throw NSError(domain: "JarvisSpeech", code: 1)
        }
        let meter = MicrophoneMeter()
        let transcript = TranscriptAccumulator(maximumCharacters: maximumTranscriptCharacters)
        let task = Task {
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    let current = transcript.accept(text, final: result.isFinal)
                    DispatchQueue.main.async { onPhrase(current) }
                }
            } catch {
                DispatchQueue.main.async { onError() }
            }
        }
        let capture = ModernCapture(engine: engine, analyzer: analyzer,
                                    converter: converter, continuation: continuation,
                                    resultTask: task, transcript: transcript,
                                    onPhrase: onPhrase, onLevel: onLevel,
                                    onFinished: onFinished, onError: onError)
        do {
            try Task.checkCancellation()
            try input.installAudioTap(onBus: 0, bufferSize: bufferFrames, format: format) { [weak capture] buffer, time in
                guard let capture else { return }
                let copy = CopiedPCM(buffer: AVAudioPCMBuffer(copying: buffer))
                let level = meter.measure(buffer)
                DispatchQueue.main.async { capture.onLevel(level) }
                capture.conversionQueue.async {
                    do {
                        let inputs = try capture.converter.convert(copy.buffer, at: time)
                        for input in inputs { continuation.yield(input) }
                    } catch {
                        DispatchQueue.main.async { capture.onError() }
                    }
                }
            }
            capture.tapInstalled = true
            try Task.checkCancellation()
            try engine.start()
            try Task.checkCancellation()
            return capture
        } catch {
            await capture.cancel().value
            throw error
        }
    }

    private init(engine: AVAudioEngine, analyzer: SpeechAnalyzer,
                 converter: AnalyzerInputConverter,
                 continuation: AsyncStream<AnalyzerInput>.Continuation,
                 resultTask: Task<Void, Never>,
                 transcript: TranscriptAccumulator,
                 onPhrase: @escaping (String) -> Void,
                 onLevel: @escaping (Double) -> Void,
                 onFinished: @escaping (String) -> Void,
                 onError: @escaping () -> Void) {
        self.engine = engine
        self.analyzer = analyzer
        self.converter = converter
        self.continuation = continuation
        self.resultTask = resultTask
        self.transcript = transcript
        self.onPhrase = onPhrase
        self.onLevel = onLevel
        self.onFinished = onFinished
        self.onError = onError
    }

    func finish() {
        guard !finished else { return }
        finished = true
        stopEngine()
        conversionQueue.async { [weak self] in
            guard let self else { return }
            if let inputs = try? self.converter.flush() {
                for input in inputs { self.continuation.yield(input) }
            }
            self.continuation.finish()
            DispatchQueue.main.async {
                guard !self.cancelled else { return }
                Task {
                    do { try await self.analyzer.finalizeAndFinishThroughEndOfInput() }
                    catch { DispatchQueue.main.async { self.onError() } }
                    await self.resultTask.value
                    let text = self.transcript.current()
                    DispatchQueue.main.async { self.onFinished(text) }
                }
            }
        }
    }

    @discardableResult func cancel() -> Task<Void, Never> {
        if let cancellationTask { return cancellationTask }
        cancelled = true
        if !finished {
            finished = true
            stopEngine()
            continuation.finish()
        }
        resultTask.cancel()
        let task = Task {
            await analyzer.cancelAndFinishNow()
            await resultTask.value
        }
        cancellationTask = task
        return task
    }

    private func stopEngine() {
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        engine.stop()
    }
}
