import AppKit
import MetalKit
import SwiftUI

enum OrbDesign {
    static let transitionFPS = 60
    static let expandedRestFPS = 30
    static let compactRestFPS = 24
    static let transitionHold: TimeInterval = 0.6

    static func framesPerSecond(expanded: Bool, hovering: Bool, transitioning: Bool) -> Int {
        if hovering || transitioning { return transitionFPS }
        return expanded ? expandedRestFPS : compactRestFPS
    }

    static let shader = """
    #include <metal_stdlib>
    using namespace metal;
    struct OrbVertex { float4 position [[position]]; float2 uv; };
    struct OrbUniforms { float time; float level; float2 size; float4 tint; };
    vertex OrbVertex orbVertex(uint id [[vertex_id]]) {
        float2 p = float2((id == 2) ? 3.0 : -1.0, (id == 1) ? 3.0 : -1.0);
        OrbVertex out;
        out.position = float4(p, 0.0, 1.0);
        out.uv = p * 0.5 + 0.5;
        return out;
    }
    fragment float4 orbFragment(OrbVertex in [[stage_in]], constant OrbUniforms &u [[buffer(0)]]) {
        float2 p = (in.uv - 0.5) * 2.0;
        float r = length(p);
        float angle = atan2(p.y, p.x);
        float swirl = sin(angle * 5.0 + u.time * 1.3 + r * 10.0) * 0.035;
        float pulse = 0.025 * sin(u.time * 2.2) + u.level * 0.045;
        float body = smoothstep(0.96 + swirl + pulse, 0.27, r);
        float ring = exp(-pow((r - 0.83 - pulse) * 16.0, 2.0)) * 0.38;
        float core = exp(-r * r * 7.5);
        float glimmer = 0.12 * sin(angle * 3.0 - u.time * 1.8 + r * 8.0);
        float3 color = u.tint.rgb * (0.22 + core * 0.72 + ring + glimmer * body);
        float alpha = clamp(body * 0.92 + ring, 0.0, 1.0);
        return float4(color, alpha);
    }
    """
}

final class OrbRenderer: NSObject, MTKViewDelegate {
    private let commandQueue: MTLCommandQueue
    private let pipeline: MTLRenderPipelineState
    private let started = Date()
    private var transitionUntil = Date()
    private var phase: IslandPhase = .ready
    private var level = 0.0
    private var expanded = false
    private var hovering = false

    struct Uniforms {
        var time: Float
        var level: Float
        var size: SIMD2<Float>
        var tint: SIMD4<Float>
    }

    init?(view: MTKView) {
        guard let device = view.device,
              let commandQueue = device.makeCommandQueue(),
              let library = try? device.makeLibrary(source: OrbDesign.shader, options: nil),
              let vertex = library.makeFunction(name: "orbVertex"),
              let fragment = library.makeFunction(name: "orbFragment") else { return nil }
        let descriptor = MTLRenderPipelineDescriptor()
        descriptor.vertexFunction = vertex
        descriptor.fragmentFunction = fragment
        descriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
        descriptor.colorAttachments[0].isBlendingEnabled = true
        descriptor.colorAttachments[0].sourceRGBBlendFactor = .sourceAlpha
        descriptor.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
        descriptor.colorAttachments[0].sourceAlphaBlendFactor = .one
        descriptor.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
        guard let pipeline = try? device.makeRenderPipelineState(descriptor: descriptor) else { return nil }
        self.commandQueue = commandQueue
        self.pipeline = pipeline
        super.init()
    }

    func update(phase: IslandPhase, level: Double, expanded: Bool,
                hovering: Bool = false, view: MTKView) {
        if self.phase != phase || self.expanded != expanded || self.hovering != hovering {
            self.phase = phase
            transitionUntil = Date().addingTimeInterval(OrbDesign.transitionHold)
        }
        self.level = level
        self.expanded = expanded
        self.hovering = hovering
        refreshFrameRate(view)
    }

    private func refreshFrameRate(_ view: MTKView) {
        let target = OrbDesign.framesPerSecond(expanded: expanded, hovering: hovering,
                                              transitioning: Date() < transitionUntil)
        if view.preferredFramesPerSecond != target { view.preferredFramesPerSecond = target }
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

    func draw(in view: MTKView) {
        refreshFrameRate(view)
        guard let descriptor = view.currentRenderPassDescriptor,
              let drawable = view.currentDrawable,
              let buffer = commandQueue.makeCommandBuffer(),
              let encoder = buffer.makeRenderCommandEncoder(descriptor: descriptor) else { return }
        let tint: SIMD4<Float>
        switch phase {
        case .thinking, .working: tint = SIMD4(0.604, 0.549, 1, 1)
        case .approval: tint = SIMD4(0.941, 0.706, 0.431, 1)
        case .error, .microphoneFailure, .permissionDenied, .offline:
            tint = SIMD4(0.49, 0.54, 0.56, 1)
        default: tint = SIMD4(0.369, 0.918, 0.831, 1)
        }
        var uniforms = Uniforms(time: Float(Date().timeIntervalSince(started)),
                                level: Float(level),
                                size: SIMD2(Float(view.drawableSize.width), Float(view.drawableSize.height)),
                                tint: tint)
        encoder.setRenderPipelineState(pipeline)
        encoder.setFragmentBytes(&uniforms, length: MemoryLayout<Uniforms>.stride, index: 0)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        encoder.endEncoding()
        buffer.present(drawable)
        buffer.commit()
    }
}

private final class TransparentOrbMetalView: MTKView {
    override var isOpaque: Bool { false }
}

// The hosting view owns either a working renderer or the Canvas fallback.
// Selecting the fallback here also covers shader and pipeline failures after
// Metal device discovery has succeeded.
final class OrbHostView: NSView {
    private(set) var renderer: OrbRenderer?
    private(set) var metalView: MTKView?
    private(set) var fallbackView: NSHostingView<MiniOrb>?

    init(frame: NSRect, phase: IslandPhase, level: Double, size: CGFloat,
         expanded: Bool, hovering: Bool,
         rendererFactory: (MTKView) -> OrbRenderer? = { OrbRenderer(view: $0) }) {
        super.init(frame: frame)
        let metal = TransparentOrbMetalView(frame: bounds, device: MTLCreateSystemDefaultDevice())
        metal.colorPixelFormat = .bgra8Unorm
        metal.clearColor = MTLClearColorMake(0, 0, 0, 0)
        metal.framebufferOnly = true
        metal.enableSetNeedsDisplay = false
        metal.autoresizingMask = [.width, .height]
        if let renderer = rendererFactory(metal) {
            self.renderer = renderer
            metalView = metal
            metal.delegate = renderer
            metal.isPaused = false
            addSubview(metal)
        } else {
            metal.isPaused = true
            let fallback = NSHostingView(rootView: MiniOrb(phase: phase, level: level, size: size))
            fallback.frame = bounds
            fallback.autoresizingMask = [.width, .height]
            fallbackView = fallback
            addSubview(fallback)
        }
        update(phase: phase, level: level, size: size, expanded: expanded, hovering: hovering)
    }

    required init?(coder: NSCoder) { return nil }

    func update(phase: IslandPhase, level: Double, size: CGFloat,
                expanded: Bool, hovering: Bool) {
        if let metalView {
            renderer?.update(phase: phase, level: level, expanded: expanded,
                             hovering: hovering, view: metalView)
        } else {
            fallbackView?.rootView = MiniOrb(phase: phase, level: level, size: size)
        }
    }

    func stop() {
        metalView?.isPaused = true
        metalView?.delegate = nil
        renderer = nil
    }
}

struct MetalOrb: NSViewRepresentable {
    let phase: IslandPhase
    let level: Double
    let size: CGFloat
    let expanded: Bool
    let hovering: Bool

    func makeNSView(context: Context) -> OrbHostView {
        OrbHostView(frame: NSRect(x: 0, y: 0, width: size, height: size),
                    phase: phase, level: level, size: size, expanded: expanded, hovering: hovering)
    }

    func updateNSView(_ view: OrbHostView, context: Context) {
        view.update(phase: phase, level: level, size: size, expanded: expanded, hovering: hovering)
    }

    static func dismantleNSView(_ view: OrbHostView, coordinator: ()) {
        view.stop()
    }
}
