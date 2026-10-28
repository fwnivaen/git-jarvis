import Foundation

final class OperationsFixtureProtocol: URLProtocol {
    static var handler: (URLRequest) -> (Int, Data) = { _ in (500, Data()) }
    static var requests: [URLRequest] = []
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        let (status, body) = Self.handler(request)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
            httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
enum DeliveryPreferencesCheck {
    static let timeout: TimeInterval = 2
    static let tick: TimeInterval = 0.01
    static func main() throws {
        var passed = 0
        func check(_ value: Bool) { precondition(value); passed += 1 }
        func spin(until condition: () -> Bool) {
            let deadline = Date().addingTimeInterval(timeout)
            while !condition(), Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(tick)) }
            precondition(condition(), "Timed out waiting for fixture")
        }
        func snapshot(_ revision: String, speak: Bool, managed: Bool = true) -> Data {
            try! JSONSerialization.data(withJSONObject: ["schema_version": 1, "managed": managed,
                "revision": revision, "settings": ["speak_background_updates": speak]])
        }
        func policy(_ revision: String, managed: Bool = true) throws -> TaskDeliveryPolicy {
            let data = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "managed": managed,
                "revision": revision, "quiet_hours_active": true, "speak_background_updates": false])
            return try JSONDecoder().decode(TaskDeliveryPolicy.self, from: data)
        }
        func body(_ request: URLRequest) -> Data {
            if let body = request.httpBody { return body }
            guard let stream = request.httpBodyStream else { return Data() }
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(contentsOf: buffer.prefix(count))
            }
            return data
        }
        let a = String(repeating: "a", count: 64), b = String(repeating: "b", count: 64)
        let c = String(repeating: "c", count: 64)
        check(OperationsRevision.valid(a) && !OperationsRevision.valid("not-a-revision"))
        check(try policy(a, managed: false).managed == false)
        for value: Any in ["true", 1, NSNull()] {
            let malformed = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "managed": true,
                "revision": a, "quiet_hours_active": value, "speak_background_updates": false])
            check((try? JSONDecoder().decode(TaskDeliveryPolicy.self, from: malformed)) == nil)
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OperationsFixtureProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = OperationsSpeechPreferences(session: session)
        var saved: OperationsSpeechSnapshot?
        OperationsFixtureProtocol.handler = { request in
            request.httpMethod == "POST" ? (200, snapshot(b, speak: true)) : (200, snapshot(a, speak: false, managed: false))
        }
        client.setSpeech(true) { if case .saved(let value) = $0 { saved = value } }
        spin { saved != nil }
        check(OperationsFixtureProtocol.requests.map { $0.url!.path } == ["/operations", "/operations/settings"])
        let post = OperationsFixtureProtocol.requests[1]
        check(post.value(forHTTPHeaderField: "Origin") == "http://127.0.0.1:4700")
        check(post.value(forHTTPHeaderField: "Content-Type") == "application/json")
        let payload = try JSONSerialization.jsonObject(with: body(post)) as! [String: Any]
        check(Set(payload.keys) == ["settings", "expected_revision"])
        check(payload["expected_revision"] as? String == a)
        let patch = payload["settings"] as! [String: Any]
        check(patch.count == 1 && patch["speak_background_updates"] as? Bool == true)
        check(saved?.settings.speakBackgroundUpdates == true && !client.updating)
        check(client.awaitingDeliveryPolicy)
        check(!client.accept(try policy(a), requestGeneration: 0) && client.awaitingDeliveryPolicy)
        check(client.accept(try policy(a), requestGeneration: client.generation))
        check(!client.awaitingDeliveryPolicy)
        // Hashes can repeat when a user deliberately reverts settings; only
        // request generations, never historical hashes, identify stale polls.
        check(client.accept(try policy(b), requestGeneration: client.generation))
        check(client.accept(try policy(a), requestGeneration: client.generation))

        var conflicted = false
        OperationsFixtureProtocol.handler = { _ in
            let current = try! JSONSerialization.jsonObject(with: snapshot(c, speak: false))
            return (409, try! JSONSerialization.data(withJSONObject: ["error": true, "current": current]))
        }
        client.setSpeech(true) { if case .conflict(let value) = $0 { conflicted = value?.revision == c } }
        spin { conflicted }
        check(!client.updating)
        check(client.awaitingDeliveryPolicy)
        check(OperationsFixtureProtocol.requests.count == 3)
        var failure = false
        OperationsFixtureProtocol.handler = { _ in (500, Data()) }
        client.setSpeech(true) { if case .failed = $0 { failure = true } }
        spin { failure }
        check(!client.updating)
        check(client.awaitingDeliveryPolicy)
        check(client.accept(try policy(c), requestGeneration: client.generation) && !client.awaitingDeliveryPolicy)
        let fallback = OperationsSpeechPreferences(session: session)
        var legacy = false
        OperationsFixtureProtocol.handler = { _ in (404, Data()) }
        let before = OperationsFixtureProtocol.requests.count
        fallback.setSpeech(false) { if case .legacy = $0 { legacy = true } }
        spin { legacy }
        check(OperationsFixtureProtocol.requests.count == before + 1 && !fallback.updating)
        check(!fallback.awaitingDeliveryPolicy)
        print("Delivery settings synchronization checks: \(passed) passed")
    }
}
