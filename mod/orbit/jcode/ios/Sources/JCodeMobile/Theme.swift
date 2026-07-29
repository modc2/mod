import SwiftUI

/// Design tokens. Dark, calm, terminal-native; mint accent for live state.
enum Theme {
    static let background = Color(hex: 0x0F0F14)
    static let surface = Color(hex: 0x1A1A1F)
    static let surfaceElevated = Color(hex: 0x242429)
    static let border = Color.white.opacity(0.08)
    static let mint = Color(hex: 0x4DD9A6)
    static let mintTint = Color(hex: 0x4DD9A6).opacity(0.15)
    static let textPrimary = Color.white.opacity(0.92)
    static let textSecondary = Color.white.opacity(0.55)
    static let textTertiary = Color.white.opacity(0.35)
    static let warning = Color(hex: 0xF59E0B)
    static let error = Color(hex: 0xD94D59)

    static func mono(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }

    /// Decorative icon font (SF Symbols) at a fixed point size.
    static func icon(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0
        )
    }
}

/// Extra edge padding for chrome pinned to an edge with no system inset.
///
/// Home-button devices (iPhone SE class) report a zero bottom safe-area inset,
/// so edge-pinned chrome needs explicit breathing room there; Dynamic Island
/// devices already get it from the system insets. Derived from the root
/// GeometryReader in RootView and injected via the environment: reading
/// UIKit window insets during a SwiftUI body evaluation creates an
/// AttributeGraph cycle that corrupts view-hierarchy updates.
struct CompactEdgePads: Equatable {
    var top: CGFloat = 0
    var bottom: CGFloat = 0

    /// Derives the pads from the container's safe-area insets.
    init(safeArea: EdgeInsets) {
        top = safeArea.top < 24 ? 12 : 0
        bottom = safeArea.bottom > 0 ? 0 : 12
    }

    init() {}
}

extension EnvironmentValues {
    @Entry var compactEdgePads = CompactEdgePads()
}

/// Card container used across screens.
struct Card<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Theme.border, lineWidth: 1)
            )
    }
}
