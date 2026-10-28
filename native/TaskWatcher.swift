import Foundation

struct WorkflowCatalog: Decodable {
    let active: String?
    let runs: [WorkflowRun]
    let noteActions: NoteActionCatalog?
    let actionApprovals: NoteActionCatalog?
    let deliveryPolicy: TaskDeliveryPolicy?
    enum CodingKeys: String, CodingKey {
        case active, activeIDs = "active_ids", runs, tasks, noteActions = "note_actions", deliveryPolicy = "delivery_policy"
        case actionApprovals = "action_approvals"
    }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let activeIDs = try container.decodeIfPresent([String].self, forKey: .activeIDs)
        active = try activeIDs?.first ?? container.decodeIfPresent(String.self, forKey: .active)
        if container.contains(.tasks) { runs = try container.decode([WorkflowRun].self, forKey: .tasks) }
        else { runs = try container.decode([WorkflowRun].self, forKey: .runs) }
        noteActions = try container.decodeIfPresent(NoteActionCatalog.self, forKey: .noteActions)
        actionApprovals = try container.decodeIfPresent(NoteActionCatalog.self, forKey: .actionApprovals)
        // A missing or malformed optional policy cannot erase the last accepted
        // quiet gate or overwrite a legacy client's local speech preference.
        deliveryPolicy = try? container.decode(TaskDeliveryPolicy.self, forKey: .deliveryPolicy)
    }
}

struct NoteActionCatalog: Decodable {
    let pendingCount: Int
    let items: [NoteActionSummary]
    enum CodingKeys: String, CodingKey { case pendingCount = "pending_count", items }
}
struct NoteActionSummary: Decodable { let id: String; let status: String }

struct WorkflowRun: Decodable {
    let id: String
    let status: String
    let result: WorkflowResult?
}

struct WorkflowResult: Decodable {
    let answer: String
}

struct WorkflowUpdate {
    let id: String
    let status: String
    let summary: String
}

final class TaskWatcher {
    static let endpoint = URL(string: "http://127.0.0.1:4700/stage2/tasks")!
    static let legacyEndpoint = URL(string: "http://127.0.0.1:4700/workflows")!
    static let busyInterval: TimeInterval = 3
    static let idleInterval: TimeInterval = 30
    static let timeout: TimeInterval = 5
    static let maximumSummaryCharacters = 200
    static let terminalStatuses: Set<String> = ["complete", "failed", "cancelled", "interrupted"]

    var onUpdate: (WorkflowUpdate) -> Void = { _ in }
    var onActivity: (Bool) -> Void = { _ in }
    var onApproval: (String?) -> Void = { _ in }
    var deliveryGeneration: () -> Int = { 0 }
    var onDeliveryPolicy: (TaskDeliveryPolicy, Int) -> Void = { _, _ in }
    private var seen: [String: String] = [:]
    private var lastBusy: Bool?
    private var lastActivity: Bool?
    private var lastApproval: String?
    private var initialized = false
    private var stopped = false
    private var pollGeneration = 0
    private var timer: Timer?
    private var useLegacyEndpoint = false
    private let session: URLSession
    private let catalogEndpoint: URL
    private let fallbackEndpoint: URL

    init(session: URLSession = .shared, endpoint: URL = TaskWatcher.endpoint,
         legacyEndpoint: URL = TaskWatcher.legacyEndpoint) {
        self.session = session
        self.catalogEndpoint = endpoint
        self.fallbackEndpoint = legacyEndpoint
    }

    func start() { poll() }
    func stop() { stopped = true; timer?.invalidate() }

    func poll() {
        guard !stopped else { return }
        timer?.invalidate()
        pollGeneration += 1
        let currentPoll = pollGeneration
        var request = URLRequest(url: useLegacyEndpoint ? fallbackEndpoint : catalogEndpoint)
        let policyGeneration = deliveryGeneration()
        request.timeoutInterval = Self.timeout
        session.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                // A manual refresh can overlap a prior scheduled request. Its
                // older response cannot undo newer quiet or speech settings.
                guard let self, !self.stopped, currentPoll == self.pollGeneration else { return }
                if let http = response as? HTTPURLResponse, http.statusCode == 404, !self.useLegacyEndpoint {
                    self.useLegacyEndpoint = true
                    self.poll()
                    return
                }
                guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                      let data, let catalog = try? JSONDecoder().decode(WorkflowCatalog.self, from: data) else {
                    // A failed poll cannot confirm that ongoing work stopped.
                    self.schedule(self.lastBusy == true ? Self.busyInterval : Self.idleInterval)
                    return
                }
                let workActive = catalog.active != nil || catalog.runs.contains {
                    !Self.terminalStatuses.contains($0.status)
                }
                if !self.useLegacyEndpoint, let policy = catalog.deliveryPolicy {
                    self.onDeliveryPolicy(policy, policyGeneration)
                }
                let pending = (catalog.actionApprovals ?? catalog.noteActions)?.items.filter { $0.status == "pending" }.count ?? 0
                let approval: String?
                if catalog.actionApprovals != nil {
                    approval = pending > 0 ? (pending == 1 ? "A note or preference change needs review" : "\(pending) note or preference changes need review") : nil
                } else {
                    approval = pending > 0 ? (pending == 1 ? "A note edit needs review" : "\(pending) note edits need review") : nil
                }
                if self.lastApproval != approval {
                    self.lastApproval = approval
                    self.onApproval(approval)
                }
                let busy = workActive || pending > 0
                self.lastBusy = busy
                if self.lastActivity != workActive {
                    self.lastActivity = workActive
                    self.onActivity(workActive)
                }
                if !self.initialized {
                    self.seen = catalog.runs.reduce(into: [:]) { result, run in result[run.id] = run.status }
                    self.initialized = true
                } else {
                    for run in catalog.runs {
                        let previous = self.seen.updateValue(run.status, forKey: run.id)
                        guard previous != run.status,
                              Self.terminalStatuses.contains(run.status) else { continue }
                        let message: String
                        if run.status == "complete" {
                            let answer = run.result?.answer.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                            message = answer.isEmpty ? "A workflow finished." :
                                String(answer.prefix(Self.maximumSummaryCharacters))
                        } else {
                            message = "A workflow \(run.status). Open Jarvis for details."
                        }
                        self.onUpdate(WorkflowUpdate(id: run.id, status: run.status, summary: message))
                    }
                }
                self.schedule(busy ? Self.busyInterval : Self.idleInterval)
            }
        }.resume()
    }

    private func schedule(_ interval: TimeInterval) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: false) { [weak self] _ in
            self?.poll()
        }
    }
}
