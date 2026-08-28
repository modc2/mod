import Foundation

/// Describes a jcode server gateway endpoint.
///
/// The server exposes:
/// - `GET  http://host:port/health`  reachability + version probe
/// - `POST http://host:port/pair`    pairing-code -> auth-token exchange
/// - `ws://host:port/ws`             newline-delimited JSON protocol over WebSocket
public struct Gateway: Hashable, Sendable {
    public static let defaultPort: UInt16 = 7643

    public var host: String
    public var port: UInt16

    public init(host: String, port: UInt16 = Gateway.defaultPort) {
        self.host = host
        self.port = port
    }

    public var healthURL: URL {
        url(scheme: "http", path: "/health")
    }

    public var pairURL: URL {
        url(scheme: "http", path: "/pair")
    }

    public var webSocketURL: URL {
        url(scheme: "ws", path: "/ws")
    }

    private func url(scheme: String, path: String) -> URL {
        var components = URLComponents()
        components.scheme = scheme
        components.host = host
        components.port = Int(port)
        components.path = path
        guard let url = components.url else {
            // Host strings from pairing are validated before reaching here;
            // a failure indicates programmer error.
            preconditionFailure("invalid gateway endpoint: \(scheme)://\(host):\(port)\(path)")
        }
        return url
    }
}

/// Parses `jcode://pair?host=H&port=P&code=C` URIs from QR codes.
public enum PairURI {
    public struct Payload: Equatable, Sendable {
        public var gateway: Gateway
        public var code: String

        public init(gateway: Gateway, code: String) {
            self.gateway = gateway
            self.code = code
        }
    }

    public static func parse(_ string: String) -> Payload? {
        guard let components = URLComponents(string: string),
            components.scheme == "jcode",
            components.host == "pair" || components.path == "pair"
                || components.host == nil && components.path == "/pair"
        else { return nil }
        let items = components.queryItems ?? []
        func value(_ name: String) -> String? {
            items.first(where: { $0.name == name })?.value
        }
        guard let host = value("host"), !host.isEmpty,
            let code = value("code"), !code.isEmpty
        else { return nil }
        let port = value("port").flatMap(UInt16.init) ?? Gateway.defaultPort
        return Payload(gateway: Gateway(host: host, port: port), code: code)
    }
}
