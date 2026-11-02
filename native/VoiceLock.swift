import AVFoundation
import AudioToolbox
import Darwin
import Foundation

// MARK: - Shared AVFoundation helpers
//
// These call sites were written against API names that do not exist in
// AVFoundation (`withAudioUnit`, `installAudioTap`, `AVAudioPCMBuffer(copying:)`).
// Rather than rewrite every call site, this file (already linked into both
// the voice helper and the desktop app builds) backfills those names on top
// of the real APIs: `AVAudioIONode.audioUnit`, `AVAudioNode.installTap`, and
// a manual deep copy of an `AVAudioPCMBuffer`.

extension AVAudioIONode {
    func jarvisWithAudioUnit<T>(_ body: (AudioUnit?) -> T) -> T {
        body(audioUnit)
    }
}

extension AVAudioNode {
    // Named distinctly (not `installTap`/`installAudioTap`) to avoid ambiguous
    // overload resolution against the SDK's own deprecated/renamed variants.
    func jarvisInstallTap(onBus bus: AVAudioNodeBus, bufferSize: AVAudioFrameCount, format: AVAudioFormat?, block: @escaping AVAudioNodeTapBlock) throws {
        installTap(onBus: bus, bufferSize: bufferSize, format: format, block: block)
    }
}

extension AVAudioPCMBuffer {
    // Named `deepCopy` rather than `init(copying:)` to avoid colliding with
    // the SDK's own `init(copying: AVReadOnlyAudioPCMBuffer)` on newer toolchains.
    static func deepCopy(of buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameCapacity)!
        copy.frameLength = buffer.frameLength
        let channels = Int(buffer.format.channelCount)
        let frames = Int(buffer.frameLength)
        if let src = buffer.floatChannelData, let dst = copy.floatChannelData {
            for channel in 0..<channels { dst[channel].update(from: src[channel], count: frames) }
        } else if let src = buffer.int16ChannelData, let dst = copy.int16ChannelData {
            for channel in 0..<channels { dst[channel].update(from: src[channel], count: frames) }
        } else if let src = buffer.int32ChannelData, let dst = copy.int32ChannelData {
            for channel in 0..<channels { dst[channel].update(from: src[channel], count: frames) }
        }
        return copy
    }
}

final class VoiceLock {
    // Use a stable per-user path across launch environments with different TMPDIR.
    static let defaultPath = "/tmp/jarvis-voice-owner-\(getuid()).lock"
    private let descriptor: Int32

    init?(path: String = VoiceLock.defaultPath) {
        let fd = open(path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { return nil }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFREG,
              fchmod(fd, S_IRUSR | S_IWUSR) == 0,
              flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            close(fd)
            return nil
        }
        descriptor = fd
    }

    deinit {
        flock(descriptor, LOCK_UN)
        close(descriptor)
    }
}
