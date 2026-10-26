import AVFoundation
import Foundation

private final class MicrophoneSampleSummary {
    private let lock = NSLock()
    private var firstBufferAt: TimeInterval?
    private var count = 0
    private var peakLevel = 0.0
    private let meter = MicrophoneMeter()

    func accept(_ buffer: AVReadOnlyAudioPCMBuffer) {
        let at = ProcessInfo.processInfo.systemUptime
        let level = meter.measure(buffer)
        lock.lock()
        if firstBufferAt == nil { firstBufferAt = at }
        count += 1
        peakLevel = max(peakLevel, level)
        lock.unlock()
    }

    func result(startedAt: TimeInterval) -> (milliseconds: Int, buffers: Int, peak: Double)? {
        lock.lock()
        defer { lock.unlock() }
        guard let firstBufferAt else { return nil }
        return (Int((firstBufferAt - startedAt) * 1000), count, peakLevel)
    }
}

enum MicrophoneCheck {
    static let captureSeconds = 2.0
    static let betweenRunsSeconds = 0.5

    static func run() -> Int32 {
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            fputs("Microphone check needs existing microphone permission\n", stderr)
            return 1
        }
        guard let voiceLock = VoiceLock() else {
            fputs("Another Jarvis helper owns the microphone\n", stderr)
            return 1
        }
        defer { withExtendedLifetime(voiceLock) {} }
        for label in ["first", "subsequent"] {
            if label == "subsequent" { Thread.sleep(forTimeInterval: betweenRunsSeconds) }
            do {
                let engine = AVAudioEngine()
                let input = engine.inputNode
                let format = input.outputFormat(forBus: 0)
                guard format.sampleRate > 0, format.channelCount > 0 else {
                    fputs("No microphone input format available\n", stderr)
                    return 1
                }
                let summary = MicrophoneSampleSummary()
                try input.installAudioTap(onBus: 0, bufferSize: AVAudioFrameCount(Conversation.meterSamples),
                                          format: format) { buffer, _ in summary.accept(buffer) }
                let startedAt = ProcessInfo.processInfo.systemUptime
                do {
                    try engine.start()
                } catch {
                    input.removeTap(onBus: 0)
                    throw error
                }
                Thread.sleep(forTimeInterval: captureSeconds)
                input.removeTap(onBus: 0)
                engine.stop()
                guard let result = summary.result(startedAt: startedAt), result.buffers > 0 else {
                    fputs("Microphone produced no audio buffers\n", stderr)
                    return 1
                }
                print("\(label): first buffer \(result.milliseconds) ms; \(result.buffers) buffers; peak meter \(String(format: "%.2f", result.peak))")
            } catch {
                fputs("Microphone check could not start audio input\n", stderr)
                return 1
            }
        }
        return 0
    }
}
