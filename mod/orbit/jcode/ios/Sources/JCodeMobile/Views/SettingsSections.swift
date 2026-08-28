import JCodeKit
import SwiftUI

/// Sessions, servers, and info sections, split out to keep view files small.
struct SettingsSessionsSection: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Binding var renameDraft: String
    @Binding var showRename: Bool

    var body: some View {
        Section("Sessions") {
            ForEach(model.session.allSessions, id: \.self) { sessionID in
                sessionRow(sessionID)
            }
            Button {
                renameDraft = model.session.sessionTitle ?? ""
                showRename = true
            } label: {
                Label("Rename current session", systemImage: "pencil")
                    .foregroundStyle(Theme.textPrimary)
            }
            .listRowBackground(Theme.surface)
            .accessibilityHint("Opens a field to rename the active session")
            Button {
                model.compactConversation()
            } label: {
                Label("Compact conversation", systemImage: "arrow.down.right.and.arrow.up.left")
                    .foregroundStyle(Theme.textPrimary)
            }
            .listRowBackground(Theme.surface)
            .accessibilityHint("Summarizes older messages to free context")
            Button {
                model.clearConversation()
                dismiss()
            } label: {
                Label("New session (clear)", systemImage: "square.and.pencil")
                    .foregroundStyle(Theme.mint)
            }
            .listRowBackground(Theme.surface)
            .accessibilityHint("Clears the conversation and starts fresh")
        }
    }

    private func sessionRow(_ sessionID: String) -> some View {
        let isActive = sessionID == model.session.sessionID
        let title = model.session.title(forSession: sessionID)
        return Button {
            model.switchSession(sessionID)
            dismiss()
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    if let title {
                        Text(title)
                            .font(.body)
                            .foregroundStyle(Theme.textPrimary)
                            .lineLimit(1)
                    }
                    Text(shortSessionID(sessionID))
                        .font(Theme.mono(title == nil ? 13 : 11))
                        .foregroundStyle(title == nil ? Theme.textPrimary : Theme.textTertiary)
                        .lineLimit(1)
                }
                Spacer()
                if isActive {
                    Image(systemName: "checkmark")
                        .font(.caption)
                        .foregroundStyle(Theme.mint)
                        .accessibilityHidden(true)
                }
            }
        }
        .listRowBackground(Theme.surface)
        .accessibilityLabel("Session \(title ?? shortSessionID(sessionID))")
        .accessibilityValue(isActive ? "Current" : "")
        .accessibilityHint("Switches to this session")
        .accessibilityAddTraits(isActive ? [.isSelected] : [])
    }

    private func shortSessionID(_ id: String) -> String {
        if id.count > 24 {
            return String(id.prefix(24)) + "…"
        }
        return id
    }
}

struct SettingsServersSection: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Binding var showPairNew: Bool

    var body: some View {
        Section("Servers") {
            ForEach(model.servers) { server in
                let isActive = server.id == model.activeServer?.id
                Button {
                    model.connect(to: server)
                    dismiss()
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(server.serverName)
                                .font(.body)
                                .foregroundStyle(Theme.textPrimary)
                            Text("\(server.host):\(String(server.port))")
                                .font(Theme.mono(11))
                                .foregroundStyle(Theme.textTertiary)
                        }
                        Spacer()
                        if isActive {
                            Circle()
                                .fill(Theme.mint)
                                .frame(width: 8, height: 8)
                                .accessibilityHidden(true)
                        }
                    }
                }
                .listRowBackground(Theme.surface)
                .accessibilityLabel(server.serverName)
                .accessibilityValue(isActive ? "Connected" : "")
                .accessibilityHint("Connects to this server")
                .accessibilityAddTraits(isActive ? [.isSelected] : [])
                .swipeActions {
                    Button(role: .destructive) {
                        model.removeServer(server)
                    } label: {
                        Label("Remove", systemImage: "trash")
                    }
                }
            }
            Button {
                showPairNew = true
            } label: {
                Label("Pair new server", systemImage: "plus")
                    .foregroundStyle(Theme.mint)
            }
            .listRowBackground(Theme.surface)
            .accessibilityHint("Opens pairing to add a server")
        }
    }
}

struct SettingsInfoSection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Section("Info") {
            row("Server version", model.session.serverVersion ?? "unknown")
            row("Provider", model.session.providerName ?? "unknown")
            row(
                "Tokens",
                "\(model.session.tokenInput) in / \(model.session.tokenOutput) out"
            )
            if let detail = model.session.statusDetail {
                row("Status", detail)
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
                .font(.callout)
                .foregroundStyle(Theme.textSecondary)
            Spacer()
            Text(value)
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textTertiary)
                .lineLimit(1)
        }
        .listRowBackground(Theme.surface)
        .accessibilityElement(children: .combine)
    }
}
