'use client'

import { useEffect, useRef, useState } from 'react'
import { api, chat as streamChat, type AgentCard, type ChatEvent, type MapAction } from '@/lib/api'

type Turn = {
  role: 'you' | 'atlas'
  text: string
  /** Tool calls made while answering, in order — the receipts for the answer. */
  tools?: { name: string; args: Record<string, any> }[]
  error?: boolean
}

type Props = {
  /** Applied to the live map as the agent works. */
  onAction: (action: MapAction) => void
  onClose: () => void
}

/** Openers that show what the thing is for without a wall of instructions. */
const STARTERS = [
  'Where is the most housing being proposed right now?',
  'Show me rental buildings near Yonge & Eglinton',
  'Which ward has the most short-term rentals?',
  'Put the city’s fire station locations on the map',
]

export default function Chat({ onAction, onClose }: Props) {
  const [card, setCard] = useState<AgentCard | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState<string | undefined>()
  const abort = useRef<AbortController | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.agent().then(setCard).catch(() => setCard(null))
  }, [])

  // Follow the newest text as it streams in.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [turns, busy])

  useEffect(() => () => abort.current?.abort(), [])

  async function send(question: string) {
    const q = question.trim()
    if (!q || busy) return
    setInput('')
    setTurns((t) => [...t, { role: 'you', text: q }, { role: 'atlas', text: '', tools: [] }])
    setBusy(true)
    abort.current = new AbortController()

    // The reply is built in place: the last turn is always the one in flight.
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)))

    const onEvent = (e: ChatEvent) => {
      switch (e.type) {
        case 'start':
          if (e.session) setSession(e.session)
          break
        case 'tool':
          patch((t) => ({ ...t, tools: [...(t.tools ?? []), { name: e.name, args: e.args }] }))
          break
        case 'text':
          patch((t) => ({ ...t, text: t.text ? `${t.text}\n\n${e.text}` : e.text }))
          break
        case 'map':
          onAction(e.action)
          break
        case 'done':
          if (e.session) setSession(e.session)
          if (e.answer) patch((t) => ({ ...t, text: e.answer }))
          break
        case 'error':
          patch((t) => ({ ...t, text: e.error, error: true }))
          break
      }
    }

    try {
      await streamChat({ message: q, session }, onEvent, abort.current.signal)
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        patch((t) => ({ ...t, text: String(err?.message ?? err), error: true }))
      }
    } finally {
      setBusy(false)
      abort.current = null
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start gap-2 border-b border-line px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-[12.5px] font-semibold leading-tight text-ink">Ask the atlas</h2>
          <p className="truncate text-[10px] leading-tight text-muted">
            {card
              ? card.ready
                ? `${card.tool_count} tools · answers from the data, and moves the map`
                : 'Agent not configured'
              : 'Checking…'}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close chat"
                className="rounded-ctl p-1 text-muted hover:bg-fill-hover hover:text-ink">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {card && !card.ready && (
          <p className="rounded-ctl bg-inset px-3 py-2 text-[11.5px] leading-relaxed text-ink-2">
            {card.hint ?? 'No Anthropic auth configured on this host.'}
          </p>
        )}

        {!turns.length && (
          <div className="space-y-2">
            <p className="text-[11.5px] leading-relaxed text-ink-2">
              Ask for what you want to see. It answers with figures from the
              city’s own datasets, turns the layers on, and can pull in anything
              else the open-data portal publishes.
            </p>
            <div className="flex flex-col gap-1.5 pt-1">
              {STARTERS.map((s) => (
                <button key={s} onClick={() => send(s)} disabled={!card?.ready}
                        className="rounded-ctl bg-inset px-2.5 py-1.5 text-left text-[11.5px] leading-snug text-ink-2 hover:bg-fill-hover hover:text-ink disabled:opacity-50">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <Bubble key={i} turn={t} thinking={busy && i === turns.length - 1} />
        ))}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(input) }}
        className="flex items-end gap-1.5 border-t border-line p-2.5"
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline, as in every chat box.
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
          }}
          rows={2}
          placeholder={card?.ready ? 'Ask about Toronto…' : 'Agent unavailable'}
          disabled={!card?.ready}
          className="max-h-28 flex-1 resize-none rounded-ctl bg-inset px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}
                  className="rounded-ctl bg-fill-strong px-2.5 py-2 text-[11px] text-ink hover:bg-fill-hover">
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim() || !card?.ready}
                  aria-label="Send"
                  className="rounded-ctl bg-accent px-2.5 py-2 text-accent-ink disabled:opacity-40">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M2.5 8h10M8.5 3.5 13 8l-4.5 4.5" stroke="currentColor"
                    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </form>
    </div>
  )
}

function Bubble({ turn, thinking }: { turn: Turn; thinking: boolean }) {
  if (turn.role === 'you') {
    return (
      <p className="ml-6 rounded-ctl bg-fill-strong px-2.5 py-1.5 text-[12px] leading-snug text-ink">
        {turn.text}
      </p>
    )
  }
  return (
    <div className="mr-2 space-y-1.5">
      {!!turn.tools?.length && (
        <div className="flex flex-wrap gap-1">
          {turn.tools.map((t, i) => <ToolChip key={i} name={t.name} args={t.args} />)}
        </div>
      )}
      {turn.text && (
        <div className={`whitespace-pre-wrap text-[12px] leading-relaxed ${
          turn.error ? 'text-bad' : 'text-ink-2'}`}>
          {renderMarkdownish(turn.text)}
        </div>
      )}
      {thinking && !turn.text && (
        <p className="flex items-center gap-1.5 text-[11.5px] text-muted">
          <span className="h-2.5 w-2.5 animate-spin rounded-full border border-line-strong border-t-transparent" />
          Reading the data…
        </p>
      )}
    </div>
  )
}

/** The receipt for a claim: which tool ran, against what. */
function ToolChip({ name, args }: { name: string; args: Record<string, any> }) {
  const label = name.replace(/^tdot_/, '').replace(/_/g, ' ')
  const subject = args?.layer ?? args?.place ?? args?.package ?? args?.query ??
    (Array.isArray(args?.layers) ? args.layers.join(', ') : undefined)
  return (
    <span className="rounded-full bg-inset px-2 py-0.5 text-[10px] text-muted">
      {label}
      {subject ? <span className="text-ink-2"> · {String(subject).slice(0, 40)}</span> : null}
    </span>
  )
}

/**
 * The agent answers in light markdown; this renders the only mark it actually
 * uses — `**bold**` around the figures — and leaves everything else as text.
 * A full markdown dependency would be more than this panel needs.
 */
function renderMarkdownish(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i} className="font-semibold text-ink">{part.slice(2, -2)}</strong>
      : <span key={i}>{part}</span>)
}
