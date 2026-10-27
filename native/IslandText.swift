import SwiftUI

private struct IncomingWords: TextAttribute {}

private final class IslandTextAnimation: ObservableObject {
    @Published var previous = ""
    @Published var arrival = 1.0
    var revision = 0
}

struct IslandTextParts {
    let stable: String
    let incoming: String

    static func changed(from previous: String, to current: String) -> IslandTextParts {
        guard previous != current, !previous.hasPrefix(current) else {
            return IslandTextParts(stable: current, incoming: "")
        }
        let count = zip(previous, current).prefix { $0 == $1 }.count
        let commonEnd = current.index(current.startIndex, offsetBy: count)
        let split: String.Index
        if count == previous.count, commonEnd < current.endIndex, current[commonEnd].isWhitespace {
            split = commonEnd
        } else if let space = current[..<commonEnd].lastIndex(where: { $0.isWhitespace }) {
            split = current.index(after: space)
        } else {
            split = current.startIndex
        }
        return IslandTextParts(stable: String(current[..<split]), incoming: String(current[split...]))
    }
}

private struct IslandWordRenderer: TextRenderer {
    var progress: Double
    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }
    var displayPadding: EdgeInsets {
        EdgeInsets(top: 0, leading: 0, bottom: IslandDesign.wordArrivalRise, trailing: 0)
    }

    func draw(layout: Text.Layout, in context: inout GraphicsContext) {
        context.drawLayer { layer in
            for line in layout {
                for run in line {
                    var word = layer
                    if run[IncomingWords.self] != nil {
                        word.opacity *= progress
                        word.translateBy(x: 0, y: IslandDesign.wordArrivalRise * (1 - progress))
                    }
                    word.draw(run)
                }
            }
            if layout.isTruncated, let first = layout.first {
                let bounds = layout.dropFirst().reduce(first.typographicBounds.rect) {
                    $0.union($1.typographicBounds.rect)
                }
                let fade = min(1, IslandDesign.transcriptFadeWidth / max(1, bounds.width))
                layer.blendMode = .destinationIn
                layer.fill(Path(bounds.insetBy(dx: 0, dy: -IslandDesign.wordArrivalRise)),
                           with: .linearGradient(Gradient(stops: [
                            .init(color: .clear, location: 0),
                            .init(color: .black, location: fade),
                            .init(color: .black, location: 1)
                           ]), startPoint: CGPoint(x: bounds.minX, y: 0),
                           endPoint: CGPoint(x: bounds.maxX, y: 0)))
            }
        }
    }
}

struct IslandText: View {
    let text: String
    let secondary: Bool
    var animatesWords = false
    var showsCaret = false
    var maximumLines: Int?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var animation = IslandTextAnimation()

    var body: some View {
        let parts = animatesWords ? IslandTextParts.changed(from: animation.previous, to: text)
                                 : IslandTextParts(stable: text, incoming: "")
        let stable = Text(verbatim: parts.stable)
        let incoming = Text(verbatim: parts.incoming).customAttribute(IncomingWords())
        Text("\(stable)\(incoming)")
            .font(.system(size: secondary ? IslandDesign.secondarySize : IslandDesign.bodySize,
                          weight: secondary ? .regular : .medium))
            .foregroundStyle(IslandDesign.ink.opacity(secondary ? 0.72 : 1))
            .lineLimit(maximumLines ?? (secondary ? 2 : 1))
            .truncationMode(.head)
            .textRenderer(IslandWordRenderer(progress: reduceMotion ? 1 : animation.arrival))
            .padding(.trailing, showsCaret ? IslandDesign.caretGap + IslandDesign.caretWidth : 0)
            .overlay(alignment: .trailing) {
                if showsCaret {
                    TimelineView(.periodic(from: .distantPast, by: IslandDesign.caretPeriod / 2)) { tick in
                        let visible = reduceMotion ||
                            Int(tick.date.timeIntervalSinceReferenceDate / (IslandDesign.caretPeriod / 2)).isMultiple(of: 2)
                        Rectangle().fill(IslandDesign.teal)
                            .frame(width: IslandDesign.caretWidth, height: IslandDesign.bodySize)
                            .opacity(visible ? 1 : 0)
                    }
                    .accessibilityHidden(true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(text)
            .onAppear { animation.previous = text }
            .onChange(of: text) { old, _ in
                animation.previous = old
                guard animatesWords, !reduceMotion else { animation.arrival = 1; return }
                animation.revision += 1
                let current = animation.revision
                animation.arrival = 0
                DispatchQueue.main.async {
                    guard animation.revision == current else { return }
                    withAnimation(.easeOut(duration: IslandDesign.motionDuration(IslandDesign.wordArrivalDuration))) {
                        animation.arrival = 1
                    }
                }
            }
    }
}
