import Foundation

final class CatalogProtocol: URLProtocol {
    static var handler: (URLRequest) -> (Int, String) = { _ in (500, "{}") }
    static var paths: [String] = []
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.paths.append(request.url!.path)
        let (status, body) = Self.handler(request)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
            httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
enum TaskWatcherCheck {
    static let timeout: TimeInterval = 2
    static let tick: TimeInterval = 0.01
    static func main() throws {
        var passed = 0
        func check(_ value: Bool) { precondition(value); passed += 1 }
        func spin(until condition: () -> Bool) {
            let deadline = Date().addingTimeInterval(timeout)
            while !condition(), Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(tick)) }
            precondition(condition(), "Timed out waiting for the fixture")
        }
        let canonical = Data(#"{"active_ids":["a","b"],"tasks":[{"id":"a","status":"running"},{"id":"b","status":"complete","result":{"answer":"Verified answer"}}]}"#.utf8)
        let current = try JSONDecoder().decode(WorkflowCatalog.self, from: canonical)
        check(current.active == "a" && current.runs.count == 2)
        check(current.runs[1].result?.answer == "Verified answer")
        let legacy = Data(#"{"active":null,"runs":[{"id":"a","status":"failed"}]}"#.utf8)
        let old = try JSONDecoder().decode(WorkflowCatalog.self, from: legacy)
        check(old.active == nil && old.runs.first?.status == "failed")
        let idle = Data(#"{"active_ids":[],"tasks":[]}"#.utf8)
        let empty = try JSONDecoder().decode(WorkflowCatalog.self, from: idle)
        check(empty.active == nil && empty.runs.isEmpty)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [CatalogProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let watcher = TaskWatcher(session: session)
        defer { watcher.stop() }
        var activities: [Bool] = []
        var updates: [WorkflowUpdate] = []
        watcher.onActivity = { activities.append($0) }
        watcher.onUpdate = { updates.append($0) }
        CatalogProtocol.handler = { request in
            request.url!.path == "/stage2/tasks" ? (404, "{}") :
                (200, #"{"active":"a","runs":[{"id":"a","status":"running"}]}"#)
        }
        watcher.start()
        spin { activities == [true] }
        check(CatalogProtocol.paths == ["/stage2/tasks", "/workflows"])
        check(updates.isEmpty)
        CatalogProtocol.handler = { _ in (500, "{}") }
        watcher.poll()
        spin { CatalogProtocol.paths.count == 3 }
        // Drain the main callback before checking failure delivery.
        RunLoop.main.run(until: Date().addingTimeInterval(tick))
        check(activities == [true])
        CatalogProtocol.handler = { _ in
            (200, #"{"active":null,"runs":[{"id":"a","status":"complete","result":{"answer":"A verified result"}}]}"#)
        }
        watcher.poll()
        spin { updates.count == 1 }
        check(activities == [true, false])
        check(updates[0].summary == "A verified result")
        watcher.poll()
        spin { CatalogProtocol.paths.count == 5 }
        RunLoop.main.run(until: Date().addingTimeInterval(tick))
        check(updates.count == 1)
        let approvals = TaskWatcher(session: session)
        defer { approvals.stop() }
        var approvalTitles: [String?] = []
        approvals.onApproval = { approvalTitles.append($0) }
        CatalogProtocol.handler = { _ in
            (200, #"{"active_ids":[],"tasks":[],"note_actions":{"pending_count":1,"items":[{"id":"edit","status":"pending"}]}}"#)
        }
        approvals.start()
        spin { approvalTitles.count == 1 }
        check(approvalTitles[0] == "A note edit needs review")
        CatalogProtocol.handler = { _ in (500, "{}") }
        let failedCount = CatalogProtocol.paths.count
        approvals.poll()
        spin { CatalogProtocol.paths.count > failedCount }
        RunLoop.main.run(until: Date().addingTimeInterval(tick))
        check(approvalTitles.count == 1)
        CatalogProtocol.handler = { _ in
            (200, #"{"active_ids":[],"tasks":[],"note_actions":{"pending_count":0,"items":[]}}"#)
        }
        approvals.poll()
        spin { approvalTitles.count == 2 }
        check(approvalTitles[1] == nil)
        print("Task watcher compatibility and delivery checks: \(passed) passed")
    }
}
