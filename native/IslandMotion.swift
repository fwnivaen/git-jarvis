import AppKit
import SwiftUI

enum IslandDesign {
    static let spacing: [CGFloat] = [4, 8, 12, 16, 24]
    static let topFillet: CGFloat = 7
    static let compactBottomRadius: CGFloat = 11
    static let peekBottomRadius: CGFloat = 14
    static let expandedBottomRadius: CGFloat = 28
    static let compactEar: CGFloat = 40
    static let peekEarMinimum: CGFloat = 64
    static let peekEarMaximum: CGFloat = 140
    static let expandedWidth: CGFloat = 466
    static let compactWidth: CGFloat = 176
    static let compactPillHeight: CGFloat = 30
    static let peekContentHeight: CGFloat = 44
    static let compactOrb: CGFloat = 22
    static let expandedOrb: CGFloat = 44
    static let panelMargin: CGFloat = 24
    static let expandedOneRowExtra: CGFloat = 52
    static let expandedTwoRowExtra: CGFloat = 76
    static let bodySize: CGFloat = 15
    static let secondarySize: CGFloat = 12
    static let labelSize: CGFloat = 10.5
    static let keylineOpacity = 0.26
    static let highContrastKeylineOpacity = 0.60
    static let shadowOpacity = 0.35
    static let shadowRadius: CGFloat = 14
    static let shadowYOffset: CGFloat = 6
    static let bloomOpacity = 0.08
    static let bloomRadius: CGFloat = 12
    static let expansionDuration = 0.42
    static let expansionDamping = 0.80
    static let geometryFramesPerSecond = 60.0
    static let collapseDuration = 0.30
    static let collapseContentFade = 0.12
    static let rowInDelay = 0.06
    static let rowInDuration = 0.18
    static let rowOutDuration = 0.10
    static let wordArrivalDuration = 0.16
    static let wordArrivalRise: CGFloat = 2
    static let transcriptFadeWidth: CGFloat = 20
    static let caretWidth: CGFloat = 1.5
    static let caretGap: CGFloat = 3
    static let caretPeriod = 1.0
    static let defaultSlowMotion = 3.0
    static let maximumSlowMotion = 10.0
    // "1" enables the standard slow demonstration. Other positive numeric
    // values specify a multiplier. Protocol and request deadlines stay real.
    static let slowMotion: Double = {
        guard let value = ProcessInfo.processInfo.environment["JARVIS_ISLAND_SLOWMO"] else { return 1 }
        if value == "1" || value.lowercased() == "true" { return defaultSlowMotion }
        guard let factor = Double(value), factor.isFinite, factor > 0 else { return 1 }
        return min(maximumSlowMotion, max(1, factor))
    }()
    static func motionDuration(_ seconds: TimeInterval) -> TimeInterval { seconds * slowMotion }

    static func geometryProgress(_ value: Double, expanding: Bool) -> Double {
        let t = min(1, max(0, value))
        guard expanding else { return t * t * (3 - 2 * t) }
        let damping = expansionDamping
        let frequency = 2 * Double.pi
        let damped = sqrt(1 - damping * damping)
        func response(_ time: Double) -> Double {
            1 - exp(-damping * frequency * time) *
                (cos(frequency * damped * time) + damping / damped * sin(frequency * damped * time))
        }
        return response(t) / response(1)
    }
    static let hoverScale: CGFloat = 1.028
    static let pressScale: CGFloat = 0.97
    static let meterFloorDB = -50.0
    static let meterCeilingDB = -14.0
    static let meterAttack = 0.03
    static let meterRelease = 0.18

    static let ink = Color(red: 238/255, green: 246/255, blue: 244/255)
    static let teal = Color(red: 94/255, green: 234/255, blue: 212/255)
    static let violet = Color(red: 154/255, green: 140/255, blue: 255/255)
    static let amber = Color(red: 240/255, green: 180/255, blue: 110/255)
    static let error = Color(red: 255/255, green: 122/255, blue: 122/255)
    static let errorGray = Color(red: 125/255, green: 138/255, blue: 144/255)

    static func accent(for phase: IslandPhase) -> Color {
        switch phase {
        case .thinking, .working: return violet
        case .approval: return amber
        case .error, .microphoneFailure, .permissionDenied, .offline: return error
        default: return teal
        }
    }
}

// Both live panels and static fixtures use this geometry, including content-
// sized peek ears. A snapshot must not substitute compact sizing for a peek.
extension IslandDisplay {
    var isPeek: Bool { [.done, .notification, .releasedEarly, .offline].contains(phase) }
}

enum IslandLayout {
    static func size(for display: IslandDisplay, hasNotch: Bool, notchHeight: CGFloat,
                     notchWidth: CGFloat, availableWidth: CGFloat) -> NSSize {
        let notch = hasNotch ? notchHeight : 0
        let compact = hasNotch ? notchWidth + 2 * IslandDesign.compactEar : IslandDesign.compactWidth
        let content = display.detail.isEmpty ? display.title : display.detail
        let textWidth = (content as NSString).size(withAttributes: [
            .font: NSFont.systemFont(ofSize: IslandDesign.secondarySize)
        ]).width
        let ear = min(IslandDesign.peekEarMaximum, max(IslandDesign.peekEarMinimum,
            (textWidth + IslandDesign.compactOrb + IslandDesign.spacing[4]) / 2))
        let width = display.isExpanded ? IslandDesign.expandedWidth :
            display.isPeek ? (hasNotch ? notchWidth : 0) + 2 * ear : compact
        let extra = display.isExpanded ?
            (display.detail.isEmpty ? IslandDesign.expandedOneRowExtra : IslandDesign.expandedTwoRowExtra) :
            display.isPeek ? IslandDesign.peekContentHeight : IslandDesign.compactPillHeight
        return NSSize(width: min(width, max(1, availableWidth)), height: notch + extra)
    }
}
