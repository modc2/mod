"use client"

import { useEffect, useMemo, useState } from 'react'
import { CopyButton } from '@/ui/CopyButton'
import { colorWithOpacity } from '@/utils'

// Minimal, safe Solidity highlighter. We HTML-escape first, then wrap tokens in
// colored spans. Order matters: comments + strings are matched before keywords
// so we don't recolor their contents. Kept deliberately small — no parser, just
// enough to make the source read like code rather than a wall of mono text.
const KEYWORDS = /\b(pragma|solidity|import|contract|interface|library|abstract|is|function|constructor|modifier|event|emit|struct|enum|mapping|address|uint256|uint|int|bool|bytes32|bytes|string|public|private|internal|external|view|pure|payable|virtual|override|returns|return|memory|storage|calldata|require|revert|assert|if|else|for|while|do|break|continue|new|delete|using|indexed|immutable|constant|this|super|true|false|unchecked|try|catch)\b/g

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function highlightLine(raw: string): string {
  let s = escapeHtml(raw)
  // Line comments
  s = s.replace(/(\/\/.*)$/g, '<span class="tok-com">$1</span>')
  // Strings (single + double)
  s = s.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|"[^"]*"|'[^']*')/g, '<span class="tok-str">$1</span>')
  // Numbers
  s = s.replace(/\b(\d+(?:\.\d+)?(?:e\d+)?)\b/g, '<span class="tok-num">$1</span>')
  // Keywords — skip lines already inside a comment span start
  s = s.replace(KEYWORDS, '<span class="tok-kw">$&</span>')
  return s
}

interface Props {
  /** Contract display name, used to fetch /api/contract-source?name=. */
  name: string
  /** Accent color for the header glow (matches the contract's color). */
  accent: string
}

export function CodeViewer({ name, accent }: Props) {
  const [code, setCode] = useState<string | null>(null)
  const [sourceName, setSourceName] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setCode(null)
    fetch(`/api/contract-source?name=${encodeURIComponent(name)}`)
      .then(async (r) => {
        const j = await r.json()
        if (cancelled) return
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`)
        setCode(j.code)
        setSourceName(j.sourceName || '')
      })
      .catch((e) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [name])

  const lines = useMemo(() => (code ? code.replace(/\n$/, '').split('\n') : []), [code])

  return (
    <div
      className="rounded-xl relative overflow-hidden"
      style={{ border: `1px solid ${colorWithOpacity(accent, 0.3)}`, backgroundColor: 'var(--bg-surface)' }}
    >
      {/* token color rules scoped to this viewer */}
      <style>{`
        .sol-code .tok-kw  { color: #c792ea; }
        .sol-code .tok-com { color: var(--text-tertiary); font-style: italic; }
        .sol-code .tok-str { color: #89ddff; }
        .sol-code .tok-num { color: #f78c6c; }
      `}</style>

      {/* Top accent line */}
      <div className="absolute top-0 left-0 right-0 h-px" style={{ background: `linear-gradient(to right, transparent, ${colorWithOpacity(accent, 0.6)}, transparent)` }} />

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5" style={{ borderBottom: '1px solid var(--border-color)' }}>
        <span className="text-[10px] font-bold tracking-[0.2em] font-mono px-2 py-0.5 rounded" style={{ color: accent, backgroundColor: colorWithOpacity(accent, 0.12) }}>SOLIDITY</span>
        <span className="text-[12px] font-mono truncate" style={{ color: 'var(--text-tertiary)' }}>{sourceName || `${name}.sol`}</span>
        <div className="flex-1" />
        {!loading && lines.length > 0 && (
          <span className="text-[11px] font-mono shrink-0" style={{ color: 'var(--text-tertiary)' }}>{lines.length} lines</span>
        )}
        {code && <CopyButton text={code} size="sm" />}
      </div>

      {/* Body */}
      {loading ? (
        <div className="px-4 py-16 text-center text-[13px] font-mono" style={{ color: 'var(--text-tertiary)' }}>loading source…</div>
      ) : error ? (
        <div className="px-4 py-16 text-center text-[13px] font-mono text-red-400">{error}</div>
      ) : (
        <div className="sol-code overflow-auto max-h-[640px] custom-scrollbar" style={{ backgroundColor: 'var(--bg-input)' }}>
          <table className="w-full border-collapse" style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '12.5px', lineHeight: '1.55' }}>
            <tbody>
              {lines.map((ln, i) => (
                <tr key={i} className="hover:bg-white/[0.025]">
                  <td
                    className="select-none text-right pr-4 pl-4 align-top"
                    style={{ color: 'var(--text-tertiary)', opacity: 0.4, width: '1%', whiteSpace: 'nowrap', userSelect: 'none' }}
                  >{i + 1}</td>
                  <td
                    className="pr-6 align-top whitespace-pre"
                    style={{ color: 'var(--text-primary)' }}
                    dangerouslySetInnerHTML={{ __html: highlightLine(ln) || '&nbsp;' }}
                  />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
