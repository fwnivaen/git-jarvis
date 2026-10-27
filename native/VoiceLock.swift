import Darwin
import Foundation

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
