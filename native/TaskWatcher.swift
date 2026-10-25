import Foundation

struct WorkflowCatalog: Decodable {
    let active: String?
    let runs: [WorkflowRun]
}

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
    static let endpoint = URL(string: "http://127.0.0.1:4700/workflows")!
    static let busyInterval: TimeInterval = 3
    static let idleInterval: TimeInterval = 30
    static let timeout: TimeInterval = 5
    static let maximumSummaryCharacters = 200
    static let terminalStatuses: Set<String> = ["complete", "failed", "cancelled", "interrupted"]

    var onUpdate: (WorkflowUpdate) -> Void = { _ in }
    var onActivity: (Bool) -> Void = { _ in }
    private var seen: [String: String] = [:]
    private var lastBusy: Bool?
    private var initialized = false
    private var stopped = false
    private var timer: Timer?

    func start() { poll() }
    func stop() { stopped = true; timer?.invalidate() }

    private func poll() {
        guard !stopped else { return }
        var request = URLRequest(url: Self.endpoint)
        request.timeoutInterval = Self.timeout
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self, !self.stopped else { return }
                guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                      let data, let catalog = try? JSONDecoder().decode(WorkflowCatalog.self, from: data) else {
                    if self.lastBusy != false {
                        self.lastBusy = false
                        self.onActivity(false)
                    }
                    self.schedule(Self.idleInterval)
                    return
                }
                let busy = catalog.active != nil || catalog.runs.contains {
                    !Self.terminalStatuses.contains($0.status)
                }
                if self.lastBusy != busy {
                    self.lastBusy = busy
                    self.onActivity(busy)
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
