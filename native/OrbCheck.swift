import AppKit
import SwiftUI

@main
enum OrbCheck {
    static func main() {
        var passed = 0
        func check(_ condition: Bool, _ message: String) {
            precondition(condition, message)
            passed += 1
        }
        for expanded in [false, true] {
            check(OrbDesign.framesPerSecond(expanded: expanded, hovering: false, transitioning: false)
                  == (expanded ? 30 : 24), "rest frame rate")
            check(OrbDesign.framesPerSecond(expanded: expanded, hovering: false, transitioning: true)
                  == 60, "moving frame rate")
            for transitioning in [false, true] {
                check(OrbDesign.framesPerSecond(expanded: expanded, hovering: true,
                                               transitioning: transitioning) == 60,
                      "hover keeps motion frame rate")
            }
        }
        let host = OrbHostView(frame: NSRect(x: 0, y: 0, width: 22, height: 22),
                               phase: .listening, level: 0.25, size: 22,
                               expanded: false, hovering: false, rendererFactory: { _ in nil })
        check(host.renderer == nil && host.metalView == nil, "failed renderer is not attached")
        check(host.fallbackView != nil && host.subviews.count == 1, "Canvas fallback is attached")
        host.update(phase: .speaking, level: 0.75, size: 44, expanded: true, hovering: true)
        check(host.fallbackView?.rootView.phase == .speaking, "fallback follows phase")
        check(host.fallbackView?.rootView.level == 0.75 && host.fallbackView?.rootView.size == 44,
              "fallback follows meter and size")
        host.stop()
        check(host.fallbackView != nil, "stopping an unavailable renderer preserves fallback")
        print("Orb fallback and frame-rate checks: \(passed) passed")
    }
}
