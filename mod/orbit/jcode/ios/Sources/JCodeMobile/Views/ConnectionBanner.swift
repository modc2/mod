import JCodeKit
import SwiftUI

/// Inline banner shown while the connection is down, with a manual retry.
///
/// The automatic reconnect loop keeps running underneath; the button just
/// short-circuits the backoff wait for impatient humans.
struct ConnectionBanner: View {
    let phase: ConnectionPhase
    let onRetry: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.slash")
                .font(.footnote)
                .foregroundStyle(Theme.warning)
                .accessibilityHidden(true)
            Text(label)
                .font(.footnote)
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(2)
            Spacer(minLength: 0)
            Button(action: onRetry) {
                Text("Retry")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.mint)
                    .frame(minWidth: 44, minHeight: 44)
            }
            .accessibilityLabel("Retry connection")
            .accessibilityHint("Reconnects to the server now")
        }
        .padding(.horizontal, 12)
        .background(Theme.warning.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Theme.warning.opacity(0.35), lineWidth: 1)
        )
        .padding(.horizontal)
        .accessibilityElement(children: .combine)
    }

    private var label: String {
        switch phase {
        case .reconnecting(let attempt):
            "Connection lost, retrying (attempt \(attempt))"
        case .failed:
            "Connection failed"
        default:
            "Offline"
        }
    }
}

/// Chip shown above the composer while soft-interrupt messages wait to be
/// injected into the running turn, with a cancel affordance.
struct QueuedInterruptChip: View {
    let count: Int
    let onCancel: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "clock")
                .font(.footnote)
                .foregroundStyle(Theme.mint)
                .accessibilityHidden(true)
            Text(count == 1 ? "1 message queued" : "\(count) messages queued")
                .font(.footnote)
                .foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 0)
            Button(action: onCancel) {
                Text("Cancel")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.error)
                    .frame(minWidth: 44, minHeight: 44)
            }
            .accessibilityLabel("Cancel queued messages")
            .accessibilityHint("Removes messages waiting to interrupt the response")
        }
        .padding(.horizontal, 12)
        .background(Theme.mintTint)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Theme.mint.opacity(0.35), lineWidth: 1)
        )
        .padding(.horizontal)
        .accessibilityElement(children: .combine)
    }
}
