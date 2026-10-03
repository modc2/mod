'use client'

import { useEffect, useRef, useState } from 'react'
import { chatStream } from '@/lib/api'
import type { Directive } from '@/lib/scene'
import { Coin, QuestionBlock } from './Sprites'

type ToolCall = { name: string; input: Record<string, any> }
type Message =
  | { role: 'user'; text: string }
  | { role: 'agent'; text: string; tools: ToolCall[]; error?: string; shown?: Directive[] }

type Props = {
  open: boolean
  onClose: () => void
  /** Apply a map / infographic directive the agent issued. */
  onDisplay?: (d: Directive) => void
  /** What the map shows right now, sent along with each question. */
  mapState?: () => Record<string, any>
}

// Openers that show off the range: curated housing stats, a map layer, and
// the portal-wide SoQL path. ASCII + upper case for Press Start 2P.
const STARTERS = [
  'Map rat complaints per resident by ZIP this year',
  'Show me only Brooklyn neighborhoods under $800K',
  'Where are prices rising fastest?',
  'Which neighborhoods have the most traffic injuries?',
  'What do people complain to 311 about the most?',
  'Which subway stations are busiest this year?',
]

/**
 * ASK NYC — a chat with an agent that answers from the same open-data tools
 * this module serves over MCP. Every consulted tool is shown as a chip above
 * the answer, so a number can always be traced to its dataset.
 */
export default function ChatPanel({ open, onClose, onDisplay, mapState }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState<string | undefined>()
  const scroller = useRef<HTMLDivElement>(null)

  // Follow the stream: new text keeps arriving at the bottom.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [messages, busy])

  const ask = async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    setMessages((ms) => [...ms, { role: 'user', text: q }, { role: 'agent', text: '', tools: [] }])

    const patch = (fn: (last: Extract<Message, { role: 'agent' }>) => Message) =>
      setMessages((ms) => {
        const last = ms[ms.length - 1]
        if (last?.role !== 'agent') return ms
        return [...ms.slice(0, -1), fn(last)]
      })

    try {
      for await (const ev of chatStream(q, session, mapState?.())) {
        if (ev.type === 'session') setSession(ev.id)
        else if (ev.type === 'tool') patch((l) => ({ ...l, tools: [...l.tools, ev] }))
        else if (ev.type === 'text')
          patch((l) => ({ ...l, text: l.text ? `${l.text}\n\n${ev.text}` : ev.text }))
        else if (ev.type === 'done' && ev.session_id) setSession(ev.session_id)
        else if (ev.type === 'error') patch((l) => ({ ...l, error: ev.error }))
        else if (ev.type === 'display') {
          onDisplay?.(ev.directive)
          patch((l) => ({ ...l, shown: [...(l.shown ?? []), ev.directive] }))
        }
      }
    } catch (e: any) {
      patch((l) => ({ ...l, error: String(e?.message ?? e).slice(0, 200) }))
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    setMessages([])
    setSession(undefined)
  }

  if (!open) return null

  return (
    // A conversation needs room: full-screen sheet on a phone, a wide drawer
    // on desktop — above the inspector, which it would otherwise fight for
    // the right edge.
    <aside className="blk sheet-in pointer-events-auto absolute inset-0 z-50 flex flex-col overflow-hidden
                      md:inset-auto md:bottom-3 md:right-3 md:top-[86px] md:w-[400px]">
      <header className="safe-t relative flex shrink-0 items-center gap-2.5 border-b-[3px] border-black bg-black/40 py-2.5 pl-4 pr-2.5">
        <span className="brick brick-strip absolute inset-y-0 left-0 w-2.5" aria-hidden />
        <QuestionBlock size={18} />
        <div className="min-w-0 flex-1">
          <h2 className="pixel text-[10px] leading-none text-white">ASK NYC</h2>
          <p className="pixel mt-1.5 text-[6.5px] leading-none text-nes-coin">
            AGENT x OPEN DATA
          </p>
        </div>
        {messages.length > 0 && (
          <button onClick={reset} disabled={busy}
                  className="btn pixel tap px-2 py-2 text-[7px] disabled:opacity-40">
            NEW
          </button>
        )}
        <button onClick={onClose} aria-label="Close chat"
                className="tap -m-1 grid shrink-0 place-items-center p-1 text-nes-ink3 hover:text-nes-red">
          <svg width="14" height="14" viewBox="0 0 14 14" shapeRendering="crispEdges"
               fill="currentColor" aria-hidden>
            <rect x="2" y="2" width="2" height="2" /><rect x="4" y="4" width="2" height="2" />
            <rect x="6" y="6" width="2" height="2" /><rect x="8" y="4" width="2" height="2" />
            <rect x="10" y="2" width="2" height="2" /><rect x="8" y="8" width="2" height="2" />
            <rect x="10" y="10" width="2" height="2" /><rect x="4" y="8" width="2" height="2" />
            <rect x="2" y="10" width="2" height="2" />
          </svg>
        </button>
      </header>

      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-[12.5px] leading-relaxed text-nes-ink2">
              Ask anything about New York — housing prices, transit, crashes,
              311, schools, budgets. The agent answers from the city&apos;s own
              open data and shows which dataset it checked.
            </p>
            <div className="space-y-2">
              {STARTERS.map((s) => (
                <button key={s} onClick={() => ask(s)}
                        className="btn tap block w-full px-3 py-2.5 text-left text-[12px] leading-snug text-nes-ink2">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] border-2 border-black bg-nes-raised px-3 py-2 text-[12.5px] leading-relaxed text-white">
                {msg.text}
              </div>
            </div>
          ) : (
            <div key={i} className="space-y-1.5">
              {msg.tools.filter((t) => t.name !== 'nyc_map' && t.name !== 'nyc_infographic').map((t, j) => (
                <div key={j}
                     className="pixel inline-flex items-center gap-1.5 border-2 border-black bg-black/40 px-2 py-1.5 text-[6.5px] text-nes-coin"
                     title={JSON.stringify(t.input)}>
                  <span aria-hidden>&gt;</span>
                  <span>{t.name.toUpperCase()}</span>
                </div>
              ))}
              {msg.shown?.map((d, j) => (
                <button key={`d${j}`} onClick={() => onDisplay?.(d)}
                        title="Show this again"
                        className="btn tap flex w-full items-center gap-2 px-2.5 py-2 text-left">
                  <span className="pixel shrink-0 text-[6.5px] text-nes-coin">
                    {d.kind === 'map' ? 'MAP' : 'CARD'}
                  </span>
                  <span className="min-w-0 truncate text-[11.5px] text-nes-ink2">{describe(d)}</span>
                </button>
              ))}
              {msg.text && (
                <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-nes-ink2">
                  {msg.text}
                </div>
              )}
              {msg.error && (
                <div className="border-2 border-black bg-black/40 px-3 py-2 text-[11px] leading-snug text-nes-red">
                  {msg.error}
                </div>
              )}
              {i === messages.length - 1 && busy && !msg.text && (
                <p className="pixel flex items-center gap-2 text-[7px] text-nes-ink3">
                  <span className="coin-spin inline-block"><Coin size={12} /></span>
                  {msg.tools.length ? 'CHECKING THE DATA...' : 'THINKING...'}
                </p>
              )}
            </div>
          ),
        )}
      </div>

      <form
        className="safe-b flex shrink-0 items-center gap-2 border-t-[3px] border-black bg-black/40 px-3 py-2.5"
        onSubmit={(e) => { e.preventDefault(); ask(input) }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about NYC..."
          maxLength={4000}
          className="min-w-0 flex-1 border-2 border-black bg-nes-panel px-3 py-2.5 text-[13px] text-white
                     placeholder:text-nes-ink3 focus:outline-none focus:ring-2 focus:ring-nes-coin"
        />
        <button type="submit" disabled={busy || !input.trim()}
                className="btn pixel tap shrink-0 px-3 py-3 text-[8px] disabled:opacity-40">
          {busy ? '...' : 'ASK'}
        </button>
      </form>
    </aside>
  )
}

/** One line saying what a directive did, for its chip in the transcript. */
function describe(d: Directive): string {
  if (d.kind === 'infographic') return d.title
  const parts: string[] = []
  if (d.reset) parts.push('reset')
  if (d.overlay) parts.push(d.overlay.title)
  if (d.overlay === null) parts.push('overlay cleared')
  if (d.housing) parts.push(Object.values(d.housing).filter(Boolean).join(' '))
  if (d.layers) parts.push(`layers: ${d.layers.join(', ')}`)
  if (d.add?.length) parts.push(`+${d.add.join(', +')}`)
  if (d.remove?.length) parts.push(`-${d.remove.join(', -')}`)
  if (d.filter) parts.push(`range ${d.filter.min ?? ''} to ${d.filter.max ?? ''}`)
  if (d.highlight) parts.push(d.highlight.length ? `outline ${d.highlight.join(', ')}` : 'outline cleared')
  if (d.only) parts.push(d.only.length ? `only ${d.only.join(', ')}` : 'all areas')
  if (d.focus) parts.push(`fly to ${d.focus.label || 'point'}`)
  if (d.basemap) parts.push(`${d.basemap} basemap`)
  return d.caption || parts.join(' / ') || 'map updated'
}
