import JCodeKit
import SwiftUI

/// First-run pairing: scan QR or type host/port/code.
struct PairingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var host = ""
    @State private var port = String(Gateway.defaultPort)
    @State private var code = ""
    @State private var isPairing = false
    @State private var errorMessage: String?
    @State private var showScanner = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                if let errorMessage {
                    ErrorBanner(message: errorMessage) {
                        self.errorMessage = nil
                    }
                    .padding(.horizontal, -16)
                }

                Card {
                    VStack(alignment: .leading, spacing: 16) {
                        field("Host", text: $host, placeholder: "devbox.tailnet.ts.net")
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        field("Port", text: $port, placeholder: "7643")
                            .keyboardType(.numberPad)
                        field("Pairing code", text: $code, placeholder: "123456")
                            .keyboardType(.numberPad)
                    }
                }

                Button(action: pair) {
                    HStack {
                        if isPairing && !reduceMotion {
                            ProgressView().tint(.black)
                        }
                        Text(isPairing ? "Pairing..." : "Pair")
                            .font(.headline)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(canPair ? Theme.mint : Theme.surfaceElevated)
                    .foregroundStyle(canPair ? .black : Theme.textTertiary)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .disabled(!canPair || isPairing)
                .accessibilityLabel("Pair")
                .accessibilityHint("Connects using the host, port, and code above")

                Button {
                    showScanner = true
                } label: {
                    Label("Scan QR from `jcode pair`", systemImage: "qrcode.viewfinder")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Theme.surface)
                        .foregroundStyle(Theme.textPrimary)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14)
                                .stroke(Theme.border, lineWidth: 1)
                        )
                }
                .accessibilityLabel("Scan QR code")
                .accessibilityHint("Opens the camera to scan a pairing code")

                Text("Run `jcode pair` on your machine, then scan the QR code or enter the code manually. Traffic stays on your tailnet.")
                    .font(.footnote)
                    .foregroundStyle(Theme.textTertiary)
            }
            .padding(16)
        }
        .scrollDismissesKeyboard(.interactively)
        .dynamicTypeSize(.large ... .accessibility3)
        .sheet(isPresented: $showScanner) {
            QRScannerView { scanned in
                showScanner = false
                if let payload = PairURI.parse(scanned) {
                    host = payload.gateway.host
                    port = String(payload.gateway.port)
                    code = payload.code
                    pair()
                } else {
                    errorMessage = "Not a jcode pairing QR code"
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("jcode")
                .font(Theme.mono(34, weight: .bold))
                .foregroundStyle(Theme.textPrimary)
            Text("Pair with a server on your tailnet")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
        }
        .padding(.top, 32)
    }

    private var canPair: Bool {
        !host.trimmingCharacters(in: .whitespaces).isEmpty && !code.isEmpty
            && UInt16(port) != nil
    }

    private func field(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Theme.textTertiary)
            TextField(placeholder, text: text)
                .font(Theme.mono(16))
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.surfaceElevated)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .accessibilityLabel(label)
        }
    }

    private func pair() {
        guard let portValue = UInt16(port) else { return }
        let gateway = Gateway(host: host.trimmingCharacters(in: .whitespaces), port: portValue)
        let pairCode = code
        isPairing = true
        errorMessage = nil
        Task {
            defer { isPairing = false }
            do {
                try await model.pair(
                    gateway: gateway,
                    code: pairCode,
                    deviceName: UIDevice.current.name
                )
            } catch let error as PairingClient.PairingError {
                switch error {
                case .invalidCode(let message):
                    errorMessage = message
                case .serverError(_, let message):
                    errorMessage = message
                case .invalidResponse:
                    errorMessage = "Unexpected response from server"
                }
            } catch {
                errorMessage = "Could not reach \(gateway.host):\(gateway.port)"
            }
        }
    }
}
