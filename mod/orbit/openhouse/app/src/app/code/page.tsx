/* /code — read the contract.

   Four source files, tens of thousands of characters. On the old page they
   were fetched on every visit whether or not anyone scrolled that far;
   here they load for the people who came to verify. */

"use client";

import dynamic from 'next/dynamic'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { API_URL, SourceFile, useResource } from '../../lib/api'
import { findLines, highlightLines, splitOnQuery, TOKEN_CLASS } from '../../lib/highlight'
import { LAUNCH } from '../../lib/whitepaper'

const LANG_LABEL: Record<string, string> = { solidity: 'Solidity', python: 'Python', typescript: 'TypeScript' }

/** One row of code: syntax spans, with the find-in-file query marked on top
 *  of them so a match never costs you the colour underneath. */
function CodeLine({ tokens, query }: { tokens: { kind: string; text: string }[]; query: string }) {
  if (!tokens.length) return <>{' '}</>
  return (
    <>
      {tokens.map((tok, i) => {
        const cls = TOKEN_CLASS[tok.kind as keyof typeof TOKEN_CLASS] || TOKEN_CLASS.plain
        if (!query) return <span key={i} className={cls}>{tok.text}</span>
        return (
          <span key={i} className={cls}>
            {splitOnQuery(tok.text, query).map((part, j) =>
              part.hit
                ? <mark key={j} className="bg-sun/60 text-ink rounded-[2px]">{part.text}</mark>
                : <span key={j}>{part.text}</span>
            )}
          </span>
        )
      })}
    </>
  )
}

function CodeViewer({ files, loading, error, onRetry }: {
  files: SourceFile[]; loading: boolean; error: string | null; onRetry: () => void
}) {
  const [active, setActive] = useState(0)
  const [query, setQuery] = useState('')
  const [hitAt, setHitAt] = useState(0)
  const bodyRef = useRef<HTMLDivElement>(null)

  const f: SourceFile | undefined = files[Math.min(active, Math.max(files.length - 1, 0))]

  // 63 KB of mod.py gets tokenized once per file, not once per keystroke.
  const highlighted = useMemo(
    () => (f ? highlightLines(f.content, f.language) : []),
    [f?.name, f?.content, f?.language]
  )
  const rawLines = useMemo(
    () => (f ? f.content.replace(/\n$/, '').split('\n') : []),
    [f?.name, f?.content]
  )
  const hits = useMemo(() => findLines(rawLines, query.trim()), [rawLines, query])

  // Switching files or editing the query restarts the walk through matches.
  useEffect(() => { setHitAt(0) }, [active, query])

  const jump = (next: number) => {
    if (!hits.length) return
    const at = (next + hits.length) % hits.length
    setHitAt(at)
    const row = bodyRef.current?.querySelector<HTMLElement>(`[data-line="${hits[at]}"]`)
    row?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  if (!files.length) {
    // Three honest outcomes, never a spinner that means all of them.
    if (loading) {
      return <div className="glass rounded-3xl py-20 text-center text-white/55 text-sm uppercase tracking-widest">Loading source…</div>
    }
    return (
      <div className="glass rounded-3xl py-14 px-7 text-center">
        <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-3">Source unavailable</div>
        <p className="text-white/70 text-sm max-w-lg mx-auto leading-relaxed">
          The API that serves these files didn&apos;t answer{error ? ` — ${error}` : ''}. The code
          is still on disk and in the repo; this page just couldn&apos;t reach it.
        </p>
        <button onClick={onRetry}
          className="mt-5 text-[11px] font-bold uppercase tracking-widest text-ink bg-coral rounded-full px-5 py-2.5 hover:opacity-90 transition-opacity">
          Try again
        </button>
      </div>
    )
  }

  const trimmed = query.trim()
  return (
    <div className="rounded-2xl overflow-hidden border border-white/15 bg-paper shadow-2xl shadow-black/20">
      {/* tab strip */}
      <div className="flex items-stretch overflow-x-auto border-b border-white/15 bg-white/[0.04]">
        {files.map((file, i) => (
          <button key={file.name} onClick={() => setActive(i)}
            className={`px-4 py-3 text-xs font-mono whitespace-nowrap border-r border-white/10 transition-colors ${i === active ? 'bg-coral/10 text-coral font-bold' : 'text-white/60 hover:text-white/90 hover:bg-white/[0.04]'}`}>
            {file.name.split('/').pop()}
          </button>
        ))}
      </div>
      {/* chrome bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 border-b border-white/10 bg-white/[0.025]">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-2.5 h-2.5 rounded-full bg-peach" /><span className="w-2.5 h-2.5 rounded-full bg-sun" /><span className="w-2.5 h-2.5 rounded-full bg-emerald-300" />
          <span className="ml-3 text-[11px] font-mono text-white/75 truncate">{f.name}</span>
          <span className="text-[10px] uppercase tracking-widest text-coral border border-coral/25 rounded px-1.5 py-0.5 shrink-0">{LANG_LABEL[f.language] || f.language}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-[10px] text-white/60 hidden sm:inline tabular-nums">{f.lines} lines · {(f.bytes / 1024).toFixed(1)} KB</span>
          <a href={`${API_URL}/source`} target="_blank" rel="noreferrer"
            className="text-[10px] font-bold uppercase tracking-widest text-white/60 hover:text-coral transition-colors">Raw</a>
          <button onClick={() => { navigator.clipboard.writeText(f.content); toast.success(`${f.name} copied`) }}
            className="text-[10px] font-bold uppercase tracking-widest text-white/60 hover:text-coral transition-colors">Copy</button>
        </div>
      </div>
      {/* find in file — 1,478 lines of mod.py is not a thing you skim */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-white/10 bg-white/[0.015]">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); jump(e.shiftKey ? hitAt - 1 : hitAt + 1) } }}
          placeholder={`Find in ${f.name.split('/').pop()}…`}
          className="flex-1 min-w-[10rem] max-w-xs bg-white/[0.06] border border-white/15 rounded-lg px-3 py-1.5 text-[12px] font-mono text-white/85 placeholder:text-white/40 focus:outline-none focus:border-coral/50"
        />
        {trimmed && (
          <>
            <span className="text-[11px] text-white/60 tabular-nums">
              {hits.length ? `${hitAt + 1} / ${hits.length} ${hits.length === 1 ? 'line' : 'lines'}` : 'no matches'}
            </span>
            {hits.length > 0 && (
              <div className="flex items-center gap-1">
                <button onClick={() => jump(hitAt - 1)} aria-label="Previous match"
                  className="w-6 h-6 rounded border border-white/15 text-white/65 hover:text-coral hover:border-coral/40 text-[11px] leading-none transition-colors">&uarr;</button>
                <button onClick={() => jump(hitAt + 1)} aria-label="Next match"
                  className="w-6 h-6 rounded border border-white/15 text-white/65 hover:text-coral hover:border-coral/40 text-[11px] leading-none transition-colors">&darr;</button>
              </div>
            )}
            <button onClick={() => setQuery('')}
              className="text-[10px] font-bold uppercase tracking-widest text-white/50 hover:text-coral transition-colors">Clear</button>
          </>
        )}
      </div>
      <p className="px-4 py-2.5 text-xs text-white/65 border-b border-white/10 bg-white/[0.01]">{f.description}</p>
      {/* code body */}
      <div ref={bodyRef} className="overflow-auto max-h-[42rem] text-[12.5px] leading-[1.55] font-mono">
        <table className="w-full border-collapse">
          <tbody>
            {highlighted.map((tokens, i) => {
              const isCurrent = hits.length > 0 && hits[hitAt] === i
              return (
                <tr key={i} data-line={i} className={isCurrent ? 'bg-coral/10' : 'hover:bg-white/[0.04]'}>
                  <td className="select-none text-right pr-4 pl-4 text-white/45 w-12 align-top tabular-nums sticky left-0 bg-paper">{i + 1}</td>
                  <td className="pr-6 whitespace-pre align-top"><CodeLine tokens={tokens} query={trimmed} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CodeInner() {
  const { data: sources, loading, error, reload } = useResource<SourceFile[]>('source', [])

  return (
    <Shell>
      <PageHead kicker="Open Source · No Black Box" title="Read the contract.">
        The whole thing is right here — the Solidity that holds your shares, the logic that splits
        the rent, the API that serves it, and the MCP server that lets an agent drive it.
        No trust required. Verify.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        <Reveal>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
            {[
              { k: 'Contract', v: 'OpenHouse.sol' },
              { k: 'Network', v: `${LAUNCH.chain} · ${LAUNCH.chainId}` },
              { k: 'Stage', v: `${LAUNCH.stage} · launch TBA` },
              { k: 'License', v: 'MIT' },
            ].map((s, i) => (
              <div key={i} className="glass rounded-xl p-4">
                <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-1">{s.k}</div>
                <div className="font-mono text-sm text-coral truncate">{s.v}</div>
              </div>
            ))}
          </div>
        </Reveal>
        <Reveal delay={60}>
          <CodeViewer
            files={Array.isArray(sources) ? sources : []}
            loading={loading}
            error={error}
            onRetry={() => reload()}
          />
        </Reveal>

        <Reveal delay={100}>
          <div className="glass rounded-3xl p-7 md:p-8 mt-8">
            <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-3">Drive it yourself</div>
            <p className="text-white/72 text-sm leading-relaxed max-w-2xl">
              Everything on this site is a REST call, and every REST call is also an MCP tool.
              Point an agent at <code className="text-coral/90 bg-coral/5 border border-coral/15 rounded px-1.5 py-0.5 text-[12px] font-mono">POST /openhouse/api/mcp</code> and
              it can read the live deal, quote a payment split, check a renter's equity or pull
              the cap table — the same numbers this page shows, no auth needed.
            </p>
            <div className="grid sm:grid-cols-2 gap-3 mt-5">
              {[
                { k: 'Transport', v: 'JSON-RPC 2.0 · Streamable HTTP' },
                { k: 'Tools', v: '17 — 13 read, 4 write' },
              ].map(s => (
                <div key={s.k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4">
                  <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-1">{s.k}</div>
                  <div className="font-mono text-[13px] text-white/80">{s.v}</div>
                </div>
              ))}
            </div>
          </div>
        </Reveal>
      </div>

      <NextUp here="/code" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(CodeInner), { ssr: false })
