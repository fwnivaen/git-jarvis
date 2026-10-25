import Darwin
import Foundation

final class VoiceLock {
    private let descriptor: Int32

    init?() {
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("jarvis-voice-owner-\(getuid()).lock").path
        let fd = open(path, O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
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
