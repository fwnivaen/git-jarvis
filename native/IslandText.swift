import SwiftUI

struct IslandText: View {
    let text: String
    let secondary: Bool

    var body: some View {
        Text(text)
            .font(.system(size: secondary ? IslandDesign.secondarySize : IslandDesign.bodySize,
                          weight: secondary ? .regular : .medium))
            .foregroundStyle(IslandDesign.ink.opacity(secondary ? 0.72 : 1))
            .lineLimit(secondary ? 2 : 1)
            .truncationMode(.head)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(text)
    }
}
