import SwiftUI

struct MiniOrb: View {
    let phase: IslandPhase
    let level: Double
    let size: CGFloat

    var body: some View {
        let color = IslandDesign.accent(for: phase)
        Canvas { context, canvasSize in
            let bounds = CGRect(origin: .zero, size: canvasSize)
            let inset = CGFloat(2 + 2 * level)
            context.fill(Path(ellipseIn: bounds.insetBy(dx: inset, dy: inset)),
                         with: .radialGradient(Gradient(colors: [color.opacity(0.95), color.opacity(0.35), .black]),
                                               center: CGPoint(x: bounds.midX, y: bounds.midY),
                                               startRadius: 0, endRadius: bounds.width / 2))
            context.stroke(Path(ellipseIn: bounds.insetBy(dx: 1, dy: 1)),
                           with: .color(color.opacity(0.55)), lineWidth: 1)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
