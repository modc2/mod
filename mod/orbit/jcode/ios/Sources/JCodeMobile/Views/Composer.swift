import SwiftUI

/// Message composer with send/interrupt.
struct Composer: View {
    @Environment(\.compactEdgePads) private var edgePads
    @Binding var draft: String
    let isProcessing: Bool
    let isConnected: Bool
    let onSend: () -> Void
    let onInterrupt: () -> Void

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(
                isProcessing ? "Queue a message..." : "Message",
                text: $draft,
                axis: .vertical
            )
            .lineLimit(1...6)
            .font(.body)
            .foregroundStyle(Theme.textPrimary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 20))
            .overlay(
                RoundedRectangle(cornerRadius: 20)
                    .stroke(Theme.border, lineWidth: 1)
            )

            if isProcessing {
                Button(action: onInterrupt) {
                    Image(systemName: "stop.fill")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.error)
                        .frame(width: 44, height: 44)
                        .background(Theme.surface)
                        .clipShape(Circle())
                }
                .accessibilityLabel("Stop")
                .accessibilityHint("Interrupt the current response")
            }

            Button(action: onSend) {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.bold))
                    .foregroundStyle(isConnected ? .black : Theme.textSecondary)
                    .frame(width: 44, height: 44)
                    .background(isConnected ? Theme.mint : Theme.surfaceElevated)
                    .clipShape(Circle())
            }
            .disabled(!canSend)
            .accessibilityLabel(isProcessing ? "Queue message" : "Send message")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .padding(.bottom, edgePads.bottom)
        .background(Theme.background)
    }

    private var canSend: Bool {
        isConnected && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
