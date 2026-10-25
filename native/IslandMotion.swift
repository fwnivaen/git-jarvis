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
    static let expansionDuration = 0.42
    static let collapseDuration = 0.30
    static let rowInDuration = 0.18
    static let rowOutDuration = 0.10
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
