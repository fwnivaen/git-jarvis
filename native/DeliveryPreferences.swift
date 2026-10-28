import Foundation

enum OperationsRevision {
    static func valid(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy {
            (48...57).contains($0) || (97...102).contains($0)
        }
    }
}

struct TaskDeliveryPolicy: Decodable, Equatable {
    let quietHoursActive: Bool
    let speakBackgroundUpdates: Bool
    let revision: String
    let managed: Bool
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", quietHoursActive = "quiet_hours_active"
        case speakBackgroundUpdates = "speak_background_updates", revision, managed
    }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(Int.self, forKey: .schemaVersion) == 1 else {
            throw DecodingError.dataCorruptedError(forKey: .schemaVersion, in: container, debugDescription: "Unsupported policy")
        }
        quietHoursActive = try container.decode(Bool.self, forKey: .quietHoursActive)
        speakBackgroundUpdates = try container.decode(Bool.self, forKey: .speakBackgroundUpdates)
        revision = try container.decode(String.self, forKey: .revision)
        managed = try container.decode(Bool.self, forKey: .managed)
        guard OperationsRevision.valid(revision) else {
            throw DecodingError.dataCorruptedError(forKey: .revision, in: container, debugDescription: "Invalid revision")
        }
    }
}

struct OperationsSpeechSnapshot: Decodable {
    struct Settings: Decodable {
        let speakBackgroundUpdates: Bool
        enum CodingKeys: String, CodingKey { case speakBackgroundUpdates = "speak_background_updates" }
    }
    let settings: Settings
    let revision: String
    let managed: Bool
    enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version", settings, revision, managed }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(Int.self, forKey: .schemaVersion) == 1 else {
            throw DecodingError.dataCorruptedError(forKey: .schemaVersion, in: container, debugDescription: "Unsupported settings")
        }
        settings = try container.decode(Settings.self, forKey: .settings)
        revision = try container.decode(String.self, forKey: .revision)
        managed = try container.decode(Bool.self, forKey: .managed)
        guard OperationsRevision.valid(revision) else {
            throw DecodingError.dataCorruptedError(forKey: .revision, in: container, debugDescription: "Invalid revision")
        }
    }
}

// Only an explicit menu action writes a speech-only patch. Revision checks
// prevent native controls from replacing newer web settings or quiet hours.
// All state and completions are serialized on the main run loop.
final class OperationsSpeechPreferences {
    enum Result { case saved(OperationsSpeechSnapshot), legacy, conflict(OperationsSpeechSnapshot?), failed }
    static let endpoint = URL(string: "http://127.0.0.1:4700/operations")!
    static let settingsEndpoint = URL(string: "http://127.0.0.1:4700/operations/settings")!
    static let origin = "http://127.0.0.1:4700"
    static let timeout: TimeInterval = 5
    private let session: URLSession
    private var revision: String?
    private(set) var updating = false
    private(set) var generation = 0
    private(set) var awaitingDeliveryPolicy = false

    init(session: URLSession = .shared) { self.session = session }

    @discardableResult func accept(_ policy: TaskDeliveryPolicy, requestGeneration: Int) -> Bool {
        guard requestGeneration == generation, !updating else { return false }
        revision = policy.revision
        awaitingDeliveryPolicy = false
        return true
    }

    private func finish(_ result: Result, completion: (Result) -> Void) {
        updating = false
        generation += 1
        if case .legacy = result { awaitingDeliveryPolicy = false }
        completion(result)
    }

    func setSpeech(_ enabled: Bool, completion: @escaping (Result) -> Void) {
        guard !updating else { completion(.failed); return }
        updating = true
        // A speech-only acknowledgement does not establish the current quiet
        // gate. Keep delivery deferred until a fresh canonical policy arrives.
        awaitingDeliveryPolicy = true
        generation += 1
        if let revision { post(enabled, revision: revision, completion: completion); return }
        var request = URLRequest(url: Self.endpoint)
        request.timeoutInterval = Self.timeout
        session.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 404 {
                    self.finish(.legacy, completion: completion)
                    return
                }
                guard (response as? HTTPURLResponse)?.statusCode == 200, let data,
                      let snapshot = try? JSONDecoder().decode(OperationsSpeechSnapshot.self, from: data) else {
                    self.finish(.failed, completion: completion)
                    return
                }
                self.revision = snapshot.revision
                self.post(enabled, revision: snapshot.revision, completion: completion)
            }
        }.resume()
    }

    private func post(_ enabled: Bool, revision: String, completion: @escaping (Result) -> Void) {
        var request = URLRequest(url: Self.settingsEndpoint)
        request.timeoutInterval = Self.timeout
        request.httpMethod = "POST"
        request.setValue(Self.origin, forHTTPHeaderField: "Origin")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "settings": ["speak_background_updates": enabled], "expected_revision": revision
        ])
        session.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                let status = (response as? HTTPURLResponse)?.statusCode
                if status == 409 {
                    struct Conflict: Decodable { let current: OperationsSpeechSnapshot? }
                    let current = data.flatMap { try? JSONDecoder().decode(Conflict.self, from: $0) }?.current
                    if let current { self.revision = current.revision }
                    self.finish(.conflict(current), completion: completion)
                    return
                }
                guard status == 200, let data,
                      let snapshot = try? JSONDecoder().decode(OperationsSpeechSnapshot.self, from: data),
                      snapshot.managed else { self.finish(.failed, completion: completion); return }
                self.revision = snapshot.revision
                self.finish(.saved(snapshot), completion: completion)
            }
        }.resume()
    }
}
