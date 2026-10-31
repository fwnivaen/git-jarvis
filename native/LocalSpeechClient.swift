// Reference-conditioned M1 voice preview. Original Fish profile is preserved separately.
import Foundation

let LOCAL_VOICE_POLL_S = 0.1       // parts are ~1 s of audio; a fast poll keeps the first one prompt
let LOCAL_VOICE_WAIT_S = 260.0
let LOCAL_VOICE_HTTP_S = 15.0
let LOCAL_VOICE_RATE = 44100
let LOCAL_VOICE_ERROR = "Voice stopped · the full reply is shown"

enum LocalVoiceError: Error { case unavailable }

@MainActor
final class LocalSpeechClient {
    private let base: URL
    private let session: URLSession

    init(base: URL, session provided: URLSession? = nil) {
        self.base = base
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = LOCAL_VOICE_HTTP_S
        session = provided ?? URLSession(configuration: config)
    }

    private func send(_ path: String, body: [String: Any]? = nil) async throws -> Data {
        try Task.checkCancellation()
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.timeoutInterval = LOCAL_VOICE_HTTP_S
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse,
              (200..<300).contains(response.statusCode) else { throw LocalVoiceError.unavailable }
        return data
    }

    private func packet(_ data: Data) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw LocalVoiceError.unavailable
        }
        return value
    }

    func render(_ text: String, requestID: String, delivery: [String: Any]? = nil) async throws -> Data {
        var body: [String: Any] = ["text": text, "request_id": requestID]
        if let delivery { body["delivery"] = delivery }
        var value = try packet(await send("/speech", body: body))
        let deadline = Date().addingTimeInterval(LOCAL_VOICE_WAIT_S)
        while value["state"] as? String != "ready" {
            try Task.checkCancellation()
            if value["state"] as? String == "cancelled" { throw CancellationError() }
            guard value["state"] as? String != "failed", Date() < deadline,
                  let path = value["status_url"] as? String,
                  path.range(of: "^/speech/jobs/[a-f0-9]{32}$", options: .regularExpression) != nil
            else { throw LocalVoiceError.unavailable }
            try await Task.sleep(nanoseconds: UInt64(LOCAL_VOICE_POLL_S * 1e9))
            value = try packet(await send(path))
        }
        try Task.checkCancellation()
        guard let path = value["audio_url"] as? String,
              path.range(of: "^/speech/audio/[a-f0-9]{32}\\.wav$", options: .regularExpression) != nil
        else { throw LocalVoiceError.unavailable }
        return try await send(path)
    }

    /// Incremental playback: hands each ordered, finished PCM part to `onPart` as soon as the
    /// server publishes it. A cached reply arrives as one complete part. `estimate` is the server's
    /// spoken-length guess (seconds) until the final part count is known.
    func stream(_ text: String, requestID: String, delivery: [String: Any]? = nil,
                onPart: (_ samples: [Float], _ estimate: Double?, _ complete: Bool) async throws -> Void,
                onTimeline: (_ anchors: [SpokenAnchor], _ complete: Bool) -> Void = { _, _ in }) async throws {
        var body: [String: Any] = ["text": text, "request_id": requestID]
        if let delivery { body["delivery"] = delivery }
        var value = try packet(await send("/speech", body: body))
        let deadline = Date().addingTimeInterval(LOCAL_VOICE_WAIT_S)
        var next = 0, announcedEnd = false
        while true {
            try Task.checkCancellation()
            let state = value["state"] as? String
            if state == "cancelled" { throw CancellationError() }
            guard state != "failed", Date() < deadline else { throw LocalVoiceError.unavailable }
            let parts = value["parts"] as? Int ?? 0
            let complete = state == "ready"
            if let anchors = spokenAnchors(value["timeline"]) { onTimeline(anchors, value["timeline_complete"] as? Bool ?? false) }
            if parts > 0 {
                guard let prefix = value["part_url"] as? String,
                      prefix.range(of: "^/speech/audio/[a-f0-9]{32}/$", options: .regularExpression) != nil
                else { throw LocalVoiceError.unavailable }
                while next < parts {
                    guard let samples = pcm16Samples(try await send(prefix + "\(next).wav")) else { throw LocalVoiceError.unavailable }
                    next += 1
                    announcedEnd = complete && next == parts
                    try await onPart(samples, value["estimated_seconds"] as? Double, announcedEnd)
                }
                if complete {
                    // Every part may already have arrived while the reply was still
                    // streaming: the end must still be announced, or playback never finishes.
                    if !announcedEnd { try await onPart([], value["estimated_seconds"] as? Double, true) }
                    return
                }
            } else if complete {
                // A cached reply: the complete file is the only part.
                guard let path = value["audio_url"] as? String,
                      path.range(of: "^/speech/audio/[a-f0-9]{32}\\.wav$", options: .regularExpression) != nil,
                      let samples = pcm16Samples(try await send(path))
                else { throw LocalVoiceError.unavailable }
                try await onPart(samples, nil, true)
                return
            }
            guard let path = value["status_url"] as? String,
                  path.range(of: "^/speech/jobs/[a-f0-9]{32}$", options: .regularExpression) != nil
            else { throw LocalVoiceError.unavailable }
            try await Task.sleep(nanoseconds: UInt64(LOCAL_VOICE_POLL_S * 1e9))
            value = try packet(await send(path))
        }
    }

    /// Loads the resident voice model while the user is still speaking or the AI is thinking, and
    /// asks for `prepare` phrases to be rendered whenever the voice is idle.
    func warm(prepare: [String] = []) async { _ = try? await send("/speech/warm", body: prepare.isEmpty ? [:] : ["prepare": prepare]) }

    /// A phrase's audio only if it is already rendered; never waits for generation.
    func cachedAudio(_ text: String) async -> Data? {
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        guard let value = try? packet(await send("/speech", body: ["text": text, "request_id": id, "cached_only": true])),
              value["state"] as? String == "ready", let path = value["audio_url"] as? String,
              path.range(of: "^/speech/audio/[a-f0-9]{32}\\.wav$", options: .regularExpression) != nil
        else { return nil }
        return try? await send(path)
    }

    func cancel(_ requestID: String) async {
        // A fresh task is used by the owner when the rendering task is cancelled.
        _ = try? await send("/speech/cancel", body: ["request_id": requestID])
    }
}

/// Mono PCM16 at the voice rate (the only format the local service writes) as Float samples.
func pcm16Samples(_ data: Data, rate: Int = LOCAL_VOICE_RATE) -> [Float]? {
    let b = [UInt8](data)
    func u32(_ i: Int) -> Int { Int(b[i]) | Int(b[i + 1]) << 8 | Int(b[i + 2]) << 16 | Int(b[i + 3]) << 24 }
    func u16(_ i: Int) -> Int { Int(b[i]) | Int(b[i + 1]) << 8 }
    guard b.count >= 12, b[0..<4] == [82, 73, 70, 70], b[8..<12] == [87, 65, 86, 69] else { return nil }
    var i = 12, format = false
    while i + 8 <= b.count {
        let id = String(decoding: b[i..<i + 4], as: UTF8.self), size = u32(i + 4), body = i + 8
        guard size >= 0, body + size <= b.count || id == "data" else { return nil }
        if id == "fmt " {
            guard size >= 16, u16(body) == 1, u16(body + 2) == 1, u32(body + 4) == rate, u16(body + 14) == 16 else { return nil }
            format = true
        } else if id == "data" {
            guard format else { return nil }
            let end = min(b.count, body + size)
            return stride(from: body, to: end - 1, by: 2).map { Float(Int16(bitPattern: UInt16(u16($0)))) / 32768 }
        }
        i = body + size + (size & 1)
    }
    return nil
}

/// One audio-anchored progress point: playback seconds → fraction of the reply text spoken.
struct SpokenAnchor: Equatable { var seconds: Double; var fraction: Double }

func spokenAnchors(_ value: Any?) -> [SpokenAnchor]? {
    guard let rows = value as? [[Double]], !rows.isEmpty else { return nil }
    var out: [SpokenAnchor] = []
    for row in rows {
        guard row.count == 2, row[1] >= 0, row[1] <= 1, row[0] >= (out.last?.seconds ?? 0), row[1] >= (out.last?.fraction ?? 0)
        else { return nil }
        out.append(SpokenAnchor(seconds: row[0], fraction: row[1]))
    }
    return out
}

/// Fraction of the text spoken at `seconds`. Holds at the last anchor until more audio is
/// analysed, so the text can trail the voice slightly but does not run ahead of it.
func spokenFraction(_ anchors: [SpokenAnchor], at seconds: Double, complete: Bool) -> Double {
    guard let first = anchors.first, seconds > first.seconds else { return 0 }
    for (a, b) in zip(anchors, anchors.dropFirst()) where seconds <= b.seconds {
        return a.fraction + (b.fraction - a.fraction) * (seconds - a.seconds) / max(b.seconds - a.seconds, 1e-6)
    }
    return complete ? 1 : anchors[anchors.count - 1].fraction
}
