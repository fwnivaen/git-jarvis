import AppKit
import Foundation

enum ReadySound {
    static let sampleRate = 44_100
    static let firstDuration = 0.040
    static let secondDuration = 0.050
    static let firstFrequency = 1_046.50 // C6
    static let secondFrequency = 1_567.98 // G6
    static let amplitude = 0.22
    static let fadeDuration = 0.005

    static func data() -> Data {
        let count = Int(Double(sampleRate) * (firstDuration + secondDuration))
        var pcm = Data(capacity: count * 2)
        for index in 0..<count {
            let time = Double(index) / Double(sampleRate)
            let frequency = time < firstDuration ? firstFrequency : secondFrequency
            let phaseTime = time < firstDuration ? time : time - firstDuration
            let duration = time < firstDuration ? firstDuration : secondDuration
            let envelope = min(1, phaseTime / fadeDuration, (duration - phaseTime) / fadeDuration)
            let value = sin(2 * .pi * frequency * time) * amplitude * max(0, envelope)
            var sample = Int16(max(-32767, min(32767, Int(value * 32767)))).littleEndian
            withUnsafeBytes(of: &sample) { pcm.append(contentsOf: $0) }
        }
        var wave = Data()
        wave.append(contentsOf: Array("RIFF".utf8))
        wave.appendLittleEndian(UInt32(36 + pcm.count))
        wave.append(contentsOf: Array("WAVEfmt ".utf8))
        wave.appendLittleEndian(UInt32(16))
        wave.appendLittleEndian(UInt16(1))
        wave.appendLittleEndian(UInt16(1))
        wave.appendLittleEndian(UInt32(sampleRate))
        wave.appendLittleEndian(UInt32(sampleRate * 2))
        wave.appendLittleEndian(UInt16(2))
        wave.appendLittleEndian(UInt16(16))
        wave.append(contentsOf: Array("data".utf8))
        wave.appendLittleEndian(UInt32(pcm.count))
        wave.append(pcm)
        return wave
    }

    static func play() {
        NSSound(data: data())?.play()
    }
}

private extension Data {
    mutating func appendLittleEndian<T: FixedWidthInteger>(_ value: T) {
        var number = value.littleEndian
        Swift.withUnsafeBytes(of: &number) { append(contentsOf: $0) }
    }
}
