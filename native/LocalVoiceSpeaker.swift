import AVFoundation

let WORD_PROGRESS_POLL_S = 0.08
let STOP_FADE_S = 0.06
let PLAYBACK_STALL_S = 20.0
let ACKNOWLEDGEMENT_MAX_S = 2.5
let ACKNOWLEDGEMENT_POLL_S = 0.04
let WORD_PROGRESS_MAX_CHARS_PER_S = 45.0
let ACKNOWLEDGEMENTS: [String] = []

@MainActor
final class Speaker: NSObject {
    private let client: LocalSpeechClient
    private var engine: AVAudioEngine?
    private var node: AVAudioPlayerNode?
    private var format: AVAudioFormat?
    private var streaming: Task<Void, Error>?
    private var progress: Task<Void, Never>?
    private var finished: CheckedContinuation<Void, Never>?
    private var configObserver: NSObjectProtocol?
    private var requestID: String?
    private var samples: [Float] = []       // everything received, so a device change can resume in place
    private var playedBase = 0               // frames played before the current node run
    private var scheduledFrom = 0            // first frame given to the current node run
    private var pendingBuffers = 0
    private var streamComplete = false
    private var estimate: Double?
    private var anchors: [SpokenAnchor] = []
    private var anchorsComplete = false
    private(set) var playing = false
    private(set) var preparingAudio = false
    private(set) var completed = false
    var onPreparing: ((Bool) -> Void)?
    var onPlayback: ((Bool) -> Void)?
    var onFailure: ((String) -> Void)?
    var onWord: ((NSRange) -> Void)? // Estimated visual progress; not forced word alignment.

    init(base: URL) {
        client = LocalSpeechClient(base: base)
        super.init()
    }

    /// Start loading the voice model while the user speaks; a no-op once it is resident.
    func warm() { Task { [client] in await client.warm(prepare: ACKNOWLEDGEMENTS) } }

    private var acknowledgement: AVAudioPlayer?
    private var ackEpoch = 0                // bumped by stop() and say(): a pending acknowledgement is dropped
    private var deferredStart: Task<Void, Never>?
    /// A short pre-rendered acknowledgement while the AI thinks. Plays only if already
    /// cached (instant); otherwise it is skipped and rendered later in idle time.
    /// `wanted` is the caller's ownership check, re-read after the fetch: Stop, an answer or a
    /// newer request meanwhile means it never becomes audible.
    func acknowledge(_ text: String, wanted: @MainActor () -> Bool = { true }) async {
        let epoch = ackEpoch
        guard requestID == nil, wanted(), let data = await client.cachedAudio(text),
              requestID == nil, epoch == ackEpoch, wanted(),
              let player = try? AVAudioPlayer(data: data) else { return }
        acknowledgement = player
        if player.play() { print("Speech acknowledgement: playing") }
    }

    func say(_ text: String, delivery: [String: Any]? = nil) async {
        // Synthesis starts at once; only the first audio waits for a playing acknowledgement
        // to end (never cut mid-word), so the acknowledgement never delays rendering.
        stop(keepAcknowledgement: true)
        guard !Task.isCancelled else { return }
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        requestID = id
        completed = false
        samples = []; playedBase = 0; lastHeardFrames = 0; scheduledFrom = 0; pendingBuffers = 0; streamComplete = false; estimate = nil
        anchors = []; anchorsComplete = false; failAfterDrain = false
        preparingAudio = true; onPreparing?(true)
        print("Speech playback: preparing")
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard requestID == id, !Task.isCancelled else { continuation.resume(); return }
                finished = continuation
                streaming = Task { [weak self, client] in
                    do {
                        try await client.stream(text, requestID: id, delivery: delivery) { part, estimate, complete in
                            guard let self, self.requestID == id else { throw CancellationError() }
                            try self.append(part, estimate: estimate, complete: complete, id: id, text: text)
                        } onTimeline: { [weak self] anchors, complete in
                            guard let self, self.requestID == id else { return }
                            self.anchors = anchors; self.anchorsComplete = complete
                        }
                    } catch {
                        guard let self, self.requestID == id else { return }
                        if !(error is CancellationError) {
                            Task { [client = self.client] in await client.cancel(id) }
                            // Audio already received keeps playing to its end; the rest of
                            // the reply then continues as text.
                            if self.engine != nil, self.playedFrames() < self.samples.count {
                                self.failAfterDrain = true; self.streamComplete = true
                                self.finishIfDrained(id); return
                            }
                            print("Speech playback: failed")
                            self.onFailure?(LOCAL_VOICE_ERROR)
                        }
                        Task { [client] in await client.cancel(id) }
                        self.complete(id)
                    }
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in if self?.requestID == id { self?.stop() } }
        }
    }

    private func append(_ part: [Float], estimate: Double?, complete: Bool, id: String, text: String) throws {
        if let estimate { self.estimate = estimate }
        streamComplete = complete
        guard !part.isEmpty else { if complete { finishIfDrained(id) }; return }
        samples += part
        if engine != nil { schedule(part, id: id); return }
        guard acknowledgement?.isPlaying == true else { try startEngine(id: id, text: text); schedule(samples[...], id: id); return }
        guard deferredStart == nil else { return }       // samples keep accumulating until it starts
        let waitUntil = Date().addingTimeInterval(ACKNOWLEDGEMENT_MAX_S)
        deferredStart = Task { [weak self] in
            while let self, self.requestID == id, self.acknowledgement?.isPlaying == true, Date() < waitUntil {
                try? await Task.sleep(nanoseconds: UInt64(ACKNOWLEDGEMENT_POLL_S * 1e9))
            }
            guard let self, self.requestID == id, self.engine == nil else { return }
            self.deferredStart = nil
            do { try self.startEngine(id: id, text: text); self.schedule(self.samples[...], id: id) }
            catch {
                print("Speech playback: failed"); self.onFailure?(LOCAL_VOICE_ERROR)
                Task { [client = self.client] in await client.cancel(id) }; self.complete(id)
            }
        }
    }

    private func startEngine(id: String, text: String) throws {
        let e = AVAudioEngine(), n = AVAudioPlayerNode()
        guard let f = AVAudioFormat(standardFormatWithSampleRate: Double(LOCAL_VOICE_RATE), channels: 1) else { throw LocalVoiceError.unavailable }
        e.attach(n); e.connect(n, to: e.mainMixerNode, format: f)
        e.prepare()
        try e.start()
        engine = e; node = n; format = f
        n.play()
        // A new output device (headphones, display audio) stops the engine: resume from the heard position.
        configObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: e, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.resumeAfterDeviceChange(id: id) }
        }
        // Both flags flip before either callback, so no render sees "not preparing, not playing".
        // Playback is reported below once the engine has rendered frames.
        // Word progress follows the audio: pause-anchored timing from the voice service, minus
        // the output device's latency (Bluetooth adds a few hundred ms). It is an alignment to
        // real pauses, not per-word forced alignment, and it holds rather than running ahead.
        let length = Double(max(1, (text as NSString).length))
        let words = ((try? NSRegularExpression(pattern: "\\S+"))?.matches(
            in: text, range: NSRange(location: 0, length: (text as NSString).length)) ?? []).map(\.range)
        var shown = 0.0
        var stalledSince: Date?
        progress = Task { [weak self] in
            while let self, self.requestID == id, !Task.isCancelled {
                if !self.playing, self.playedFrames() > 0 {
                    self.preparingAudio = false; self.playing = true
                    self.onPreparing?(false); self.onPlayback?(true)
                    print("Speech playback: playing")
                }
                let latency = self.engine?.outputNode.presentationLatency ?? 0
                let heard = Double(self.playedFrames()) / Double(LOCAL_VOICE_RATE) - latency
                let target = spokenFraction(self.anchors, at: heard, complete: self.anchorsComplete && self.streamComplete)
                // Never backwards, and a timeline correction is eased in rather than flashed ahead.
                shown = max(shown, min(target, shown + WORD_PROGRESS_MAX_CHARS_PER_S * WORD_PROGRESS_POLL_S / length))
                let fraction = shown
                if let last = words.lastIndex(where: { Double($0.location) / length < fraction }) { self.onWord?(words[last]) }
                // Safety net: everything received has played and nothing new arrives.
                if self.playedFrames() >= self.samples.count {
                    stalledSince = stalledSince ?? Date()
                    if Date().timeIntervalSince(stalledSince!) > PLAYBACK_STALL_S {
                        print("Speech playback: failed (stalled)")
                        self.onFailure?(LOCAL_VOICE_ERROR)
                        Task { [client = self.client] in await client.cancel(id) }
                        self.complete(id); return
                    }
                } else { stalledSince = nil }
                try? await Task.sleep(nanoseconds: UInt64(WORD_PROGRESS_POLL_S * 1e9))
            }
        }
    }

    private func schedule(_ part: ArraySlice<Float>, id: String) {
        guard let node, let format, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(part.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(part.count)
        part.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: part.count) }
        pendingBuffers += 1
        let owner = node
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.node === owner else { return }
                self.pendingBuffers -= 1
                self.finishIfDrained(id)
            }
        }
    }
    private func schedule(_ part: [Float], id: String) { schedule(part[...], id: id) }

    private var lastHeardFrames = 0          // last position the engine reported (it reports none once stopped)
    private func playedFrames() -> Int {
        guard let node, let t = node.lastRenderTime, let p = node.playerTime(forNodeTime: t) else { return max(playedBase, lastHeardFrames) }
        lastHeardFrames = max(lastHeardFrames, playedBase + max(0, Int(p.sampleTime)))
        return lastHeardFrames
    }

    private func resumeAfterDeviceChange(id: String) {
        guard requestID == id, let engine, let node else { return }
        let heard = min(samples.count, playedFrames())
        node.stop()                      // drops the old run's callbacks via the owner check
        pendingBuffers = 0
        playedBase = heard; scheduledFrom = heard
        do { try engine.start() } catch {
            print("Speech playback: failed"); onFailure?(LOCAL_VOICE_ERROR)
            Task { [client] in await client.cancel(id) }; complete(id); return
        }
        let replacement = AVAudioPlayerNode()
        engine.attach(replacement); engine.connect(replacement, to: engine.mainMixerNode, format: format)
        engine.detach(node)
        self.node = replacement
        replacement.play()
        if heard < samples.count { schedule(samples[heard...], id: id) } else { finishIfDrained(id) }
    }

    /// Diagnostic: deliver the notification macOS sends when the output device changes.
    func simulateDeviceChange() {
        guard let engine else { return }
        engine.stop()                          // macOS stops the engine before notifying
        NotificationCenter.default.post(name: .AVAudioEngineConfigurationChange, object: engine)
    }
    var heardSeconds: Double { Double(playedFrames()) / Double(LOCAL_VOICE_RATE) }

    private var failAfterDrain = false
    private func finishIfDrained(_ id: String) {
        // Audio waiting behind an acknowledgement has not played yet: not drained.
        guard requestID == id, streamComplete, pendingBuffers == 0, engine != nil || samples.isEmpty else { return }
        if failAfterDrain {
            print("Speech playback: failed")
            onFailure?(LOCAL_VOICE_ERROR); complete(id); return
        }
        completed = true
        print("Speech playback: completed")
        complete(id)
    }

    func stop(keepAcknowledgement: Bool = false) {
        ackEpoch += 1
        if !keepAcknowledgement { acknowledgement?.stop(); acknowledgement = nil }
        deferredStart?.cancel(); deferredStart = nil
        let id = requestID
        requestID = nil
        streaming?.cancel(); streaming = nil
        teardown()
        if preparingAudio { preparingAudio = false; onPreparing?(false) }
        if playing { playing = false; onPlayback?(false) }
        let continuation = finished; finished = nil; continuation?.resume()
        if let id { Task { [client] in await client.cancel(id) } }
    }

    private func teardown() {
        progress?.cancel(); progress = nil
        if let configObserver { NotificationCenter.default.removeObserver(configObserver) }
        configObserver = nil
        // Interrupted mid-word: a short fade instead of a click. The reply is already
        // detached (no callbacks or progress reach it), so nothing can play after Stop.
        if let node, let engine, node.isPlaying, playing {
            let mixer = engine.mainMixerNode, steps = 6
            Task { @MainActor in
                for step in 1...steps {
                    mixer.outputVolume = Float(steps - step) / Float(steps)
                    try? await Task.sleep(nanoseconds: UInt64(STOP_FADE_S / Double(steps) * 1e9))
                }
                node.stop(); engine.stop()
            }
        } else {
            node?.stop(); engine?.stop()
        }
        node = nil; engine = nil; format = nil
        samples = []
    }

    private func complete(_ id: String) {
        guard requestID == id else { return }
        requestID = nil; streaming = nil
        deferredStart?.cancel(); deferredStart = nil
        teardown()
        if preparingAudio { preparingAudio = false; onPreparing?(false) }
        if playing { playing = false; onPlayback?(false) }
        let continuation = finished; finished = nil; continuation?.resume()
    }
}
