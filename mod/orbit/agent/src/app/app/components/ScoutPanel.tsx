'use client'

// ScoutPanel — watch an agent idea get found on the internet.
//
// POST /agents/scout/stream runs five phases and this renders each one as it
// lands, so the process is the show, not a spinner:
//
//   lens    the theme being scouted (yours, or a random one)
//   search  Hacker News · GitHub · arXiv · web — every hit a numbered signal
//   read    the best pages opened, robots.txt honored
//   ideate  the idea-scout agent pitching one agent, live, citing signals
//   vibe    the vibe-builder turning that pitch into a whole agent
//
// The finished draft is handed to onDraft (the editor fills its form with
// it); nothing is saved here.

import { useCallback, useRef, useState } from 'react'
import { API_URL } from '../config'

export type ScoutDraft = {
  name?: string; icon?: string; description?: string; goal?: string
  tools?: string[] | null; model?: string | null
}
type Signal = { n: number; source: string; title: string; url: string; meta?: string; snippet?: string }
type Read = { n: number; url: string; status: string; note?: string; preview?: string; done: boolean }
type Idea = {
  title: string; pitch: string; why_now: string; brief: string; novelty: string
  inspired_by: { n: number; source: string; title: string; url: string }[]
}

const PHASES = ['lens', 'search', 'read', 'ideate', 'vibe'] as const
const SRC: Record<string, { label: string; cls: string }> = {
  hn: { label: 'HN', cls: 'text-orange-300 border-orange-400/30' },
  github: { label: 'GH', cls: 'text-gray-200 border-gray-400/30' },
  arxiv: { label: 'arXiv', cls: 'text-rose-300 border-rose-400/30' },
  web: { label: 'web', cls: 'text-sky-300 border-sky-400/30' },
}

export default function ScoutPanel({ token, theme, engine, onDraft, onIdea }: {
  token?: string
  /** optional theme — blank means the scout picks a lens at random */
  theme: string
  /** '' = this module's loop, else a harness name */
  engine?: string
  onDraft: (d: ScoutDraft, dropped: string[]) => void
  onIdea?: (i: Idea) => void
}) {
  const [running, setRunning] = useState(false)
  const [phase, setPhase] = useState<string | null>(null)
  const [lens, setLens] = useState<{ theme: string; picked: boolean } | null>(null)
  const [sources, setSources] = useState<Record<string, string>>({})
  const [signals, setSignals] = useState<Signal[]>([])
  const [reads, setReads] = useState<Read[]>([])
  const [thought, setThought] = useState('')
  const [steps, setSteps] = useState<{ phase: string; tool: string }[]>([])
  const [idea, setIdea] = useState<Idea | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [open, setOpen] = useState(true)
  const abortRef = useRef<AbortController | null>(null)

  const reset = () => {
    setPhase(null); setLens(null); setSources({}); setSignals([]); setReads([])
    setThought(''); setSteps([]); setIdea(null); setError(null); setElapsed(null)
  }

  const onEvent = useCallback((ev: any) => {
    switch (ev.type) {
      case 'phase':
        setPhase(ev.phase)
        if (ev.phase === 'lens') setLens({ theme: ev.theme, picked: ev.picked })
        break
      case 'source_start': setSources(s => ({ ...s, [ev.source]: 'searching' })); break
      case 'source_done': setSources(s => ({ ...s, [ev.source]: `${ev.count}` })); break
      case 'source_error': setSources(s => ({ ...s, [ev.source]: 'down' })); break
      case 'signal': setSignals(s => [...s, ev.signal]); break
      case 'read_start': setReads(r => [...r, { n: ev.n, url: ev.url, status: 'reading', done: false }]); break
      case 'read_done':
        setReads(r => r.map(x => x.n === ev.n ? { ...x, status: ev.status, note: ev.note, preview: ev.preview, done: true } : x))
        break
      case 'token': setThought(t => (t + (ev.text || '')).slice(-1600)); break
      case 'step': setSteps(s => [...s, { phase: ev.phase, tool: ev.step?.tool || '?' }]); break
      case 'idea': setIdea(ev.idea); onIdea?.(ev.idea); break
      case 'vibe_error': setError(`vibe: ${ev.error}`); break
      case 'done':
        setElapsed(ev.run?.elapsed ?? null)
        if (ev.run?.error) setError(ev.run.error)
        if (ev.run?.draft) onDraft(ev.run.draft, ev.run.tools_dropped || [])
        setPhase('done')
        break
      case 'error': setError(ev.error); break
    }
  }, [onDraft, onIdea])

  const start = useCallback(async () => {
    if (!token) { setError('sign in to scout for an agent'); return }
    reset(); setOpen(true); setRunning(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const res = await fetch(`${API_URL}/agents/scout/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: theme.trim() || null, harness: engine || null, key: token }),
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error('the scout could not be started')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n')
        buf = frames.pop() || ''
        for (const f of frames) {
          const line = f.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          try { onEvent(JSON.parse(line.slice(6))) } catch { /* partial frame */ }
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') setError(e?.message || String(e))
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }, [token, theme, engine, onEvent])

  const stop = () => abortRef.current?.abort()
  const cited = new Set((idea?.inspired_by || []).map(r => r.n))
  const at = phase ? PHASES.indexOf(phase as any) : -1
  const started = running || phase !== null

  return (
    <div className="mt-2">
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] text-gray-600 min-w-0">
          no idea? it searches the web and builds one — the box above is the theme (blank = surprise me)
        </span>
        <button onClick={running ? stop : start}
          className="ml-auto shrink-0 text-[10px] uppercase tracking-wider px-2 py-1 rounded border border-cyan-400/30 text-cyan-300 hover:bg-cyan-500/10 transition">
          {running ? '■ stop' : '◎ scout the web'}
        </button>
      </div>

      {started && (
        <div className="mt-2 border border-cyan-400/20 rounded-md bg-cyan-500/[0.03] text-[10px]">
          {/* phase rail */}
          <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-1 px-2 py-1.5 border-b border-cyan-400/10">
            {PHASES.map((p, i) => {
              const done = phase === 'done' || i < at
              const live = i === at && running
              return (
                <span key={p} className={`px-1.5 py-0.5 rounded uppercase tracking-wider ${
                  live ? 'bg-cyan-500/20 text-cyan-200 animate-pulse'
                  : done ? 'text-cyan-400' : 'text-gray-600'}`}>
                  {done ? '✓ ' : live ? '▸ ' : ''}{p}
                </span>
              )
            })}
            <span className="ml-auto text-gray-600">{elapsed != null ? `${elapsed}s` : ''} {open ? '▾' : '▸'}</span>
          </button>

          {open && (
            <div className="p-2 space-y-2 max-h-[28rem] overflow-y-auto">
              {lens && (
                <div className="text-gray-400">
                  lens <span className="text-cyan-200">{lens.theme}</span>
                  {lens.picked && <span className="text-gray-600"> · picked at random</span>}
                </div>
              )}

              {Object.keys(sources).length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {Object.entries(sources).map(([s, st]) => (
                    <span key={s} className={`px-1.5 py-0.5 rounded border ${SRC[s]?.cls || ''} ${st === 'down' ? 'opacity-40 line-through' : ''}`}>
                      {SRC[s]?.label || s} {st === 'searching' ? '…' : st === 'down' ? '' : `· ${st}`}
                    </span>
                  ))}
                </div>
              )}

              {signals.length > 0 && (
                <div className="space-y-0.5">
                  {signals.map(s => (
                    <div key={s.n} className={`flex items-baseline gap-1.5 ${cited.has(s.n) ? 'bg-cyan-500/10 rounded px-1 -mx-1' : ''}`}>
                      <span className="text-gray-600 w-5 shrink-0 text-right">[{s.n}]</span>
                      <span className={`shrink-0 px-1 rounded border text-[9px] ${SRC[s.source]?.cls || ''}`}>{SRC[s.source]?.label || s.source}</span>
                      <a href={s.url} target="_blank" rel="noreferrer" className="truncate text-gray-300 hover:text-cyan-200" title={s.snippet || s.title}>{s.title}</a>
                      {s.meta && <span className="shrink-0 text-gray-600">{s.meta}</span>}
                      {cited.has(s.n) && <span className="shrink-0 text-cyan-300">cited</span>}
                    </div>
                  ))}
                </div>
              )}

              {reads.length > 0 && (
                <div className="space-y-0.5 border-t border-cyan-400/10 pt-1.5">
                  {reads.map(r => (
                    <div key={r.n} title={r.preview || r.note || ''} className="flex items-baseline gap-1.5">
                      <span className={r.status === 'ok' ? 'text-emerald-400' : r.done ? 'text-amber-400' : 'text-cyan-300 animate-pulse'}>
                        {r.status === 'ok' ? '✓' : r.done ? '–' : '▸'}
                      </span>
                      <span className="text-gray-500">read [{r.n}]</span>
                      <span className="truncate text-gray-400">{r.preview || r.url}</span>
                      {r.note && <span className="shrink-0 text-amber-400/70">{r.note}</span>}
                    </div>
                  ))}
                </div>
              )}

              {(thought || steps.length > 0) && !idea && (
                <div className="border-t border-cyan-400/10 pt-1.5">
                  <div className="text-gray-600 mb-0.5">idea-scout thinking{steps.length ? ` · ${steps.map(s => s.tool).join(' → ')}` : ''}</div>
                  {thought && <pre className="whitespace-pre-wrap text-gray-500 max-h-24 overflow-y-auto font-mono text-[9px]">{thought}</pre>}
                </div>
              )}

              {idea && (
                <div className="border border-violet-400/30 rounded p-2 bg-violet-500/[0.06] space-y-1">
                  <div className="text-violet-200 text-[11px] font-semibold">✧ {idea.title}</div>
                  <div className="text-gray-300">{idea.pitch}</div>
                  {idea.why_now && <div className="text-gray-500"><span className="text-gray-600">why now · </span>{idea.why_now}</div>}
                  {idea.novelty && <div className="text-gray-500"><span className="text-gray-600">new because · </span>{idea.novelty}</div>}
                  {idea.inspired_by.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {idea.inspired_by.map(r => (
                        <a key={r.n} href={r.url} target="_blank" rel="noreferrer"
                          className="px-1 rounded border border-cyan-400/30 text-cyan-300 hover:bg-cyan-500/10 truncate max-w-[14rem]">
                          [{r.n}] {r.title}
                        </a>
                      ))}
                    </div>
                  )}
                  {phase === 'vibe' && running && <div className="text-violet-300 animate-pulse">vibe-builder drafting the agent…</div>}
                  {phase === 'done' && !error && <div className="text-emerald-400">built — opening your new agent</div>}
                </div>
              )}

              {error && <div className="text-red-400">{error}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
