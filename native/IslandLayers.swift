import AppKit
import QuartzCore
import SwiftUI

enum IslandLayerDesign {
    static let barWeights: [CGFloat] = [0.45, 0.75, 1, 0.75, 0.45]
    static let barWidth: CGFloat = 2.5
    static let barGap: CGFloat = 2.5
    static let barMinimumHeight: CGFloat = 2
    static let compactBarHeight: CGFloat = 12
    static let expandedBarHeight: CGFloat = 20
    static let barReleaseDuration: TimeInterval = 0.12
    static let meterFramesPerSecond = 30.0
    static let shimmerHeight: CGFloat = 1.5
    static let shimmerBottomInset: CGFloat = 6
    static let shimmerSpan: CGFloat = 0.35
    static let shimmerOpacity: Float = 0.38
    static let shimmerDuration: TimeInterval = 1.6
    static let staticShimmerOpacity: Float = 0.14
    static let minimumAnimationDelta = 0.01
    static let evidenceWidth: CGFloat = 280
    static let evidenceHeight: CGFloat = 76
    static let shimmerAnimationKey = "jarvis.thinking.shimmer"
    static let meterAnimationKey = "jarvis.microphone.level"
    static var barsWidth: CGFloat {
        CGFloat(barWeights.count) * barWidth + CGFloat(barWeights.count - 1) * barGap
    }

    static func height(level: Double, weight: CGFloat, expanded: Bool) -> CGFloat {
        let normalized = level.isFinite ? min(1, max(0, level)) : 0
        let maximum = expanded ? expandedBarHeight : compactBarHeight
        return barMinimumHeight + CGFloat(normalized) * weight * (maximum - barMinimumHeight)
    }
}

// Receives only the already-normalized value from the existing audio tap.
// This object never owns or opens an audio input and never stores samples.
final class IslandLayerMeter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0.0
    func store(_ level: Double) {
        lock.lock()
        value = level.isFinite ? min(1, max(0, level)) : 0
        lock.unlock()
    }
    func read() -> Double {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

// Scalar layer updates use a separate clock. No SwiftUI state is published by
// this view, and the render-server shimmer does not need a main-thread timer.
final class IslandActivityView: NSView {
    override var isFlipped: Bool { true }
    private(set) var bars: [CALayer] = []
    private(set) var shimmer = CAGradientLayer()
    private(set) var phase: IslandPhase = .hidden
    private var expanded = false
    private var notchHeight: CGFloat = 0
    private var reduceMotion = false
    private var meter: IslandLayerMeter
    private var meterTimer: Timer?
    private var previousLevel = 0.0
    private var previousShimmerSpan: CGFloat = -1

    init(frame: NSRect, meter: IslandLayerMeter) {
        self.meter = meter
        super.init(frame: frame)
        wantsLayer = true
        guard let root = layer else { return }
        root.masksToBounds = true
        for _ in IslandLayerDesign.barWeights {
            let bar = CALayer()
            bar.backgroundColor = NSColor(IslandDesign.teal).cgColor
            bar.cornerRadius = IslandLayerDesign.barWidth / 2
            root.addSublayer(bar)
            bars.append(bar)
        }
        shimmer.colors = [NSColor.clear.cgColor, NSColor(IslandDesign.violet).cgColor,
                          NSColor.clear.cgColor]
        shimmer.locations = [0, 0.5, 1]
        shimmer.startPoint = CGPoint(x: 0, y: 0.5)
        shimmer.endPoint = CGPoint(x: 1, y: 0.5)
        root.addSublayer(shimmer)
        setVisibility()
    }

    required init?(coder: NSCoder) { return nil }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func configure(phase: IslandPhase, meter: IslandLayerMeter, expanded: Bool,
                   notchHeight: CGFloat, reduceMotion: Bool) {
        let changed = self.phase != phase || self.reduceMotion != reduceMotion
        self.phase = phase
        self.meter = meter
        self.expanded = expanded
        self.notchHeight = notchHeight
        self.reduceMotion = reduceMotion
        needsLayout = true
        setVisibility()
        if changed { updateShimmer() }
        refreshMeterClock()
        applyLevel(meter.read(), animated: !reduceMotion && phase == .listening)
    }

    private func setVisibility() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for bar in bars { bar.isHidden = phase != .listening }
        shimmer.isHidden = phase != .thinking && phase != .working
        shimmer.opacity = reduceMotion ? IslandLayerDesign.staticShimmerOpacity : IslandLayerDesign.shimmerOpacity
        CATransaction.commit()
    }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let inset = expanded ? IslandDesign.spacing[4] : IslandDesign.spacing[3]
        let left = max(0, bounds.width - inset - IslandLayerDesign.barsWidth)
        let center = notchHeight + max(0, bounds.height - notchHeight) / 2
        for (index, bar) in bars.enumerated() {
            bar.position = CGPoint(x: left + CGFloat(index) * (IslandLayerDesign.barWidth + IslandLayerDesign.barGap)
                                    + IslandLayerDesign.barWidth / 2, y: center)
            bar.bounds.size = CGSize(width: IslandLayerDesign.barWidth,
                height: IslandLayerDesign.height(level: previousLevel,
                                                  weight: IslandLayerDesign.barWeights[index], expanded: expanded))
        }
        let span = max(0, bounds.width - 2 * inset)
        let shimmerWidth = span * IslandLayerDesign.shimmerSpan
        // Model transforms can be set by deterministic evidence sampling.
        // Reset before assigning frame: CALayer.frame is undefined under a
        // nonidentity transform and would displace a reduced-motion sample.
        shimmer.transform = CATransform3DIdentity
        shimmer.frame = CGRect(x: reduceMotion ? inset + (span - shimmerWidth) / 2 : inset - shimmerWidth,
                               y: max(notchHeight, bounds.height - IslandLayerDesign.shimmerBottomInset),
                               width: shimmerWidth,
                               height: IslandLayerDesign.shimmerHeight)
        CATransaction.commit()
        if previousShimmerSpan != span { previousShimmerSpan = span; updateShimmer() }
    }

    private func updateShimmer() {
        shimmer.removeAnimation(forKey: IslandLayerDesign.shimmerAnimationKey)
        guard !reduceMotion, phase == .thinking || phase == .working else { return }
        let inset = expanded ? IslandDesign.spacing[4] : IslandDesign.spacing[3]
        let span = max(0, bounds.width - 2 * inset)
        let movement = CABasicAnimation(keyPath: "transform.translation.x")
        movement.fromValue = 0
        movement.toValue = span * (1 + IslandLayerDesign.shimmerSpan)
        movement.duration = IslandDesign.motionDuration(IslandLayerDesign.shimmerDuration)
        movement.repeatCount = .infinity
        movement.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        shimmer.add(movement, forKey: IslandLayerDesign.shimmerAnimationKey)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        refreshMeterClock()
    }

    private func refreshMeterClock() {
        guard phase == .listening, window != nil else {
            meterTimer?.invalidate()
            meterTimer = nil
            return
        }
        guard meterTimer == nil else { return }
        let timer = Timer(timeInterval: 1 / IslandLayerDesign.meterFramesPerSecond,
                          repeats: true) { [weak self] _ in
            guard let self else { return }
            self.applyLevel(self.meter.read(), animated: !self.reduceMotion)
        }
        meterTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func applyLevel(_ level: Double, animated: Bool) {
        let normalized = level.isFinite ? min(1, max(0, level)) : 0
        let duration = normalized < previousLevel ? IslandLayerDesign.barReleaseDuration : IslandDesign.meterAttack
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (index, bar) in bars.enumerated() {
            let height = IslandLayerDesign.height(level: normalized,
                weight: IslandLayerDesign.barWeights[index], expanded: expanded)
            if abs(bar.bounds.height - height) <= IslandLayerDesign.minimumAnimationDelta {
                if !animated { bar.removeAnimation(forKey: IslandLayerDesign.meterAnimationKey) }
                continue
            }
            let old = bar.presentation()?.bounds.height ?? bar.bounds.height
            bar.bounds.size.height = height
            if animated, abs(old - height) > IslandLayerDesign.minimumAnimationDelta {
                let animation = CABasicAnimation(keyPath: "bounds.size.height")
                animation.fromValue = old
                animation.toValue = height
                animation.duration = IslandDesign.motionDuration(duration)
                animation.timingFunction = CAMediaTimingFunction(name: .easeOut)
                bar.add(animation, forKey: IslandLayerDesign.meterAnimationKey)
            } else { bar.removeAnimation(forKey: IslandLayerDesign.meterAnimationKey) }
        }
        previousLevel = normalized
        CATransaction.commit()
    }

    // Synthetic deterministic evidence draws the real layer tree at fixed
    // values. It intentionally does not claim presentation-animation capture.
    func evidenceSample(level: Double, shimmerProgress: CGFloat) {
        layoutSubtreeIfNeeded()
        applyLevel(level, animated: false)
        shimmer.removeAllAnimations()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let inset = expanded ? IslandDesign.spacing[4] : IslandDesign.spacing[3]
        let span = max(0, bounds.width - 2 * inset)
        shimmer.transform = reduceMotion ? CATransform3DIdentity :
            CATransform3DMakeTranslation(span * (1 + IslandLayerDesign.shimmerSpan)
                * min(1, max(0, shimmerProgress)), 0, 0)
        CATransaction.commit()
    }

    func stop() {
        meterTimer?.invalidate()
        meterTimer = nil
        for bar in bars { bar.removeAllAnimations() }
        shimmer.removeAllAnimations()
    }
    deinit { meterTimer?.invalidate() }
}

struct IslandActivityLayers: NSViewRepresentable {
    let phase: IslandPhase
    let meter: IslandLayerMeter
    let expanded: Bool
    let notchHeight: CGFloat
    let reduceMotion: Bool

    func makeNSView(context: Context) -> IslandActivityView {
        IslandActivityView(frame: .zero, meter: meter)
    }
    func updateNSView(_ view: IslandActivityView, context: Context) {
        view.configure(phase: phase, meter: meter, expanded: expanded,
                       notchHeight: notchHeight, reduceMotion: reduceMotion)
    }
    static func dismantleNSView(_ view: IslandActivityView, coordinator: ()) { view.stop() }
}

enum IslandLayerEvidence {
    static func render(directory: URL) throws -> Int {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let size = NSSize(width: IslandLayerDesign.evidenceWidth, height: IslandLayerDesign.evidenceHeight)
        let meter = IslandLayerMeter()
        let view = IslandActivityView(frame: NSRect(origin: .zero, size: size), meter: meter)
        defer { view.stop() }
        let samples: [(String, IslandPhase, Double, CGFloat, Bool)] = [
            ("listening-silence", .listening, 0, 0, false),
            ("listening-speech", .listening, 0.8, 0, false),
            ("thinking-left", .thinking, 0, 0.25, false),
            ("thinking-right", .thinking, 0, 0.75, false),
            ("thinking-reduced-motion", .thinking, 0, 0.5, true)
        ]
        for (name, phase, level, progress, reduceMotion) in samples {
            view.configure(phase: phase, meter: meter, expanded: true,
                           notchHeight: 0, reduceMotion: reduceMotion)
            view.evidenceSample(level: level, shimmerProgress: progress)
            guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
                pixelsWide: Int(size.width), pixelsHigh: Int(size.height), bitsPerSample: 8,
                samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
                  let context = NSGraphicsContext(bitmapImageRep: bitmap) else { throw CocoaError(.fileWriteUnknown) }
            let graphics = context.cgContext
            graphics.setFillColor(NSColor.black.cgColor)
            graphics.fill(CGRect(origin: .zero, size: size))
            view.layer?.render(in: graphics)
            guard let png = bitmap.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
            try png.write(to: directory.appendingPathComponent(name + ".png"), options: .atomic)
        }
        try Data("Deterministic samples of the actual CALayer/CAGradientLayer tree with synthetic meter values and explicit shimmer positions. This is not live microphone or presentation-animation capture. No hotkeys, microphone, network or visible window.\n".utf8)
            .write(to: directory.appendingPathComponent("LIMITATIONS.txt"), options: .atomic)
        return samples.count
    }
}
