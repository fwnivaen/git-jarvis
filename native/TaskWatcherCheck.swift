import Foundation

final class CatalogProtocol: URLProtocol {
    static var handler: (URLRequest) -> (Int, String) = { _ in (500, "{}") }
    static var paths: [String] = []
    static var deferNext = false
    static var deferred: [() -> Void] = []
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.paths.append(request.url!.path)
        let (status, body) = Self.handler(request)
        if Self.deferNext {
            Self.deferNext = false
            Self.deferred.append { self.deliver(status: status, body: body) }
        } else { deliver(status: status, body: body) }
    }
    private func deliver(status: Int, body: String) {
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
        CatalogProtocol.handler = { _ in
            (200, #"{"active_ids":[],"tasks":[],"action_approvals":{"pending_count":1,"items":[{"id":"preference","status":"pending"}]},"note_actions":{"pending_count":0,"items":[]}}"#)
        }
        approvals.poll()
        spin { approvalTitles.count == 3 }
        check(approvalTitles[2] == "A note or preference change needs review")

        let policyWatcher = TaskWatcher(session: session)
        defer { policyWatcher.stop() }
        var policies: [TaskDeliveryPolicy] = []
        var generations: [Int] = []
        policyWatcher.deliveryGeneration = { 7 }
        policyWatcher.onDeliveryPolicy = { policies.append($0); generations.append($1) }
        let revision = String(repeating: "a", count: 64)
        CatalogProtocol.handler = { _ in
            (200, "{\"active_ids\":[],\"tasks\":[],\"delivery_policy\":{\"schema_version\":1,\"quiet_hours_active\":true,\"speak_background_updates\":false,\"managed\":true,\"revision\":\"\(revision)\"}}")
        }
        policyWatcher.start()
        spin { policies.count == 1 }
        check(policies[0].quietHoursActive && !policies[0].speakBackgroundUpdates && policies[0].managed)
        check(generations == [7])
        for body in [
            #"{"active_ids":[],"tasks":[]}"#,
            #"{"active_ids":[],"tasks":[],"delivery_policy":{"schema_version":1,"quiet_hours_active":"false"}}"#
        ] {
            let count = CatalogProtocol.paths.count
            CatalogProtocol.handler = { _ in (200, body) }
            policyWatcher.poll()
            spin { CatalogProtocol.paths.count > count }
            RunLoop.main.run(until: Date().addingTimeInterval(tick))
            check(policies.count == 1)
        }
        CatalogProtocol.deferNext = true
        CatalogProtocol.handler = { _ in
            (200, "{\"active_ids\":[],\"tasks\":[],\"delivery_policy\":{\"schema_version\":1,\"quiet_hours_active\":false,\"speak_background_updates\":true,\"managed\":true,\"revision\":\"\(revision)\"}}")
        }
        policyWatcher.poll()
        spin { CatalogProtocol.deferred.count == 1 }
        CatalogProtocol.handler = { _ in
            (200, "{\"active_ids\":[],\"tasks\":[],\"delivery_policy\":{\"schema_version\":1,\"quiet_hours_active\":true,\"speak_background_updates\":false,\"managed\":true,\"revision\":\"\(revision)\"}}")
        }
        policyWatcher.poll()
        spin { policies.count == 2 }
        check(policies.last?.quietHoursActive == true)
        CatalogProtocol.deferred.removeFirst()()
        RunLoop.main.run(until: Date().addingTimeInterval(tick))
        check(policies.count == 2 && policies.last?.quietHoursActive == true)
        print("Task watcher compatibility and delivery checks: \(passed) passed")
    }
}
