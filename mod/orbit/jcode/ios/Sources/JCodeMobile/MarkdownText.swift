import SwiftUI

/// Lightweight markdown renderer for assistant messages.
///
/// Handles fenced code blocks as monospaced cards and renders everything else
/// through SwiftUI's native AttributedString markdown (bold, italics, inline
/// code, links). Deliberately not a full CommonMark implementation.
struct MarkdownText: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                switch segment {
                case .prose(let prose):
                    Text(attributed(prose))
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                case .code(let code, _):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(code)
                            .font(Theme.mono(12))
                            .foregroundStyle(Theme.textSecondary)
                            .padding(12)
                            .textSelection(.enabled)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.surfaceElevated)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    private enum Segment {
        case prose(String)
        case code(String, language: String?)
    }

    private var segments: [Segment] {
        var result: [Segment] = []
        var prose: [String] = []
        var code: [String] = []
        var language: String?
        var inCode = false

        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("```") {
                if inCode {
                    result.append(.code(code.joined(separator: "\n"), language: language))
                    code = []
                    inCode = false
                } else {
                    let joined = prose.joined(separator: "\n")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !joined.isEmpty {
                        result.append(.prose(joined))
                    }
                    prose = []
                    language = line.dropFirst(3).isEmpty ? nil : String(line.dropFirst(3))
                    inCode = true
                }
            } else if inCode {
                code.append(String(line))
            } else {
                prose.append(String(line))
            }
        }
        if inCode {
            result.append(.code(code.joined(separator: "\n"), language: language))
        } else {
            let joined = prose.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty {
                result.append(.prose(joined))
            }
        }
        return result
    }

    private func attributed(_ string: String) -> AttributedString {
        (try? AttributedString(
            markdown: string,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(string)
    }
}
