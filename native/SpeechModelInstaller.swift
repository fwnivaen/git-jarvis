import AppKit
import Speech

final class SpeechModelInstaller {
    private var progressTimer: Timer?
    private var installing = false

    func install(island: IslandController) {
        guard !installing else { return }
        installing = true
        Task { @MainActor in
            defer {
                progressTimer?.invalidate()
                progressTimer = nil
                installing = false
            }
            guard SpeechTranscriber.isAvailable else {
                island.show(.error, title: "Enhanced speech unavailable")
                return
            }
            let transcriber = SpeechTranscriber(locale: Locale(identifier: "en-US"),
                                                preset: .progressiveTranscription)
            let status = await AssetInventory.status(forModules: [transcriber])
            if status == .installed {
                island.notice("Enhanced English speech is ready")
                return
            }
            guard status != .unsupported else {
                island.show(.error, title: "English speech model unsupported")
                return
            }
            do {
                guard let request = try await AssetInventory.assetInstallationRequest(
                    supporting: [transcriber]) else {
                    island.notice("Speech model installation is already managed by macOS")
                    return
                }
                if island.state.activePhase == nil {
                    island.begin(.modelDownload, title: "Preparing speech model")
                }
                progressTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
                    guard island.state.activePhase == .modelDownload else { return }
                    let progress = request.progress
                    if progress.isIndeterminate {
                        island.update(title: "Downloading speech model")
                    } else {
                        let percent = Int(max(0, min(100, progress.fractionCompleted * 100)))
                        island.update(title: "Downloading speech model", detail: "\(percent)%")
                    }
                }
                try await request.downloadAndInstall()
                if island.state.activePhase == .modelDownload { island.end(.status, text: "") }
                island.notice("Enhanced English speech is ready")
            } catch {
                if island.state.activePhase == .modelDownload {
                    island.show(.error, title: "Speech model download failed",
                                detail: "Try again from the Jarvis menu")
                } else {
                    island.notice("Speech model download failed")
                }
            }
        }
    }
}
