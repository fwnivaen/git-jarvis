import AppKit
import Metal
import SwiftUI

final class IslandViewModel: ObservableObject {
    @Published var display = IslandDisplay(phase: .hidden, title: "Jarvis", detail: "",
                                           wordIndex: nil, microphoneLevel: 0)
    @Published var hasNotch = false
    @Published var notchHeight: CGFloat = 0
    @Published var hover = false
    var review: () -> Void = {}
}

struct IslandShape: Shape {
    var bottomRadius: CGFloat
    var topRadius: CGFloat

    func path(in rect: CGRect) -> Path {
        let top = min(topRadius, rect.height / 2)
        let bottom = min(bottomRadius, rect.height / 2)
        var path = Path()
        path.move(to: CGPoint(x: rect.minX + top, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - top, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY + top),
                          control: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - bottom))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - bottom, y: rect.maxY),
                          control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + bottom, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - bottom),
                          control: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + top))
        path.addQuadCurve(to: CGPoint(x: rect.minX + top, y: rect.minY),
                          control: CGPoint(x: rect.minX, y: rect.minY))
        path.closeSubpath()
        return path
    }
}

struct IslandView: View {
    @ObservedObject var model: IslandViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorSchemeContrast) private var contrast

    private var display: IslandDisplay { model.display }
    private var accent: Color { IslandDesign.accent(for: display.phase) }

    var body: some View {
        let expanded = display.isExpanded
        let bottom = expanded ? IslandDesign.expandedBottomRadius :
            display.phase == .notification || display.phase == .done ? IslandDesign.peekBottomRadius :
            IslandDesign.compactBottomRadius
        HStack(spacing: IslandDesign.spacing[2]) {
            if reduceMotion || MTLCreateSystemDefaultDevice() == nil ||
                CommandLine.arguments.contains("--snapshot-island") {
                MiniOrb(phase: display.phase, level: display.microphoneLevel,
                        size: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb)
            } else {
                MetalOrb(phase: display.phase, level: display.microphoneLevel,
                         size: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb,
                         expanded: expanded)
                    .frame(width: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb,
                           height: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: IslandDesign.spacing[0]) {
                IslandText(text: display.title, secondary: false)
                if expanded || display.phase == .notification || display.phase == .done {
                    IslandText(text: display.phase == .speaking
                        ? SpokenWords.centeredText(display.detail, activeWord: display.wordIndex)
                        : display.detail, secondary: true)
                }
            }
            if display.phase == .approval {
                Button("Review ↗", action: model.review)
                    .buttonStyle(.plain)
                    .font(.system(size: IslandDesign.secondarySize, weight: .semibold))
                    .foregroundStyle(IslandDesign.amber)
                    .accessibilityHint("Opens the Jarvis dashboard to review this request")
            }
        }
        .padding(.horizontal, expanded ? IslandDesign.spacing[4] : IslandDesign.spacing[3])
        .padding(.top, model.hasNotch ? model.notchHeight : IslandDesign.spacing[0])
        .padding(.bottom, expanded ? IslandDesign.spacing[2] : IslandDesign.spacing[0])
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IslandShape(bottomRadius: bottom, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet)
            .fill(.black))
        .overlay(IslandShape(bottomRadius: bottom, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet)
            .stroke(accent.opacity(contrast == .increased ? IslandDesign.highContrastKeylineOpacity
                                                         : IslandDesign.keylineOpacity), lineWidth: 1))
        .shadow(color: .black.opacity(IslandDesign.shadowOpacity), radius: IslandDesign.shadowRadius,
                y: IslandDesign.shadowYOffset)
        .scaleEffect(model.hover && !reduceMotion ? IslandDesign.hoverScale : 1, anchor: .top)
        .animation(reduceMotion ? nil : .easeOut(duration: IslandDesign.rowInDuration), value: display.phase)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Jarvis, \(display.title), \(display.detail)")
    }
}
