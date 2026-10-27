import AppKit
import SwiftUI

final class IslandViewModel: ObservableObject {
    @Published var display = IslandDisplay(phase: .hidden, title: "Jarvis", detail: "",
                                           wordIndex: nil, microphoneLevel: 0)
    @Published var hasNotch = false
    @Published var notchHeight: CGFloat = 0
    @Published var hover = false
    @Published var effectsActive = true
    let layerMeter = IslandLayerMeter()
    var evidenceRendering = false
    var review: () -> Void = {}
}

struct IslandShape: Shape {
    var bottomRadius: CGFloat
    var topRadius: CGFloat
    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(bottomRadius, topRadius) }
        set { bottomRadius = newValue.first; topRadius = newValue.second }
    }

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
    @GestureState private var pressed = false

    private var display: IslandDisplay { model.display }
    private var accent: Color { IslandDesign.accent(for: display.phase) }

    var body: some View {
        let expanded = display.isExpanded
        let bottom = expanded ? IslandDesign.expandedBottomRadius :
            display.isPeek ? IslandDesign.peekBottomRadius :
            IslandDesign.compactBottomRadius
        HStack(spacing: IslandDesign.spacing[2]) {
            if reduceMotion || model.evidenceRendering || CommandLine.arguments.contains("--snapshot-island") {
                MiniOrb(phase: display.phase, level: display.microphoneLevel,
                        size: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb)
            } else {
                MetalOrb(phase: display.phase, level: display.microphoneLevel,
                         size: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb,
                         expanded: expanded, hovering: model.hover)
                    .frame(width: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb,
                           height: expanded ? IslandDesign.expandedOrb : IslandDesign.compactOrb)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: IslandDesign.spacing[0]) {
                IslandText(text: display.title, secondary: false,
                           animatesWords: display.phase == .listening,
                           showsCaret: display.phase == .listening)
                if expanded || display.isPeek {
                    IslandText(text: display.phase == .speaking || display.phase == .mutedReply
                        ? SpokenWords.centeredText(display.detail, activeWord: display.wordIndex)
                        : display.detail, secondary: true, maximumLines: expanded ? 2 : 1)
                        .id(display.phase)
                        .transition(.asymmetric(
                            insertion: .opacity.animation(.easeOut(duration: IslandDesign.motionDuration(IslandDesign.rowInDuration))
                                .delay(IslandDesign.motionDuration(IslandDesign.rowInDelay))),
                            removal: .opacity.animation(.easeOut(duration: IslandDesign.motionDuration(IslandDesign.rowOutDuration)))))
                }
            }
            .padding(.trailing, display.phase == .listening ? IslandLayerDesign.barsWidth + IslandDesign.spacing[0] : 0)
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
        .padding(.bottom, expanded ? IslandDesign.spacing[1] : IslandDesign.spacing[0])
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IslandShape(bottomRadius: bottom, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet)
            .fill(.black)
            .shadow(color: accent.opacity(IslandDesign.bloomOpacity), radius: IslandDesign.bloomRadius))
        .overlay {
            IslandActivityLayers(phase: model.effectsActive ? display.phase : .hidden, meter: model.layerMeter,
                                 expanded: expanded, notchHeight: model.notchHeight,
                                 reduceMotion: reduceMotion)
                .clipShape(IslandShape(bottomRadius: bottom, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet))
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .overlay(IslandShape(bottomRadius: bottom, topRadius: model.hasNotch ? 0 : IslandDesign.topFillet)
            .stroke(accent.opacity(contrast == .increased ? IslandDesign.highContrastKeylineOpacity
                                                         : IslandDesign.keylineOpacity), lineWidth: 1))
        .shadow(color: .black.opacity(IslandDesign.shadowOpacity), radius: IslandDesign.shadowRadius,
                y: IslandDesign.shadowYOffset)
        .scaleEffect(reduceMotion ? 1 : (pressed ? IslandDesign.pressScale :
            model.hover ? IslandDesign.hoverScale : 1), anchor: .top)
        .animation(reduceMotion ? nil : .easeOut(duration: IslandDesign.motionDuration(IslandDesign.rowInDuration)), value: display.phase)
        .animation(reduceMotion ? nil : .easeOut(duration: IslandDesign.motionDuration(IslandDesign.rowOutDuration)), value: pressed)
        .simultaneousGesture(DragGesture(minimumDistance: 0).updating($pressed) { _, state, _ in state = true })
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Jarvis, \(display.title), \(display.detail)")
    }
}
