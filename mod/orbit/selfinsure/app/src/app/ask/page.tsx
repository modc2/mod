'use client'

import Link from 'next/link'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Note } from '@/components/chrome'
import { DraftCard, OwnerKeyCard } from '@/components/pool'
import { post, useResource, Guide, RunResult } from '@/lib/api'

// The guide runs on this node (src/agent.py). With no model connected it
// answers from its own rules — explanations, live pools, drafts — for free.

interface Turn { role: 'user' | 'assistant'; text: string; run?: RunResult }

const ROUTE_LABEL: Record<string, string> = {
  '/': 'Overview', '/pools': 'Pools', '/contract': 'On chain', '/preset': 'Templates', '/create': 'Create a pool',
}

export default function AskPage() {
  const guide = useResource<Guide>('/guide')
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [turns, busy])

  const ask = async (q: string) => {
    q = q.trim()
    if (!q || busy) return
    setText('')
    setError(null)
    const history = turns.slice(-8).map((t) => ({ role: t.role, content: t.text }))
    setTurns((ts) => [...ts, { role: 'user', text: q }])
    setBusy(true)
    try {
      const run = await post<RunResult>('/run', { query: q, history })
      setTurns((ts) => [...ts, { role: 'assistant', text: run.result, run }])
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => { e.preventDefault(); ask(text) }
  const last = [...turns].reverse().find((t) => t.run)?.run
  const chips = last?.suggestions?.length ? last.suggestions : guide.data?.examples || []
  const llm = guide.data?.brains?.llm

  return (
    <>
      <div className="hero">
        <h1>Ask the guide</h1>
        <p>
          Ask what anything means, look at the live pools, or describe the pool you want in
          one sentence and the guide drafts it — with a check of whether it could actually
          pay. Nothing is created until you press Create.
        </p>
      </div>

      <div className="chat">
        {turns.length === 0 && (
          <div className="bubble bubble-agent">
            Hi — I am the selfinsure guide. I run on this server, with no outside service.
            Pick a question below or type your own.
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === 'user' ? 'bubble bubble-user' : 'bubble bubble-agent'}>
            {t.role === 'assistant' && t.run?.draft ? (
              <>
                <p>Here is a draft — nothing has been created yet.</p>
                <DraftCard draft={t.run.draft} />
              </>
            ) : t.role === 'assistant' && t.run?.created ? (
              <OwnerKeyCard created={t.run.created} />
            ) : (
              <div className="bubble-text">{t.text}</div>
            )}
            {t.run?.links && t.run.links.length > 0 && !t.run.draft && (
              <div className="row">
                {Array.from(new Set(t.run.links)).map((l) => (
                  <Link key={l} href={l} className="btn btn-ghost">Open {ROUTE_LABEL[l] || l}</Link>
                ))}
              </div>
            )}
            {t.run?.fallback && <div className="sub">{t.run.fallback}</div>}
          </div>
        ))}
        {busy && <div className="bubble bubble-agent bubble-busy">Thinking…</div>}
        {error && <Note tone="error">{error}</Note>}
        <div ref={end} />
      </div>

      <div className="chips">
        {chips.map((c) => (
          <button key={c} className="chip" onClick={() => ask(c)} disabled={busy}>{c}</button>
        ))}
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "a pool for 12 neighbours covering burst pipes, $15 a month, up to $1,000"'
          aria-label="Ask the guide"
        />
        <button className="btn" type="submit" disabled={busy || !text.trim()}>Ask</button>
      </form>
      <p className="sub" style={{ marginTop: 8 }}>
        {llm?.ready
          ? `Connected model: ${llm.model}${llm.local ? ' (running locally)' : ''}.`
          : 'Running on built-in rules — free and private. The node owner can connect a local model for open-ended questions.'}
      </p>
    </>
  )
}
