import AppKit
import QuartzCore

@main
enum IslandLayerCheck {
    static func main() throws {
        var passed = 0
        func check(_ condition: Bool, _ message: String) {
            precondition(condition, message)
            passed += 1
        }
        let meter = IslandLayerMeter()
        meter.store(-1)
        check(meter.read() == 0, "negative meter values clamp")
        meter.store(2)
        check(meter.read() == 1, "high meter values clamp")
        meter.store(.nan)
        check(meter.read() == 0, "invalid meter values become silence")
        let view = IslandActivityView(frame: NSRect(x: 0, y: 0, width: 280, height: 76), meter: meter)
        defer { view.stop() }
        view.configure(phase: .listening, meter: meter, expanded: true, notchHeight: 0, reduceMotion: false)
        view.layoutSubtreeIfNeeded()
        view.applyLevel(0, animated: false)
        check(view.bars.count == 5 && view.bars.allSatisfy { !$0.isHidden }, "listening displays five bars")
        check(view.bars.allSatisfy { $0.bounds.height == IslandLayerDesign.barMinimumHeight }, "silence is flat")
        view.applyLevel(0.8, animated: false)
        let heights = view.bars.map { $0.bounds.height }
        check(heights[2] > heights[1] && heights[1] > heights[0], "voice energy changes bar heights")
        check(heights[0] == heights[4] && heights[1] == heights[3], "meter remains symmetric")
        view.applyLevel(0, animated: true)
        let release = view.bars[2].animation(forKey: IslandLayerDesign.meterAnimationKey)
        check(release?.duration == IslandDesign.motionDuration(IslandLayerDesign.barReleaseDuration), "release uses 120 ms baseline")
        view.configure(phase: .listening, meter: meter, expanded: true, notchHeight: 0, reduceMotion: true)
        view.applyLevel(0.8, animated: false)
        check(view.bars.allSatisfy { ($0.animationKeys() ?? []).isEmpty }, "reduced-motion meter has no animation")
        check(view.bars[2].bounds.height > IslandLayerDesign.barMinimumHeight, "reduced-motion meter still communicates input")
        view.configure(phase: .thinking, meter: meter, expanded: true, notchHeight: 0, reduceMotion: false)
        check(view.bars.allSatisfy { $0.isHidden }, "thinking hides microphone bars")
        check(!view.shimmer.isHidden, "thinking displays shimmer layer")
        let shimmer = view.shimmer.animation(forKey: IslandLayerDesign.shimmerAnimationKey)
        check(shimmer?.repeatCount == .infinity, "shimmer uses a render-server repeat")
        check(shimmer?.duration == IslandDesign.motionDuration(IslandLayerDesign.shimmerDuration), "shimmer honors visual slow motion")
        view.configure(phase: .working, meter: meter, expanded: true, notchHeight: 0, reduceMotion: true)
        check(!view.shimmer.isHidden && view.shimmer.opacity > 0, "reduced-motion work keeps a static indicator")
        check((view.shimmer.animationKeys() ?? []).isEmpty, "reduced-motion work removes motion")
        view.configure(phase: .hidden, meter: meter, expanded: true, notchHeight: 0, reduceMotion: false)
        check(view.shimmer.isHidden && view.bars.allSatisfy { $0.isHidden }, "hidden state has no visible layer effects")

        func display(_ phase: IslandPhase, detail: String = "") -> IslandDisplay {
            IslandDisplay(phase: phase, title: "Jarvis", detail: detail,
                          wordIndex: nil, microphoneLevel: 0)
        }
        let compact = IslandLayout.size(for: display(.ready), hasNotch: false, notchHeight: 0,
                                         notchWidth: 180, availableWidth: 466)
        check(compact == NSSize(width: 176, height: 30), "no-notch ready keeps compact geometry")
        for phase: IslandPhase in [.done, .notification] {
            let peek = IslandLayout.size(for: display(phase, detail: "Your schedule is clear."),
                hasNotch: false, notchHeight: 0, notchWidth: 180, availableWidth: 466)
            check(peek.height > compact.height, "peek has room for its secondary row")
            let notched = IslandLayout.size(for: display(phase, detail: "Your schedule is clear."),
                hasNotch: true, notchHeight: 32, notchWidth: 180, availableWidth: 466)
            check(notched.height == peek.height + 32 && notched.width == peek.width + 180,
                  "notched peek reserves real notch and ear geometry")
        }
        let narrow = IslandLayout.size(for: display(.thinking, detail: "Question"), hasNotch: false,
                                        notchHeight: 0, notchWidth: 0, availableWidth: 320)
        check(narrow.width == 320 && narrow.height == 76, "expanded layout adapts to narrow displays")

        // Pixel checks verify real layer output, not a duplicate height formula.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-layer-check-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        check(try IslandLayerEvidence.render(directory: directory) == 5, "all actual layer samples render")
        func bitmap(_ name: String) throws -> NSBitmapImageRep {
            let data = try Data(contentsOf: directory.appendingPathComponent(name + ".png"))
            return NSBitmapImageRep(data: data)!
        }
        func coloredPixels(_ image: NSBitmapImageRep) -> Int {
            var count = 0
            for y in 0..<image.pixelsHigh {
                for x in 0..<image.pixelsWide {
                    guard let color = image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
                    if max(color.redComponent, color.greenComponent, color.blueComponent) > 0.01 { count += 1 }
                }
            }
            return count
        }
        let silence = try bitmap("listening-silence")
        let speech = try bitmap("listening-speech")
        check(coloredPixels(speech) > coloredPixels(silence), "real speech-layer drawing has more energy than silence")
        let left = try bitmap("thinking-left")
        let right = try bitmap("thinking-right")
        check(coloredPixels(left) > 0 && coloredPixels(right) > 0, "real gradient-layer drawing is visible")
        check(left.bitmapData != nil && right.bitmapData != nil &&
              Data(bytes: left.bitmapData!, count: left.bytesPerRow * left.pixelsHigh) !=
              Data(bytes: right.bitmapData!, count: right.bytesPerRow * right.pixelsHigh),
              "shimmer positions produce different actual pixels")
        let reduced = try bitmap("thinking-reduced-motion")
        check(coloredPixels(reduced) > 0, "reduced-motion static indicator renders")
        print("Island layer and sizing checks: \(passed) passed")
    }
}
