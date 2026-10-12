"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { ChatBubbleLeftRightIcon, XMarkIcon, PaperAirplaneIcon, ArrowPathIcon, TrashIcon } from '@heroicons/react/24/outline'
import { Inline } from './Whitepaper'

// The guide in the bottom-right corner. POST /ask streams SSE from a
// tool-less Claude run grounded in whitepaper.md (api/agent.py) — it explains,
// it never signs. The console hands over what it already has on screen as
// `context` so "how much do I have?" style questions get real numbers.

type Msg = { role: 'user' | 'guide'; text: string; error?: boolean }

const STORE_KEY = 'bloctime_agent_chat'
const KEEP = 30
const FALLBACK_STARTERS = [
  'What is BlocTime, in plain English?',
  'If I lock $100 for a month, what do I get?',
  'How does the weekly pot pay out?',
  'Can I get my money out early?',
]

function load(): Msg[] {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]').slice(-KEEP) } catch { return [] }
}

// Paragraphs and "- " lists; inline **bold** / `code` via the paper's renderer.
function Answer({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/)
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split('\n')
        if (lines.every(l => /^\s*([-*]|\d+\.)\s+/.test(l) || !l.trim())) {
          return (
            <ul key={i} className="my-1.5 space-y-0.5 pl-4 list-disc marker:text-faint">
              {lines.filter(l => l.trim()).map((l, j) => <li key={j}><Inline text={l.replace(/^\s*([-*]|\d+\.)\s+/, '')} /></li>)}
            </ul>
          )
        }
        return <p key={i} className="my-1.5">{lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}<Inline text={l} /></Fragment>)}</p>
      })}
    </>
  )
}

export default function Agent({ apiUrl, getContext, seed }: {
  apiUrl: string
  getContext: () => Record<string, any>
  // Bumping `n` opens the panel; a non-empty `q` is asked straight away.
  seed: { q: string; n: number }
}) {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>(load)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [starters, setStarters] = useState<string[]>(FALLBACK_STARTERS)
  const [offline, setOffline] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(msgs.slice(-KEEP))) } catch {}
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [msgs])

  useEffect(() => {
    if (!open) return
    field.current?.focus()
    fetch(`${apiUrl}/ask/status`).then(r => r.json()).then(s => {
      if (Array.isArray(s.starters) && s.starters.length) setStarters(s.starters)
      setOffline(s.ready ? null : (s.hint || 'The guide is offline right now.'))
    }).catch(() => {})
  }, [open, apiUrl])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const ask = useCallback(async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    const history = msgs.filter(m => !m.error).map(m => ({ role: m.role, text: m.text }))
    setMsgs(m => [...m, { role: 'user', text: q }, { role: 'guide', text: '' }])
    setInput('')
    setBusy(true)
    const patch = (fn: (m: Msg) => Msg) =>
      setMsgs(all => all.map((m, i) => i === all.length - 1 ? fn(m) : m))
    const ctrl = new AbortController()
    abort.current = ctrl
    try {
      let context: Record<string, any> | undefined
      try { context = getContext() } catch {}
      const res = await fetch(`${apiUrl}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, history, context }),
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `The guide answered ${res.status}`)
      }
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let cut
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, cut).replace(/^data:\s*/, '')
          buf = buf.slice(cut + 2)
          let ev: any
          try { ev = JSON.parse(line) } catch { continue }
          if (ev.type === 'delta') patch(m => ({ ...m, text: m.text + ev.text }))
          else if (ev.type === 'done') patch(m => ({ ...m, text: ev.text }))
          else if (ev.type === 'error') patch(m => ({ ...m, text: ev.error, error: true }))
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') patch(m => ({ ...m, text: e?.message || 'Could not reach the guide.', error: true }))
    } finally {
      patch(m => m.text ? m : { ...m, text: 'No answer came back.', error: true })
      setBusy(false)
      abort.current = null
    }
  }, [apiUrl, busy, getContext, msgs])

  // Intro page buttons open the panel, optionally with a question.
  useEffect(() => {
    if (!seed.n) return
    setOpen(true)
    if (seed.q) ask(seed.q)
  }, [seed.n]) // eslint-disable-line react-hooks/exhaustive-deps

  const clear = () => { abort.current?.abort(); setMsgs([]) }

  return (
    <>
      {open && (
        <div
          role="dialog"
          aria-label="Ask the BlocTime guide"
          className="fixed z-40 bottom-20 right-3 md:right-5 w-[min(390px,calc(100vw-1.5rem))] h-[min(580px,calc(100vh-7rem))]
                     card flex flex-col overflow-hidden animate-fade-up"
        >
          <div className="card-head">
            <ChatBubbleLeftRightIcon className="w-4 h-4 text-accent" />
            <span className="lbl flex-1">Ask the guide</span>
            {msgs.length > 0 && (
              <button onClick={clear} className="text-faint hover:text-ink" title="Clear conversation"><TrashIcon className="w-4 h-4" /></button>
            )}
            <button onClick={() => setOpen(false)} className="text-faint hover:text-ink" title="Close (Esc)"><XMarkIcon className="w-4 h-4" /></button>
          </div>

          <div ref={scroller} className="flex-1 overflow-y-auto px-3.5 py-3 space-y-3 text-[12.5px] leading-relaxed">
            {msgs.length === 0 && (
              <div className="space-y-3">
                <p className="text-ink2">
                  Hi — I explain BlocTime: what it is, how staking and the Friday pot work, and what the risks are.
                  I can't move funds or sign anything.
                </p>
                <div className="flex flex-col items-start gap-1.5">
                  {starters.map(s => (
                    <button key={s} onClick={() => ask(s)} disabled={busy || !!offline}
                            className="chip text-left hover:border-accent/50 hover:text-accent disabled:opacity-50">{s}</button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              m.role === 'user'
                ? <div key={i} className="flex justify-end"><p className="max-w-[85%] rounded-lg rounded-br-sm bg-accent/15 border border-accent/30 px-3 py-2 text-ink whitespace-pre-wrap">{m.text}</p></div>
                : <div key={i} className={`max-w-[95%] text-ink2 ${m.error ? 'text-down' : ''}`}>
                    {m.text
                      ? <Answer text={m.text} />
                      : <span className="inline-flex items-center gap-2 text-faint"><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" />thinking...</span>}
                  </div>
            ))}
          </div>

          {offline && <p className="px-3.5 pb-2 text-[11px] text-down">{offline}</p>}

          <form
            onSubmit={e => { e.preventDefault(); ask(input) }}
            className="border-t border-hair p-2.5 flex items-end gap-2"
          >
            <textarea
              ref={field}
              rows={1}
              value={input}
              maxLength={1500}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input) } }}
              placeholder="Ask anything about BlocTime..."
              className="input resize-none max-h-28 py-2 text-[13px]"
            />
            <button type="submit" disabled={busy || !input.trim()} className="btn btn-accent btn-sm h-[38px]" title="Send">
              {busy ? <ArrowPathIcon className="w-4 h-4 animate-spin" /> : <PaperAirplaneIcon className="w-4 h-4" />}
            </button>
          </form>
        </div>
      )}

      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        title={open ? 'Close the guide' : 'Ask the guide'}
        className="fixed z-40 bottom-4 right-3 md:right-5 w-12 h-12 rounded-full flex items-center justify-center
                   border border-accent/50 bg-panel text-accent shadow-lg hover:bg-accent/15 transition-colors"
        style={{ boxShadow: 'var(--shadow)' }}
      >
        {open ? <XMarkIcon className="w-5 h-5" /> : <ChatBubbleLeftRightIcon className="w-5 h-5" />}
      </button>
    </>
  )
}
