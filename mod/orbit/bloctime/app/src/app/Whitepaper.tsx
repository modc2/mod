"use client";

import { Fragment, useEffect, useRef, useState } from 'react'
import { ArrowPathIcon } from '@heroicons/react/24/outline'

// The protocol paper, read in the sidebar. The text is whitepaper.md at the
// module root, served by GET /whitepaper — one file, edited in one place,
// live without a rebuild. The renderer below covers exactly the markdown the
// paper uses (headings, paragraphs, lists, fences, **bold**, `code`) so the
// console carries no markdown dependency.

type Block =
  | { kind: 'h'; level: number; text: string; id: string }
  | { kind: 'p'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'code'; text: string }

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export function parseMarkdown(md: string): Block[] {
  const out: Block[] = []
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++])
      i++
      out.push({ kind: 'code', text: body.join('\n') })
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      out.push({ kind: 'h', level: h[1].length, text: h[2], id: slug(h[2]) })
      i++
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ''))
      out.push({ kind: 'ul', items })
      continue
    }
    if (!line.trim()) { i++; continue }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*]\s)/.test(lines[i])) para.push(lines[i++])
    out.push({ kind: 'p', text: para.join(' ') })
  }
  return out
}

function Inline({ text }: { text: string }) {
  // `code` first, then **bold** inside the remaining runs.
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((run, i) =>
        run.startsWith('`') && run.endsWith('`') && run.length > 1
          ? <code key={i} className="font-mono text-[0.92em] px-1 py-px rounded bg-field text-accent">{run.slice(1, -1)}</code>
          : <Fragment key={i}>{run.split(/(\*\*[^*]+\*\*)/g).map((b, j) =>
              b.startsWith('**') && b.endsWith('**') && b.length > 4
                ? <strong key={j} className="text-ink font-semibold">{b.slice(2, -2)}</strong>
                : <Fragment key={j}>{b}</Fragment>)}</Fragment>
      )}
    </>
  )
}

export default function Whitepaper({ load }: { load: () => Promise<{ markdown: string; updated?: number }> }) {
  const [blocks, setBlocks] = useState<Block[] | null>(null)
  const [error, setError] = useState('')
  const body = useRef<HTMLDivElement>(null)

  const fetchPaper = () => {
    setError('')
    load()
      .then(r => setBlocks(parseMarkdown(r.markdown || '')))
      .catch(e => setError(e?.message || 'Could not load the whitepaper'))
  }
  useEffect(fetchPaper, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div className="px-4 py-6 space-y-3">
        <p className="text-xs text-down">{error}</p>
        <button onClick={fetchPaper} className="btn btn-sm">Retry</button>
      </div>
    )
  }
  if (!blocks) {
    return (
      <div className="px-4 py-6 flex items-center gap-2 text-xs text-faint">
        <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Loading whitepaper...
      </div>
    )
  }

  const sections = blocks.filter((b): b is Extract<Block, { kind: 'h' }> => b.kind === 'h' && b.level === 2)
  const jump = (id: string) =>
    body.current?.querySelector(`#wp-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <div ref={body} className="px-5 py-4 text-[12.5px] leading-relaxed text-ink2">
      {/* Contents — the paper is short, but the sidebar is narrow. */}
      {sections.length > 2 && (
        <nav className="mb-5 border border-hair rounded-lg bg-panel px-3 py-2.5">
          <p className="lbl-dim mb-1.5">Contents</p>
          <ol className="space-y-0.5">
            {sections.map(s => (
              <li key={s.id}>
                <button onClick={() => jump(s.id)} className="text-left text-[11px] text-mute hover:text-accent transition-colors">
                  {s.text}
                </button>
              </li>
            ))}
          </ol>
        </nav>
      )}
      {blocks.map((b, i) => {
        if (b.kind === 'h') {
          if (b.level === 1) return <h1 key={i} id={`wp-${b.id}`} className="text-xl font-bold text-ink mb-1">{b.text}</h1>
          if (b.level === 2) return <h2 key={i} id={`wp-${b.id}`} className="scroll-mt-3 text-sm font-bold text-ink mt-6 mb-2 pb-1 border-b border-hair">{b.text}</h2>
          return <h3 key={i} id={`wp-${b.id}`} className="scroll-mt-3 text-[12px] font-bold uppercase tracking-wider text-accent mt-4 mb-1.5">{b.text}</h3>
        }
        if (b.kind === 'ul') {
          return (
            <ul key={i} className="my-2 space-y-1 pl-4 list-disc marker:text-faint">
              {b.items.map((it, j) => <li key={j}><Inline text={it} /></li>)}
            </ul>
          )
        }
        if (b.kind === 'code') {
          return <pre key={i} className="my-2 px-3 py-2 rounded-lg bg-field border border-hair font-mono text-[11px] text-ink overflow-x-auto whitespace-pre">{b.text}</pre>
        }
        return <p key={i} className="my-2"><Inline text={b.text} /></p>
      })}
    </div>
  )
}
