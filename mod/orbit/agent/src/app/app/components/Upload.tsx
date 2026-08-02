'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { API_URL } from '../config'
import type { LibKind } from './Library'

// ── bring your own ──────────────────────────────────────────────────
// One box for every collection: drop a file (or paste one, or hand it a
// shared CID) and it lands in the library as a prompt, a skill, a memory
// note or a whole agent. The server decides which from the file's `type:`,
// its name, then its shape — the chips below override that. The format
// reference is docs/uploads.md, served by GET /library/formats so the panel
// and the repo can never drift apart.

type Kind = LibKind | 'auto'

const KINDS: Kind[] = ['auto', 'agent', 'prompt', 'skill', 'memory']

const DOT: Record<LibKind, string> = {
  prompt: 'bg-amber-400', skill: 'bg-sky-400',
  memory: 'bg-emerald-400', agent: 'bg-violet-400',
}

type Landed = { file: string; kind?: LibKind; name?: string; error?: string }

type Props = {
  // the signed token — the server files what you upload under your address
  token?: string | null
  // something landed in the library: refetch
  onDone?: () => void
  // an agent landed: the console may want to select it
  onAgent?: (name: string) => void
  // rail-sized rendering: tighter, docs collapse instead of sitting open
  compact?: boolean
  onSignIn?: () => void
}

export default function Upload({ token, onDone, onAgent, compact = false, onSignIn }: Props) {
  const [kind, setKind] = useState<Kind>('auto')
  const [busy, setBusy] = useState(false)
  const [landed, setLanded] = useState<Landed[]>([])
  const [dragging, setDragging] = useState(false)
  const [cid, setCid] = useState('')
  const [pasting, setPasting] = useState(false)
  const [text, setText] = useState('')
  const [showDocs, setShowDocs] = useState(!compact)
  const fileRef = useRef<HTMLInputElement>(null)

  const post = async (route: string, body: Record<string, unknown>) => {
    const res = await fetch(`${API_URL}/${route}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, key: token }),
    })
    return res.json()
  }

  const record = (rows: Landed[]) => {
    setLanded(rows)
    if (rows.some(r => !r.error)) onDone?.()
    const agent = rows.find(r => r.kind === 'agent' && r.name && !r.error)
    if (agent) onAgent?.(agent.name!)
  }

  const upload = useCallback(async (files: File[]) => {
    if (!files.length || busy) return
    setBusy(true)
    const rows: Landed[] = []
    for (const f of files) {
      try {
        const data = await post('library/upload', {
          text: await f.text(), filename: f.name,
          kind: kind === 'auto' ? null : kind,
        })
        rows.push(data.error
          ? { file: f.name, error: data.error }
          : { file: f.name, kind: data.kind, name: data.name })
      } catch (e: any) {
        rows.push({ file: f.name, error: e?.message || 'upload failed' })
      }
    }
    record(rows)
    setBusy(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, token, busy])

  const uploadText = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      const data = await post('library/upload', {
        text, filename: null, kind: kind === 'auto' ? null : kind,
      })
      record([data.error
        ? { file: 'pasted', error: data.error }
        : { file: 'pasted', kind: data.kind, name: data.name }])
      if (!data.error) setText('')
    } catch (e: any) {
      record([{ file: 'pasted', error: e?.message || 'upload failed' }])
    }
    setBusy(false)
  }

  const importCid = async () => {
    const c = cid.trim()
    if (!c || busy) return
    setBusy(true)
    try {
      const data = await post('library/import', { cid: c })
      record([data.error
        ? { file: c.slice(0, 12) + '…', error: data.error }
        : { file: c.slice(0, 12) + '…', kind: data.kind, name: data.item?.name }])
      if (!data.error) setCid('')
    } catch (e: any) {
      record([{ file: 'cid', error: e?.message || 'import failed' }])
    }
    setBusy(false)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    upload(Array.from(e.dataTransfer.files || []))
  }

  const pad = compact ? 'px-2.5 py-2' : 'px-5 py-4'
  const label = compact ? 'text-[10px]' : 'text-[11px]'

  return (
    <div className={compact ? 'space-y-2' : 'space-y-4'}>
      {/* what it should land as */}
      <div className="flex flex-wrap items-center gap-1">
        <span className={`${label} text-gray-600 uppercase tracking-wider mr-1`}>as</span>
        {KINDS.map(k => (
          <button key={k} onClick={() => setKind(k)}
            title={k === 'auto'
              ? 'Let the file say what it is — its type:, its name, then its shape'
              : `Treat every file in this batch as a ${k}`}
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] border transition ${
              kind === k ? 'border-white/25 text-gray-100 bg-white/[0.07]'
                         : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
            }`}>
            {k !== 'auto' && <span className={`w-1 h-1 rounded-full ${DOT[k as LibKind]}`} />}
            {k}
          </button>
        ))}
      </div>

      {/* the drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        className={`rounded-xl border border-dashed text-center cursor-pointer transition ${pad} ${
          dragging ? 'border-emerald-400/50 bg-emerald-500/[0.07]'
                   : 'border-white/[0.12] hover:border-white/25 bg-white/[0.02]'
        }`}>
        <input ref={fileRef} type="file" multiple hidden
          accept=".md,.markdown,.txt,.json,.yaml,.yml"
          onChange={e => { upload(Array.from(e.target.files || [])); e.target.value = '' }} />
        <div className={`${compact ? 'text-[11px]' : 'text-sm'} text-gray-300`}>
          {busy ? 'uploading…' : 'Drop files or click to pick'}
        </div>
        <div className={`${label} text-gray-600 mt-0.5`}>
          .md with front matter or .json — agents, prompts, skills, notes
        </div>
      </div>

      {/* paste one instead */}
      {pasting ? (
        <div className="space-y-1.5">
          <textarea value={text} onChange={e => setText(e.target.value)}
            rows={compact ? 4 : 8} spellCheck={false}
            placeholder={'---\ntype: agent\nname: release-captain\ndescription: cuts releases\n---\nYou cut releases for this repo.'}
            className="w-full bg-white/[0.03] border border-white/[0.08] rounded-lg px-2.5 py-2 text-[11px] font-mono text-gray-300 placeholder-gray-700 outline-none focus:border-emerald-500/40 transition resize-none leading-relaxed" />
          <div className="flex items-center gap-1.5">
            <button onClick={uploadText} disabled={!text.trim() || busy}
              className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition">
              upload
            </button>
            <button onClick={() => { setPasting(false); setText('') }}
              className="px-2 py-1 rounded-md text-[10px] text-gray-600 hover:text-gray-300 transition">
              cancel
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setPasting(true)}
          className={`${label} text-gray-600 hover:text-gray-300 transition`}>
          …or paste one in
        </button>
      )}

      {/* install from a shared CID — any kind, the bundle says which */}
      <div className="flex items-center gap-1.5">
        <input value={cid} onChange={e => setCid(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') importCid() }}
          placeholder="Qm… install from a shared CID"
          className="flex-1 min-w-0 bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] font-mono text-gray-300 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition" />
        <button onClick={importCid} disabled={!cid.trim() || busy}
          className="px-2.5 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.1] text-gray-400 hover:text-gray-200 hover:border-white/25 disabled:opacity-40 transition">
          install
        </button>
      </div>

      {/* what landed */}
      {landed.length > 0 && (
        <div className="space-y-1">
          {landed.map((r, i) => (
            <div key={i} className={`flex items-center gap-2 ${label} rounded-md px-2 py-1 border ${
              r.error ? 'border-red-500/20 bg-red-500/[0.06]' : 'border-emerald-500/20 bg-emerald-500/[0.06]'
            }`}>
              {r.kind && <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT[r.kind]}`} />}
              <span className="font-mono text-gray-500 truncate max-w-[40%]">{r.file}</span>
              {r.error ? (
                <span className="text-red-300/90 truncate">{r.error}</span>
              ) : (
                <span className="text-emerald-300/90 truncate">→ {r.kind} · {r.name}</span>
              )}
            </div>
          ))}
          {landed.some(r => (r.error || '').includes('sign in')) && onSignIn && (
            <button onClick={onSignIn}
              className={`${label} text-sky-300 hover:text-sky-200 transition`}>
              sign in and try again →
            </button>
          )}
        </div>
      )}

      {/* the format reference, straight from docs/uploads.md */}
      <div className="border-t border-white/[0.06] pt-2">
        <button onClick={() => setShowDocs(v => !v)}
          className={`${label} text-gray-500 hover:text-gray-300 transition flex items-center gap-1`}>
          <span className={`transition-transform ${showDocs ? 'rotate-90' : ''}`}>›</span>
          how to write one
        </button>
        {showDocs && <Docs compact={compact} />}
      </div>
    </div>
  )
}

// ── docs/uploads.md, rendered ───────────────────────────────────────

function Docs({ compact }: { compact: boolean }) {
  const [doc, setDoc] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/library/formats`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setDoc(d.doc || ''))
      .catch(() => setErr('format docs unreachable'))
  }, [])

  if (err) return <p className="text-[10px] text-gray-600 mt-2">{err}</p>
  if (doc === null) return <p className="text-[10px] text-gray-600 mt-2">loading…</p>
  return (
    <div className={`mt-2 overflow-y-auto pr-1 ${compact ? 'max-h-64' : 'max-h-[52vh]'}`}>
      <Markdown text={doc} />
    </div>
  )
}

// what ends a paragraph: a heading, fence, table row, rule or list item
const STRUCTURAL = /^(?:#{1,3} |```|\||---|\s*[-*] |\s*\d+\. )/

// A deliberately small markdown renderer — headings, fences, tables, lists
// and inline code are everything docs/uploads.md uses.
function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = []
  const lines = text.split('\n')
  let i = 0
  let key = 0

  const inline = (s: string): React.ReactNode[] =>
    s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, j) =>
      part.startsWith('`') && part.endsWith('`') ? (
        <code key={j} className="font-mono text-[11px] text-emerald-300/90 bg-white/[0.05] rounded px-1 py-px">
          {part.slice(1, -1)}
        </code>
      ) : part.startsWith('**') && part.endsWith('**') ? (
        <strong key={j} className="text-gray-200 font-medium">{part.slice(2, -2)}</strong>
      ) : <span key={j}>{part}</span>)

  const cells = (row: string) => row.split('|').slice(1, -1).map(c => c.trim())

  while (i < lines.length) {
    const line = lines[i]

    if (line.startsWith('```')) {
      const code: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++])
      i++
      blocks.push(
        <pre key={key++} className="my-2 p-2.5 rounded-lg bg-black/40 border border-white/[0.06] overflow-x-auto">
          <code className="text-[11px] font-mono text-gray-300 leading-relaxed whitespace-pre">{code.join('\n')}</code>
        </pre>)
      continue
    }

    if (/^#{1,3} /.test(line)) {
      const level = line.match(/^#+/)![0].length
      const body = line.replace(/^#+ /, '')
      blocks.push(
        <div key={key++} className={
          level === 1 ? 'text-sm font-semibold text-gray-100 mt-3 mb-1'
            : level === 2 ? 'text-[13px] font-medium text-gray-200 mt-3 mb-1'
            : 'text-[11px] font-medium text-gray-300 mt-2 mb-1'
        }>{inline(body)}</div>)
      i++
      continue
    }

    if (line.startsWith('|') && lines[i + 1]?.startsWith('|')) {
      const head = cells(line)
      i += 2   // header + the |---| rule
      const rows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) rows.push(cells(lines[i++]))
      blocks.push(
        <table key={key++} className="my-2 w-full text-[11px] border border-white/[0.07] rounded-lg overflow-hidden">
          <thead><tr className="bg-white/[0.04]">
            {head.map((h, j) => <th key={j} className="text-left px-2 py-1 text-gray-400 font-medium">{inline(h)}</th>)}
          </tr></thead>
          <tbody>{rows.map((r, j) => (
            <tr key={j} className="border-t border-white/[0.05]">
              {r.map((c, k) => <td key={k} className="px-2 py-1 text-gray-500 align-top">{inline(c)}</td>)}
            </tr>))}
          </tbody>
        </table>)
      continue
    }

    if (/^\s*[-*] /.test(line) || /^\s*\d+\. /.test(line)) {
      const items: string[] = []
      while (i < lines.length && (/^\s*[-*] /.test(lines[i]) || /^\s*\d+\. /.test(lines[i]))) {
        items.push(lines[i++].replace(/^\s*(?:[-*]|\d+\.) /, ''))
      }
      blocks.push(
        <ul key={key++} className="my-1.5 space-y-0.5">
          {items.map((it, j) => (
            <li key={j} className="text-[11px] text-gray-500 leading-relaxed flex gap-1.5">
              <span className="text-gray-700">·</span><span>{inline(it)}</span>
            </li>))}
        </ul>)
      continue
    }

    if (line.startsWith('---')) { blocks.push(<div key={key++} className="my-3 border-t border-white/[0.06]" />); i++; continue }

    if (!line.trim()) { i++; continue }

    // always consume the current line first: a paragraph that opens with a
    // structural character (`inline code`, say) must still make progress
    const para: string[] = [lines[i++]]
    while (i < lines.length && lines[i].trim() && !STRUCTURAL.test(lines[i])) para.push(lines[i++])
    blocks.push(
      <p key={key++} className="text-[11px] text-gray-500 leading-relaxed my-1.5">{inline(para.join(' '))}</p>)
  }

  return <div>{blocks}</div>
}
